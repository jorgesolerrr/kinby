import asyncio
from pathlib import Path

import pytest

from kinby.contracts import (
    FACTORY_CHECK,
    FACTORY_EDIT,
    FACTORY_GET,
    FACTORY_LIST,
    ErrorCode,
    ErrorEnvelope,
    FactoryCheckCommand,
    FactoryCheckResult,
    FactoryEditCommand,
    FactoryGetCommand,
    FactoryListCommand,
    FactoryListResult,
    FactoryResult,
    FactorySource,
    FactorySummary,
)
from kinby.hub import Hub
from tests.test_hub import FakeImages, FakeRuntime, hub_client

FACTORY = """\
name: tickets
instances:
  coder: { image: coder }
intake: { instance: coder, routine: scan }
work_item: { issue: int, repo: str }
steps:
  - id: implement
    kind: client
    in: coder
    client: claude
    prompt: prompts/implement.md
    hook: record_branch
    results: { branch: str }
    timeout: 60m
  - id: review
    kind: agent
    in: coder
    prompt: prompts/review.md
    hook: read_verdict
    requires: [branch]
    outcomes: { clean: next, changes: { back: implement, max: 3 } }
  - id: checks
    kind: command
    in: coder
    run: ["uv run pytest"]
    requires: [branch]
  - id: open-pr
    kind: code
    in: coder
    call: open_pull_request
    requires: [branch, repo]
    results: { pr: int }
    retry: 0
  - id: babysit
    kind: wait
    signal:
      - routine: github
        headers.X-GitHub-Event: pull_request_review
        body.pull_request.number: "{{pr}}"
    deadline: 7d
  - id: merge
    kind: approve
    summary: Merge the pull request.
done_requires: [pr]
"""
TOOLS = '''\
from kinby.plugins.tools import tool


@tool(write=True)
def open_pull_request() -> str:
    """Open the pull request."""
    return "opened"
'''
HOOKS = """\
from kinby.plugins.hooks import hook


@hook
def record_branch() -> None:
    pass


@hook
def read_verdict() -> None:
    pass
"""
FILES = {
    "factory.yaml": FACTORY,
    "prompts/implement.md": "Implement issue {{issue}}.\n",
    "prompts/review.md": "Review the branch.\n",
    "instances/coder/routines/scan/ROUTINE.md": "---\ndescription: Scan\n---\nScan.\n",
    "instances/coder/tools/github.py": TOOLS,
    "instances/coder/hooks/record.py": HOOKS,
}


def write_factory(directory: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def factory_hub(tmp_path: Path, *, shipped: dict[str, dict[str, str]] | None = None) -> Hub:
    shipped_directory = tmp_path / "shipped"
    shipped_directory.mkdir(parents=True)
    for name, files in (shipped or {}).items():
        write_factory(shipped_directory / name, files)
    return Hub(
        tmp_path / "hub",
        runtime=FakeRuntime(),
        images=FakeImages(),
        shipped_factories=shipped_directory,
    )


def problems(tmp_path: Path, files: dict[str, str]) -> list[str]:
    """What the factory check finds in a hub factory made of *files*."""
    hub = factory_hub(tmp_path)
    write_factory(tmp_path / "hub" / "factories" / "tickets", files)
    client = hub_client(hub)

    async def check() -> list[str]:
        result = await client.call(FACTORY_CHECK, FactoryCheckCommand(name="tickets"))
        assert isinstance(result, FactoryCheckResult)
        return result.problems

    return asyncio.run(check())


def renamed(files: dict[str, str], name: str) -> dict[str, str]:
    return files | {"factory.yaml": files["factory.yaml"].replace("name: tickets", f"name: {name}")}


def test_list_and_get_serve_the_hubs_factories_and_the_shipped_ones(tmp_path):
    hub = factory_hub(tmp_path, shipped={"software": renamed(FILES, "software")})
    write_factory(tmp_path / "hub" / "factories" / "tickets", FILES)
    client = hub_client(hub)

    async def scenario() -> None:
        listed = await client.call(FACTORY_LIST, FactoryListCommand())
        got = await client.call(FACTORY_GET, FactoryGetCommand(name="tickets"))
        missing = await client.call(FACTORY_GET, FactoryGetCommand(name="absent"))

        assert isinstance(listed, FactoryListResult)
        assert listed.factories == [
            FactorySummary(name="software", source=FactorySource.SHIPPED),
            FactorySummary(name="tickets", source=FactorySource.HUB),
        ]
        assert isinstance(got, FactoryResult)
        assert got.source is FactorySource.HUB
        assert got.files == FILES
        assert isinstance(missing, ErrorEnvelope)
        assert missing.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_the_check_passes_a_factory_whose_names_all_resolve(tmp_path):
    assert problems(tmp_path, FILES) == []


def test_the_check_reports_every_name_that_does_not_resolve(tmp_path):
    factory = (
        FACTORY.replace("coder: { image: coder }", "coder: { image: rust }\n  reviewer: {}")
        .replace("routine: scan", "routine: sweep")
        .replace("prompts/review.md", "prompts/missing.md")
        .replace("hook: record_branch", "hook: record_commits")
        .replace("call: open_pull_request", "call: merge_pull_request")
    )

    found = problems(tmp_path, FILES | {"factory.yaml": factory})

    assert found == [
        'Instance "coder" names image recipe "rust", which kinby does not ship.',
        'Instance "reviewer" has no template: instances/reviewer/ is not a folder.',
        'The intake routine "sweep" is not in instance "coder".',
        'Step "implement" names hook "record_commits", which instance "coder" does not have.',
        'Step "review" names prompt "prompts/missing.md", which is not a file in the factory.',
        'Step "open-pr" calls tool "merge_pull_request", which instance "coder" does not have.',
    ]


def test_a_step_may_name_one_of_kinbys_default_hooks_and_a_command_step_a_hook(tmp_path):
    factory = FACTORY.replace("hook: read_verdict", "hook: find_pull_request").replace(
        '    run: ["uv run pytest"]\n', '    run: ["uv run pytest"]\n    hook: record_branch\n'
    )
    misnamed = factory.replace(
        "hook: record_branch\n    requires", "hook: record_check\n    requires"
    )

    assert problems(tmp_path / "default", FILES | {"factory.yaml": factory}) == []
    assert problems(tmp_path / "misnamed", FILES | {"factory.yaml": misnamed}) == [
        'Step "checks" names hook "record_check", which instance "coder" does not have.'
    ]


def test_needs_human_calls_a_tool_of_one_of_the_factorys_instances(tmp_path):
    def calling(instance: str, tool: str) -> dict[str, str]:
        needs_human = f"needs_human: {{ in: {instance}, call: {tool} }}\n"
        return FILES | {"factory.yaml": FACTORY + needs_human}

    assert problems(tmp_path / "declared", calling("coder", "open_pull_request")) == []
    assert problems(tmp_path / "missing", calling("coder", "label_issue")) == [
        'needs_human calls tool "label_issue", which instance "coder" does not have.'
    ]
    assert problems(tmp_path / "undeclared", calling("triager", "open_pull_request")) == [
        'needs_human runs in instance "triager", which the factory does not declare.'
    ]


def test_the_check_reports_a_tool_or_hook_file_that_does_not_load(tmp_path):
    broken = {
        "instances/coder/tools/github.py": "raise RuntimeError('no token')\n",
        "instances/coder/hooks/record.py": "import not_installed\n",
    }

    found = problems(tmp_path, FILES | broken)

    assert found[:2] == [
        "instances/coder/tools/github.py: RuntimeError: no token",
        "instances/coder/hooks/record.py: ModuleNotFoundError: No module named 'not_installed'",
    ]
    assert (
        'Step "open-pr" calls tool "open_pull_request", which instance "coder" does not have.'
        in found
    )


def test_the_check_fails_setup_and_login_declarations_an_install_would_fail_on(tmp_path):
    declarations = """\
  coder:
    image: coder
    setup_fields:
      - { name: TOKEN, label: Token, description: A token., kind: secret, type: text,
          required: true, default: sk-shipped }
      - { name: steps, label: Steps, description: A budget., kind: config, type: integer,
          required: false, default: many, target: { file: kinby.toml, key: budgets.steps } }
      - { name: steps, label: Steps, description: A budget., kind: config, type: integer,
          required: false, target: { file: kinby.toml, key: budgets.steps } }
    logins:
      - { id: claude, label: Claude, description: Signs in., command: [claude, login],
          volume: /root/.claude, prompt_pattern: '(?P<url>\\S+) (?P<code>\\S+)' }
      - { id: claude, label: Claude, description: Signs in., command: [claude, login],
          volume: /root/.claude, prompt_pattern: '(?P<url>\\S+) (?P<code>\\S+)' }
      - { id: codex, label: Codex, description: Signs in., command: [codex, login],
          volume: codex, prompt_pattern: '(?P<url>\\S+) (?P<code>\\S+)' }
"""
    factory = FACTORY.replace("  coder: { image: coder }\n", declarations)

    found = problems(tmp_path, FILES | {"factory.yaml": factory})

    assert found[:2] == [
        'Instance "coder": Secret field "TOKEN" has a default. A secret never ships in a template.',
        'Instance "coder": Setup field "steps" has a default that is not a whole number.',
    ]
    assert found[2:] == [
        'Instance "coder": Setup field "steps" is declared more than once.',
        'Instance "coder": Login "claude" is declared more than once.',
        'Instance "coder": Login "codex" mounts its volume at "codex", '
        "which is not an absolute path.",
    ]


def _field(name: str = "drafts", **options: str | None) -> str:
    """One setup field in flow YAML, an integer landing in kinby.toml unless *options* say not."""
    declared = {
        "name": name,
        "label": "Drafts",
        "description": "Drafts a day.",
        "kind": "config",
        "type": "integer",
        "required": "false",
        "target": "{ file: kinby.toml, key: budgets.steps }",
    } | options
    return "{ " + ", ".join(f"{key}: {value}" for key, value in declared.items() if value) + " }"


def _declaring(declarations: str) -> dict[str, str]:
    factory = FACTORY.replace(
        "  coder: { image: coder }\n", f"  coder:\n    image: coder\n{declarations}"
    )
    return FILES | {"factory.yaml": factory}


@pytest.mark.parametrize(
    ("field", "failure"),
    [
        (_field(target=None), 'Configuration field "drafts" names no target.'),
        (_field(label='""'), 'Setup field "drafts" has no label.'),
        (
            _field(choices="[one, two]"),
            'Setup field "drafts" has choices, but it is not a choice field.',
        ),
        (_field(type="choice"), 'Choice field "drafts" offers no choices.'),
        (
            _field("EDITOR-KEY", kind="secret", type="text", target=None),
            'Secret field "EDITOR-KEY" is not an environment variable name.',
        ),
        (
            _field("SEARCH_TOKEN", kind="secret", type="boolean", target=None),
            'Secret field "SEARCH_TOKEN" is boolean. A secret is text or multiline.',
        ),
        (
            _field("SEARCH_TOKEN", kind="secret", type="text"),
            'Secret field "SEARCH_TOKEN" has a target. A secret lands in the instance secrets.',
        ),
        (
            _field("model", type="text", default="openai:gpt-5"),
            'Setup field "model" overrides a built-in field, which kinby writes itself, '
            "so it takes no target.",
        ),
        (
            _field("model", type="multiline", target=None),
            'Setup field "model" overrides a built-in field, so it keeps its kind and type.',
        ),
        (
            _field(type="boolean", default="1"),
            'Setup field "drafts" has a default that is not true or false.',
        ),
        (
            _field(type="choice", choices="[plain, formal]", default="loud"),
            'Setup field "drafts" has a default that is not one of plain, formal.',
        ),
        (
            _field(type="email", default="not-an-email"),
            'Setup field "drafts" has a default that is not an email address, '
            "like someone@example.com.",
        ),
        (
            _field(type="url", default="owner/name"),
            'Setup field "drafts" has a default that is not a URL with a scheme and a host, '
            "like https://example.com.",
        ),
    ],
    ids=[
        "missing-target",
        "no-label",
        "choices-on-a-non-choice",
        "choice-without-choices",
        "secret-not-an-environment-name",
        "secret-of-another-type",
        "target-on-a-secret",
        "target-on-a-built-in",
        "built-in-of-another-type",
        "boolean-default",
        "choice-default",
        "email-default",
        "url-default",
    ],
)
def test_the_check_fails_each_malformed_setup_field(tmp_path, field, failure):
    found = problems(tmp_path, _declaring(f"    setup_fields:\n      - {field}\n"))

    assert found == [f'Instance "coder": {failure}']


def _login(login_id: str, pattern: str) -> str:
    return (
        f"{{ id: {login_id}, label: Editor, description: Signs in., command: [editor, login], "
        f"volume: /root/.editor, prompt_pattern: '{pattern}' }}"
    )


@pytest.mark.parametrize(
    ("login", "failure"),
    [
        (
            _login("reviewer", r"(?P<url>\S+"),
            'Login "reviewer" has a prompt pattern that does not compile: '
            "missing ), unterminated subpattern at position 0.",
        ),
        (
            _login("mailer", r"(?P<url>\S+) (?P<token>\S+)"),
            'Login "mailer" has a prompt pattern without the named group "code".',
        ),
        (
            _login("Mail box", r"(?P<url>\S+) (?P<code>\S+)"),
            'Login "Mail box" has an id that cannot name a volume: use letters, digits, ".", '
            '"_" and "-", starting with a letter or digit.',
        ),
    ],
    ids=["pattern-does-not-compile", "pattern-without-code", "id-not-a-volume-name"],
)
def test_the_check_fails_each_malformed_login(tmp_path, login, failure):
    found = problems(tmp_path, _declaring(f"    logins:\n      - {login}\n"))

    assert found == [f'Instance "coder": {failure}']


def test_the_check_fails_a_template_that_does_not_initialize(tmp_path):
    for case, (name, content, problem) in enumerate(
        [
            ("kinby.toml", "[models\n", "Expected ']' at the end of a table declaration"),
            ("kinby.toml", 'id = "mine"\n', 'Template cannot set "id".'),
            (".env", "TOKEN=sk-shipped\n", 'Template cannot copy ".env".'),
        ]
    ):
        found = problems(tmp_path / str(case), FILES | {f"instances/coder/{name}": content})

        assert len(found) == 1
        assert found[0].startswith(f'Instance "coder": the template does not initialize: {problem}')


def test_the_check_fails_a_suggested_pick_the_shipped_library_does_not_hold(tmp_path):
    factory = FACTORY.replace(
        "coder: { image: coder }", "coder: { image: coder, suggested_picks: [tdd, absent] }"
    )

    found = problems(tmp_path, FILES | {"factory.yaml": factory})

    assert found == [
        'Instance "coder" suggests skill "absent", which the shipped library does not hold.'
    ]


def test_a_prompt_outside_the_factory_folder_does_not_resolve(tmp_path):
    (tmp_path / "secret.md").write_text("outside\n", encoding="utf-8")
    factory = FACTORY.replace("prompts/review.md", "../../../secret.md")

    assert problems(tmp_path, FILES | {"factory.yaml": factory}) == [
        'Step "review" names prompt "../../../secret.md", which is not a file in the factory.'
    ]


def test_the_check_fails_a_step_whose_input_no_earlier_step_or_work_item_holds(tmp_path):
    factory = (
        FACTORY.replace("requires: [branch, repo]", "requires: [branch, pr, title]")
        .replace("requires: [branch]\n    outcomes", "requires: [repo, issue]\n    outcomes")
        .replace("done_requires: [pr]", "done_requires: [pr, merged]")
    )

    assert problems(tmp_path, FILES | {"factory.yaml": factory}) == [
        'Step "open-pr" requires "pr", which no earlier step declares in its results '
        "and the work item does not carry.",
        'Step "open-pr" requires "title", which no earlier step declares in its results '
        "and the work item does not carry.",
        'done_requires names "merged", which no step declares in its results '
        "and the work item does not carry.",
    ]


def test_the_check_fails_a_wait_filter_naming_a_value_no_earlier_step_or_work_item_holds(
    tmp_path,
):
    another = '\n      - body.repository.name: "{{repo}}"\n        routine: "{{title}}"'
    factory = FACTORY.replace('"{{pr}}"', '"{{merged}}"' + another)

    assert problems(tmp_path, FILES | {"factory.yaml": factory}) == [
        'Step "babysit" matches "body.pull_request.number" against "merged", which no earlier '
        "step declares in its results and the work item does not carry.",
        'Step "babysit" matches "routine" against "title", which no earlier '
        "step declares in its results and the work item does not carry.",
    ]


def test_the_check_fails_a_command_naming_a_value_no_earlier_step_or_work_item_holds(tmp_path):
    factory = FACTORY.replace(
        'run: ["uv run pytest"]', 'run: ["git switch {{branch}}", "make {{pr}}"]'
    )

    assert problems(tmp_path, FILES | {"factory.yaml": factory}) == [
        'Step "checks" names "pr" in a command, which no earlier step declares in its results '
        "and the work item does not carry."
    ]


def test_a_wait_filter_reads_only_the_routine_a_header_or_the_body(tmp_path):
    factory = FACTORY.replace("headers.X-GitHub-Event", "event").replace(
        "body.pull_request.number", "body"
    )

    found = problems(tmp_path, FILES | {"factory.yaml": factory})

    assert [problem.split(": ")[1] for problem in found] == [
        "steps.4.wait.signal.0.event.[key]",
        "steps.4.wait.signal.0.body.[key]",
        # The filter that failed no longer counts toward the list's one filter at least.
        "steps.4.wait.signal",
    ]


def test_the_check_fails_a_resume_of_anything_but_an_earlier_step_of_the_same_client(tmp_path):
    resumes = """\
  - id: fix
    kind: client
    in: coder
    client: codex
    prompt: prompts/implement.md
    hook: record_branch
    resume: implement
  - id: redo
    kind: client
    in: coder
    client: claude
    prompt: prompts/implement.md
    hook: record_branch
    resume: review
  - id: again
    kind: client
    in: coder
    client: claude
    prompt: prompts/implement.md
    hook: record_branch
    resume: later
"""
    factory = FACTORY.replace("  - id: checks\n", resumes + "  - id: checks\n")

    assert problems(tmp_path, FILES | {"factory.yaml": factory}) == [
        'Step "fix" resumes "implement", which is not an earlier codex step in instance "coder".',
        'Step "redo" resumes "review", which is not an earlier claude step in instance "coder".',
        'Step "again" resumes "later", which is not an earlier claude step in instance "coder".',
    ]


def test_the_check_fails_a_send_back_to_a_step_that_is_not_earlier(tmp_path):
    factory = FACTORY.replace(
        "outcomes: { clean: next, changes: { back: implement, max: 3 } }",
        "outcomes: { changes: { back: review, max: 3 }, later: { back: checks, max: 1 } }",
    )

    assert problems(tmp_path, FILES | {"factory.yaml": factory}) == [
        'Step "review" sends work back to "review", which is not an earlier step.',
        'Step "review" sends work back to "checks", which is not an earlier step.',
    ]


def test_the_check_fails_a_failure_send_back_that_is_not_earlier_or_that_also_retries(tmp_path):
    factory = FACTORY.replace(
        '    run: ["uv run pytest"]\n',
        '    run: ["uv run pytest"]\n    failed: { back: open-pr, max: 2 }\n',
    ).replace("    retry: 0\n", "    retry: 0\n    failed: { back: implement, max: 1 }\n")

    assert problems(tmp_path, FILES | {"factory.yaml": factory}) == [
        'Step "checks" sends work back to "open-pr", which is not an earlier step.',
        'Step "open-pr" declares both retry and failed, but a failed attempt is sent back, '
        "never tried again.",
    ]


def test_the_check_fails_a_resume_that_is_not_an_earlier_client_step(tmp_path):
    factory = FACTORY.replace(
        "    hook: record_branch\n", "    hook: record_branch\n    resume: implement\n", 1
    ).replace(
        "  - id: checks\n",
        "  - id: fix\n"
        "    kind: client\n"
        "    in: coder\n"
        "    client: claude\n"
        "    prompt: prompts/implement.md\n"
        "    hook: record_branch\n"
        "    resume: review\n"
        "  - id: refix\n"
        "    kind: client\n"
        "    in: coder\n"
        "    client: claude\n"
        "    prompt: prompts/implement.md\n"
        "    hook: record_branch\n"
        "    resume: fix\n"
        "  - id: checks\n",
    )

    assert problems(tmp_path, FILES | {"factory.yaml": factory}) == [
        'Step "implement" resumes "implement", which is not an earlier claude step in instance '
        '"coder".',
        'Step "fix" resumes "review", which is not an earlier claude step in instance "coder".',
    ]


def test_agent_and_client_steps_must_name_a_hook(tmp_path):
    factory = FACTORY.replace("    hook: record_branch\n", "").replace(
        "    hook: read_verdict\n", ""
    )

    assert problems(tmp_path, FILES | {"factory.yaml": factory}) == [
        "factory.yaml: steps.0.client.hook: Field required",
        "factory.yaml: steps.1.agent.hook: Field required",
    ]


def test_the_check_reports_a_factory_file_that_is_missing_or_not_yaml(tmp_path):
    assert problems(tmp_path / "missing", {"prompts/review.md": "Review.\n"}) == [
        "The factory has no factory.yaml."
    ]
    found = problems(tmp_path / "broken", FILES | {"factory.yaml": "steps: [\n"})
    assert len(found) == 1
    assert found[0].startswith("factory.yaml is not YAML: ")
    found = problems(tmp_path / "circular", FILES | {"factory.yaml": "x: &x [*x]\n"})
    assert len(found) == 1
    assert found[0].startswith("factory.yaml is not a JSON document: ")


def test_the_check_reports_a_factory_file_it_cannot_read_as_utf8_text(tmp_path):
    hub = factory_hub(tmp_path)
    folder = tmp_path / "hub" / "factories" / "tickets"
    write_factory(folder, FILES)
    (folder / "factory.yaml").write_bytes(b"name: tickets\n\xff\n")
    client = hub_client(hub)

    async def check() -> list[str]:
        result = await client.call(FACTORY_CHECK, FactoryCheckCommand(name="tickets"))
        assert isinstance(result, FactoryCheckResult)
        return result.problems

    found = asyncio.run(check())
    assert len(found) == 1
    assert found[0].startswith("factory.yaml cannot be read as UTF-8 text: ")


async def read(client, name: str = "tickets") -> FactoryResult:
    result = await client.call(FACTORY_GET, FactoryGetCommand(name=name))
    assert isinstance(result, FactoryResult)
    return result


def test_an_edit_that_passes_the_check_replaces_the_factory(tmp_path):
    hub = factory_hub(tmp_path)
    write_factory(tmp_path / "hub" / "factories" / "tickets", FILES)
    client = hub_client(hub)
    changed = {
        "factory.yaml": FACTORY.replace("prompts/review.md", "prompts/check.md"),
        "prompts/check.md": "Check the branch.\n",
    }

    async def scenario() -> None:
        before = await read(client)
        edited = await client.call(
            FACTORY_EDIT, FactoryEditCommand(name="tickets", files=changed, hash=before.hash)
        )

        assert isinstance(edited, FactoryResult)
        assert edited.files == FILES | changed
        assert edited.hash != before.hash
        assert await read(client) == edited

    asyncio.run(scenario())


def test_an_edit_that_fails_the_check_changes_nothing(tmp_path):
    hub = factory_hub(tmp_path)
    write_factory(tmp_path / "hub" / "factories" / "tickets", FILES)
    client = hub_client(hub)
    broken = {
        "factory.yaml": FACTORY.replace("prompts/review.md", "prompts/check.md"),
        "prompts/implement.md": "Implement it.\n",
    }

    async def scenario() -> None:
        before = await read(client)
        refused = await client.call(
            FACTORY_EDIT, FactoryEditCommand(name="tickets", files=broken, hash=before.hash)
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert (
            'Step "review" names prompt "prompts/check.md", which is not a file in the factory.'
            in refused.message
        )
        assert await read(client) == before

    asyncio.run(scenario())


def test_an_edit_from_a_stale_read_is_refused(tmp_path):
    hub = factory_hub(tmp_path)
    write_factory(tmp_path / "hub" / "factories" / "tickets", FILES)
    client = hub_client(hub)

    async def scenario() -> None:
        before = await read(client)
        command = FactoryEditCommand(
            name="tickets", files={"prompts/review.md": "Review it.\n"}, hash=before.hash
        )
        await client.call(FACTORY_EDIT, command)
        stale = await client.call(
            FACTORY_EDIT, command.model_copy(update={"files": {"prompts/review.md": "Mine.\n"}})
        )

        assert isinstance(stale, ErrorEnvelope)
        assert stale.code is ErrorCode.STALE
        assert (await read(client)).files["prompts/review.md"] == "Review it.\n"

    asyncio.run(scenario())


def test_editing_a_shipped_factory_copies_it_into_the_hubs_factories(tmp_path):
    shipped = renamed(FILES, "software")
    hub = factory_hub(tmp_path, shipped={"software": shipped})
    client = hub_client(hub)

    async def scenario() -> None:
        before = await read(client, "software")
        edited = await client.call(
            FACTORY_EDIT,
            FactoryEditCommand(
                name="software", files={"prompts/review.md": "Review it.\n"}, hash=before.hash
            ),
        )
        listed = await client.call(FACTORY_LIST, FactoryListCommand())

        assert before.source is FactorySource.SHIPPED
        assert isinstance(edited, FactoryResult)
        assert edited.source is FactorySource.HUB
        assert edited.files == shipped | {"prompts/review.md": "Review it.\n"}
        assert await read(client, "software") == edited
        assert isinstance(listed, FactoryListResult)
        assert listed.factories == [FactorySummary(name="software", source=FactorySource.HUB)]
        assert (tmp_path / "shipped" / "software" / "prompts" / "review.md").read_text(
            encoding="utf-8"
        ) == FILES["prompts/review.md"]

    asyncio.run(scenario())


def test_an_edit_without_a_hash_creates_a_factory_only_when_none_has_its_name(tmp_path):
    hub = factory_hub(tmp_path, shipped={"software": renamed(FILES, "software")})
    client = hub_client(hub)

    async def scenario() -> None:
        created = await client.call(
            FACTORY_EDIT, FactoryEditCommand(name="tickets", files=FILES, hash=None)
        )
        taken = await client.call(
            FACTORY_EDIT,
            FactoryEditCommand(name="software", files=renamed(FILES, "software"), hash=None),
        )

        assert isinstance(created, FactoryResult)
        assert created.files == FILES
        assert isinstance(taken, ErrorEnvelope)
        assert taken.code is ErrorCode.STALE

    asyncio.run(scenario())


def test_an_edit_cannot_write_outside_the_factorys_folder(tmp_path):
    hub = factory_hub(tmp_path)
    write_factory(tmp_path / "hub" / "factories" / "tickets", FILES)
    client = hub_client(hub)

    async def scenario() -> None:
        before = await read(client)
        refused = await client.call(
            FACTORY_EDIT,
            FactoryEditCommand(name="tickets", files={"../escaped.md": "out\n"}, hash=before.hash),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert not (tmp_path / "hub" / "factories" / "escaped.md").exists()
        assert await read(client) == before

    asyncio.run(scenario())


def test_an_edit_that_fails_to_swap_in_keeps_the_last_good_factory(tmp_path, monkeypatch):
    hub = factory_hub(tmp_path)
    write_factory(tmp_path / "hub" / "factories" / "tickets", FILES)
    client = hub_client(hub)
    destination = tmp_path / "hub" / "factories" / "tickets"
    rename = Path.rename

    def failing_rename(self: Path, target: Path) -> Path:
        # Only the staged edit fails to move in; putting the old folder back still works.
        if Path(target) == destination and self.name == "tickets":
            raise OSError("disk full")
        return rename(self, target)

    async def scenario() -> None:
        before = await read(client)
        monkeypatch.setattr(Path, "rename", failing_rename)
        failed = await client.call(
            FACTORY_EDIT,
            FactoryEditCommand(
                name="tickets", files={"prompts/review.md": "Review it.\n"}, hash=before.hash
            ),
        )
        monkeypatch.undo()

        assert isinstance(failed, ErrorEnvelope)
        assert failed.code is ErrorCode.INTERNAL
        assert await read(client) == before

    asyncio.run(scenario())


BYTECODE = b"\xa7\r\r\n\x00\xff"


def test_get_leaves_out_the_bytecode_python_writes_beside_the_factorys_python(tmp_path):
    hub = factory_hub(tmp_path)
    folder = tmp_path / "hub" / "factories" / "tickets"
    write_factory(folder, FILES)
    client = hub_client(hub)

    async def scenario() -> None:
        before = await read(client)
        bytecode = folder / "instances" / "coder" / "hooks" / "__pycache__" / "x.pyc"
        bytecode.parent.mkdir()
        bytecode.write_bytes(BYTECODE)
        after = await read(client)
        edited = await client.call(
            FACTORY_EDIT,
            FactoryEditCommand(
                name="tickets", files={"prompts/review.md": "Review it.\n"}, hash=before.hash
            ),
        )

        assert after == before
        assert isinstance(edited, FactoryResult)

    asyncio.run(scenario())


def test_editing_a_shipped_factory_leaves_its_bytecode_behind(tmp_path):
    hub = factory_hub(tmp_path, shipped={"software": renamed(FILES, "software")})
    bytecode = tmp_path / "shipped" / "software" / "instances" / "coder" / "tools" / "__pycache__"
    bytecode.mkdir()
    (bytecode / "github.pyc").write_bytes(BYTECODE)
    client = hub_client(hub)

    async def scenario() -> None:
        before = await read(client, "software")
        edited = await client.call(
            FACTORY_EDIT,
            FactoryEditCommand(
                name="software", files={"prompts/review.md": "Review it.\n"}, hash=before.hash
            ),
        )

        assert isinstance(edited, FactoryResult)
        assert not list((tmp_path / "hub" / "factories" / "software").rglob("__pycache__"))

    asyncio.run(scenario())


def test_get_names_a_factory_file_that_is_not_text(tmp_path):
    hub = factory_hub(tmp_path)
    folder = tmp_path / "hub" / "factories" / "tickets"
    write_factory(folder, FILES)
    (folder / "prompts" / "logo.png").write_bytes(BYTECODE)
    client = hub_client(hub)

    async def scenario() -> None:
        refused = await client.call(FACTORY_GET, FactoryGetCommand(name="tickets"))

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert refused.fields == {"files": '"prompts/logo.png" is not UTF-8 text.'}

    asyncio.run(scenario())


def test_an_edit_keeps_an_empty_instance_template(tmp_path):
    hub = factory_hub(tmp_path)
    folder = tmp_path / "hub" / "factories" / "tickets"
    write_factory(
        folder,
        FILES
        | {
            "factory.yaml": FACTORY.replace(
                "  coder: { image: coder }", "  coder: { image: coder }\n  helper: {}"
            )
        },
    )
    (folder / "instances" / "helper").mkdir()
    client = hub_client(hub)

    async def scenario() -> None:
        before = await read(client)
        edited = await client.call(
            FACTORY_EDIT,
            FactoryEditCommand(
                name="tickets", files={"prompts/review.md": "Review it.\n"}, hash=before.hash
            ),
        )

        assert isinstance(edited, FactoryResult)

    asyncio.run(scenario())


def test_an_edit_keeps_an_instance_template_that_links_to_a_folder(tmp_path):
    hub = factory_hub(tmp_path)
    folder = tmp_path / "hub" / "factories" / "tickets"
    write_factory(
        folder, {name: content for name, content in FILES.items() if "instances/" not in name}
    )
    template = tmp_path / "coder"
    write_factory(
        template,
        {
            name.removeprefix("instances/coder/"): content
            for name, content in FILES.items()
            if name.startswith("instances/coder/")
        },
    )
    (folder / "instances").mkdir()
    (folder / "instances" / "coder").symlink_to(template, target_is_directory=True)
    client = hub_client(hub)

    async def scenario() -> None:
        before = await read(client)
        edited = await client.call(
            FACTORY_EDIT,
            FactoryEditCommand(
                name="tickets", files={"prompts/review.md": "Review it.\n"}, hash=before.hash
            ),
        )

        assert isinstance(edited, FactoryResult)

    asyncio.run(scenario())
