import asyncio
from pathlib import Path

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
      routine: github
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


def test_the_check_fails_a_template_configuration_field_that_does_not_land_in_kinby_toml(
    tmp_path,
):
    fields = """\
  coder:
    image: coder
    setup_fields:
      - { name: model, label: Model, description: Its model., kind: config, type: text,
          required: true, default: "openai:gpt-5" }
      - { name: TOKEN, label: Token, description: A token., kind: secret, type: text,
          required: true }
      - { name: steps, label: Steps, description: A budget., kind: config, type: integer,
          required: false, target: { file: kinby.toml, key: budgets.steps } }
      - { name: tone, label: Tone, description: How it writes., kind: config, type: text,
          required: false, target: { file: package.yaml, key: tone } }
      - { name: style, label: Style, description: How it looks., kind: config, type: text,
          required: false }
"""
    factory = FACTORY.replace("  coder: { image: coder }\n", fields)

    assert problems(tmp_path, FILES | {"factory.yaml": factory}) == [
        'Instance "coder": Configuration field "style" names no target.',
        'Instance "coder" asks for "tone", which targets no key of kinby.toml, '
        "where a template's configuration lands.",
    ]


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
        'Instance "coder": Secret field "TOKEN" has a default. A secret never ships in a package.',
        'Instance "coder": Setup field "steps" has a default that is not a whole number.',
    ]
    assert found[2:] == [
        'Instance "coder": Setup field "steps" is declared more than once.',
        'Instance "coder": Login "claude" is declared more than once.',
        'Instance "coder": Login "codex" mounts its volume at "codex", '
        "which is not an absolute path.",
    ]


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
    factory = FACTORY.replace('"{{pr}}"', '"{{merged}}"\n      body.repository.name: "{{repo}}"')

    assert problems(tmp_path, FILES | {"factory.yaml": factory}) == [
        'Step "babysit" matches "body.pull_request.number" against "merged", which no earlier '
        "step declares in its results and the work item does not carry."
    ]


def test_a_wait_filter_reads_only_the_routine_a_header_or_the_body(tmp_path):
    factory = FACTORY.replace("headers.X-GitHub-Event", "event").replace(
        "body.pull_request.number", "body"
    )

    found = problems(tmp_path, FILES | {"factory.yaml": factory})

    assert [problem.split(": ")[1] for problem in found] == [
        "steps.4.wait.signal.event.[key]",
        "steps.4.wait.signal.body.[key]",
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
