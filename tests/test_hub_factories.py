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
)
from kinby.hub import Hub
from tests.test_hub import FakeImages, FakeRuntime, hub_client

FACTORY = """\
name: tickets
requires:
  image: coder
work_item: { issue: int, repo: str }
steps:
  - id: implement
    kind: agent
    engine: claude
    prompt: prompts/implement.md
    check: record_branch
    results: { branch: str }
    timeout: 60m
  - id: review
    kind: agent
    engine: kinby
    prompt: prompts/review.md
    check: read_verdict
    requires: [branch]
    outcomes: { clean: next, changes: { back: implement, max: 3 } }
  - id: checks
    kind: command
    run: ["uv run pytest"]
    requires: [branch]
  - id: open-pr
    kind: code
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
    "tools/github.py": TOOLS,
    "hooks/record.py": HOOKS,
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
        assert [(factory.name, factory.source) for factory in listed.factories] == [
            ("software", FactorySource.SHIPPED),
            ("tickets", FactorySource.HUB),
        ]
        assert isinstance(got, FactoryResult)
        assert got.source is FactorySource.HUB
        assert got.files == FILES
        assert isinstance(missing, ErrorEnvelope)
        assert missing.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_the_check_passes_a_factory_whose_names_all_resolve(tmp_path):
    assert problems(tmp_path, FILES) == []


def test_the_check_passes_a_step_that_describes_itself(tmp_path):
    described = FACTORY.replace(
        "  - id: checks\n", "  - id: checks\n    description: Runs the tests.\n"
    )

    assert problems(tmp_path, FILES | {"factory.yaml": described}) == []


def test_the_check_reports_a_tool_or_hook_file_that_does_not_load(tmp_path):
    broken = {
        "tools/github.py": "raise RuntimeError('no token')\n",
        "hooks/record.py": "import not_installed\n",
    }

    found = problems(tmp_path, FILES | broken)

    assert found[:2] == [
        "tools/github.py: RuntimeError: no token",
        "hooks/record.py: ModuleNotFoundError: No module named 'not_installed'",
    ]
    assert (
        'Step "open-pr" calls tool "open_pull_request", which the factory\'s folder does not hold.'
        in found
    )


def _field(name: str = "drafts", **options: str | None) -> str:
    """One required field in flow YAML, an optional integer unless *options* say not."""
    declared = {
        "name": name,
        "label": "Drafts",
        "description": "Drafts a day.",
        "type": "integer",
        "required": "false",
    } | options
    return "{ " + ", ".join(f"{key}: {value}" for key, value in declared.items() if value) + " }"


def _requiring(declarations: str) -> dict[str, str]:
    factory = FACTORY.replace("  image: coder\n", f"  image: coder\n{declarations}")
    return FILES | {"factory.yaml": factory}


@pytest.mark.parametrize(
    ("required", "field", "failure"),
    [
        ("values", _field(label='""'), 'Setup field "drafts" has no label.'),
        (
            "values",
            _field(choices="[one, two]"),
            'Setup field "drafts" has choices, but it is not a choice field.',
        ),
        ("values", _field(type="choice"), 'Choice field "drafts" offers no choices.'),
        (
            "secrets",
            _field("SEARCH_TOKEN", type="boolean"),
            'Secret field "SEARCH_TOKEN" is boolean. A secret is text or multiline.',
        ),
        (
            "values",
            _field(default="many"),
            'Setup field "drafts" has a default that is not a whole number.',
        ),
        (
            "values",
            _field(type="boolean", default="1"),
            'Setup field "drafts" has a default that is not true or false.',
        ),
        (
            "values",
            _field(type="choice", choices="[plain, formal]", default="loud"),
            'Setup field "drafts" has a default that is not one of plain, formal.',
        ),
        (
            "values",
            _field(type="email", default="not-an-email"),
            'Setup field "drafts" has a default that is not an email address, '
            "like someone@example.com.",
        ),
    ],
    ids=[
        "no-label",
        "choices-on-a-non-choice",
        "choice-without-choices",
        "secret-of-another-type",
        "integer-default",
        "boolean-default",
        "choice-default",
        "email-default",
    ],
)
def test_the_check_fails_each_malformed_required_field(tmp_path, required, field, failure):
    assert problems(tmp_path, _requiring(f"  {required}:\n    - {field}\n")) == [failure]


def _login(login_id: str, pattern: str = r"(?P<url>\S+) (?P<code>\S+)") -> str:
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
            _login("Mail box"),
            'Login "Mail box" has an id that cannot name a volume: use letters, digits, ".", '
            '"_" and "-", starting with a letter or digit.',
        ),
    ],
    ids=["pattern-does-not-compile", "pattern-without-code", "id-not-a-volume-name"],
)
def test_the_check_fails_each_malformed_login(tmp_path, login, failure):
    assert problems(tmp_path, _requiring(f"  logins:\n    - {login}\n")) == [failure]


def test_the_check_fails_a_login_declared_more_than_once(tmp_path):
    logins = f"  logins:\n    - {_login('editor')}\n    - {_login('editor')}\n"

    assert problems(tmp_path, _requiring(logins)) == ['Login "editor" is declared more than once.']


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


def test_the_check_fails_a_closing_filter_naming_a_value_no_step_or_work_item_holds(tmp_path):
    factory = FACTORY + (
        "closing:\n"
        '  done: [{ body.pull_request.number: "{{pr}}", body.pull_request.merged: true }]\n'
        '  cancelled: [{ body.issue.number: "{{issue}}" }, { body.issue.title: "{{title}}" }]\n'
    )

    assert problems(tmp_path, FILES | {"factory.yaml": factory}) == [
        'closing matches "body.issue.title" against "title", which no step declares in its '
        "results and the work item does not carry."
    ]


@pytest.mark.parametrize(
    ("entry", "failure"),
    [
        ('""', "steps.2.command.run.0.constrained-str: String should have at least 1 character"),
        (
            "{ commands: Set Up }",
            "steps.2.command.run.0.RepositoryCommands.commands: "
            "String should match pattern '^[a-z0-9][a-z0-9_-]*$'",
        ),
        (
            "{ commands: setup, run: [make] }",
            "steps.2.command.run.0.RepositoryCommands.run: Extra inputs are not permitted",
        ),
        ("{ list: setup }", "steps.2.command.run.0.RepositoryCommands.commands: Field required"),
    ],
)
def test_the_check_fails_a_malformed_command_entry(tmp_path, entry, failure):
    factory = FACTORY.replace('run: ["uv run pytest"]', f"run: [{entry}]")

    assert f"factory.yaml: {failure}" in problems(tmp_path, FILES | {"factory.yaml": factory})


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


def test_an_agent_step_resumes_neither_itself_nor_a_later_step(tmp_path):
    def resuming(step: str, resumed: str) -> str:
        return (
            f"  - {{ id: {step}, kind: agent, engine: claude, prompt: prompts/implement.md, "
            f"resume: {resumed} }}\n"
        )

    factory = FACTORY.replace(
        "    check: record_branch\n", "    check: record_branch\n    resume: implement\n"
    ).replace(
        "  - id: checks\n", resuming("fix", "refix") + resuming("refix", "fix") + "  - id: checks\n"
    )

    assert problems(tmp_path, FILES | {"factory.yaml": factory}) == [
        'Step "implement" resumes "implement", which is not an earlier claude step.',
        'Step "fix" resumes "refix", which is not an earlier claude step.',
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
        assert [(factory.name, factory.source) for factory in listed.factories] == [
            ("software", FactorySource.HUB)
        ]
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
        bytecode = folder / "hooks" / "__pycache__" / "x.pyc"
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
    bytecode = tmp_path / "shipped" / "software" / "tools" / "__pycache__"
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


def test_an_edit_keeps_a_hooks_folder_that_links_elsewhere(tmp_path):
    hub = factory_hub(tmp_path)
    folder = tmp_path / "hub" / "factories" / "tickets"
    write_factory(
        folder, {name: content for name, content in FILES.items() if name != "hooks/record.py"}
    )
    write_factory(tmp_path / "hooks", {"record.py": HOOKS})
    (folder / "hooks").symlink_to(tmp_path / "hooks", target_is_directory=True)
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
