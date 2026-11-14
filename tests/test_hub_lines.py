"""A line is set up on a workbench under an owner, and removed, through the hub contract."""

import asyncio
from collections.abc import Awaitable
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr, ValidationError

from kinby.cli.client import ContractClient
from kinby.contracts import (
    FACTORY_LIST,
    FACTORY_REMOVE,
    INSTANCE_CREATE,
    LINE_GET,
    LINE_LIST,
    LINE_REMOVE,
    LINE_SETUP,
    LINE_SETUP_DESCRIBE,
    WORKBENCH_LIST,
    WORKBENCH_LOGIN_START,
    ErrorCode,
    ErrorEnvelope,
    FactoryListCommand,
    FactoryListResult,
    FactoryRemoveCommand,
    FactoryRole,
    InstanceCreateCommand,
    LifecycleOperationResult,
    Line,
    LineGetCommand,
    LineListCommand,
    LineListResult,
    LineRemoveCommand,
    LineSetupCommand,
    LineSetupDescribeCommand,
    LineSetupDescription,
    LineSetupResult,
    NewWorkbench,
    OperationState,
    RepositoryCommandList,
    SetupFieldKind,
    WorkbenchCreateCommand,
    WorkbenchListCommand,
    WorkbenchListResult,
    WorkbenchLoginStartCommand,
    WorkbenchOperationResult,
)
from kinby.hub import Hub
from tests.test_factory_templates import FILES as ROLE_FILES
from tests.test_factory_templates import created_roles, factory_hub
from tests.test_hub import FakeRuntime, created_instance, finished_operation, hub_client
from tests.test_hub_browser import FakeBrowserRuntime
from tests.test_hub_factories import write_factory
from tests.test_hub_logins import EDITOR
from tests.test_hub_workbench_logins import SIGNED
from tests.test_hub_workbench_workspace import _commands_set
from tests.test_hub_workbenches import CEC_BACKEND, created, finished, got

TICKETS = """\
name: tickets
requires:
  image: coder
  secrets:
    - { name: GH_TOKEN, label: GitHub token, description: Opens pull requests., type: text,
        required: true }
  values:
    - { name: MOODLE_URL, label: Moodle address, description: Where the course lives.,
        type: url, required: true }
    - { name: MOODLE_COURSE, label: Course, description: The course to fill., type: text,
        required: false }
  commands: [checks]
  suggested_picks: [tdd]
work_item: { issue: int }
steps:
  - id: checks
    kind: command
    run: [{ commands: checks }]
"""
REVIEWS = """\
name: reviews
requires: { image: coder }
work_item: { pr: int }
steps:
  - { id: review, kind: approve, summary: Review the pull request. }
"""


def _hub(tmp_path: Path, runtime: FakeRuntime | None = None) -> Hub:
    hub = factory_hub(tmp_path, {"factory.yaml": TICKETS}, runtime=runtime)
    write_factory(tmp_path / "hub" / "factories" / "reviews", {"factory.yaml": REVIEWS})
    return hub


async def _home(hub: Hub) -> UUID:
    """Start the hub, which creates its home instance, and wait for that creation."""
    home = (await hub.recover()).home
    assert home is not None
    assert (await finished_operation(hub_client(hub), home)).state is OperationState.SUCCEEDED
    return home.instance_id


async def _cec_backend(client: ContractClient) -> UUID:
    """A workbench that holds all the tickets factory requires."""
    workbench_id = (await created(client, CEC_BACKEND)).workbench_id
    await _commands_set(client, workbench_id, "checks", ["uv run pytest"])
    return workbench_id


async def _set_up(client: ContractClient, command: LineSetupCommand) -> Line:
    set_up = await client.call(LINE_SETUP, command)
    assert isinstance(set_up, LineSetupResult), set_up
    return set_up.line


async def _refused(client: ContractClient, command: LineSetupCommand) -> dict[str, str]:
    refused = await client.call(LINE_SETUP, command)
    assert isinstance(refused, ErrorEnvelope), refused
    assert refused.code is ErrorCode.INVALID_SETUP
    return refused.fields


async def _lines(client: ContractClient) -> list[Line]:
    listed = await client.call(LINE_LIST, LineListCommand())
    assert isinstance(listed, LineListResult), listed
    return listed.lines


def test_a_line_set_up_is_returned_listed_and_got_with_its_model_and_budget(tmp_path):
    hub = _hub(tmp_path)
    client = hub_client(hub)

    async def scenario() -> None:
        owner = (await created_instance(client)).instance_id
        workbench_id = await _cec_backend(client)

        line = await _set_up(
            client,
            LineSetupCommand(
                factory="tickets",
                workbench_id=workbench_id,
                owner_id=owner,
                model="anthropic:claude-opus-5-5",
                usd_per_day=20,
            ),
        )
        got_line = await client.call(LINE_GET, LineGetCommand(line_id=line.line_id))

        assert (line.factory, line.workbench_id, line.owner_id) == ("tickets", workbench_id, owner)
        assert (line.model, line.usd_per_day) == ("anthropic:claude-opus-5-5", 20)
        assert got_line == line
        assert await _lines(client) == [line]

    asyncio.run(scenario())


def test_a_line_set_up_with_no_owner_is_owned_by_the_home_instance(tmp_path):
    hub = _hub(tmp_path)
    client = hub_client(hub)

    async def scenario() -> None:
        home = await _home(hub)
        workbench_id = await _cec_backend(client)

        line = await _set_up(client, LineSetupCommand(factory="tickets", workbench_id=workbench_id))

        assert line.owner_id == home
        assert (line.model, line.usd_per_day) == (None, None)

    asyncio.run(scenario())


def test_setup_names_each_secret_value_and_command_list_the_workbench_lacks(tmp_path):
    hub = _hub(tmp_path)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        bare = await created(client, WorkbenchCreateCommand(name="bare", recipe="coder"))

        fields = await _refused(
            client, LineSetupCommand(factory="tickets", workbench_id=bare.workbench_id)
        )

        assert fields == {
            "workbench_id": 'Workbench "bare" lacks what factory "tickets" requires: '
            'secret "GH_TOKEN", value "MOODLE_URL", command list "checks".'
        }
        assert await _lines(client) == []

    asyncio.run(scenario())


SIGNING = """\
name: signing
requires:
  logins:
    - { id: editor, label: Editor account, description: Signs the editor in.,
        command: [editor, login], volume: /root/.editor,
        prompt_pattern: 'Open (?P<url>https://\\S+) and enter (?P<code>[A-Z0-9-]+)' }
  browser: true
work_item: { issue: int }
steps:
  - { id: review, kind: approve, summary: Review it. }
"""


def _signing_hub(tmp_path: Path, runtime: FakeRuntime) -> Hub:
    hub = factory_hub(tmp_path, runtime=runtime, browser=FakeBrowserRuntime("ws://nowhere"))
    write_factory(tmp_path / "hub" / "factories" / "signing", {"factory.yaml": SIGNING})
    return hub


def test_setup_names_each_login_not_signed_in_and_a_browser_the_workbench_lacks(tmp_path):
    runtime = FakeRuntime()
    hub = _signing_hub(tmp_path, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        bare = await created(client, WorkbenchCreateCommand(name="bare"))
        holding = await created(
            client, WorkbenchCreateCommand(name="holding", logins=[EDITOR], browser=True)
        )

        fields = [
            await _refused(client, LineSetupCommand(factory="signing", workbench_id=workbench_id))
            for workbench_id in (bare.workbench_id, holding.workbench_id)
        ]

        assert fields == [
            {
                "workbench_id": 'Workbench "bare" lacks what factory "signing" requires: '
                'login "editor", a browser.'
            },
            {
                "workbench_id": 'Workbench "holding" lacks what factory "signing" requires: '
                'login "editor".'
            },
        ]

    asyncio.run(scenario())


def test_a_workbench_with_its_login_signed_in_and_a_browser_takes_a_line(tmp_path):
    runtime = FakeRuntime()
    hub = _signing_hub(tmp_path, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        signed = SIGNED.model_copy(update={"recipe": None, "browser": True})
        workbench_id = (await created(client, signed)).workbench_id
        runtime.setup_exits.set()
        login = await client.call(
            WORKBENCH_LOGIN_START,
            WorkbenchLoginStartCommand(workbench_id=workbench_id, login_id="editor"),
        )
        assert (await finished(client, login)).state is OperationState.SUCCEEDED

        line = await _set_up(client, LineSetupCommand(factory="signing", workbench_id=workbench_id))

        assert line.workbench_id == workbench_id

    asyncio.run(scenario())


def test_a_workbench_has_no_browser_on_a_hub_that_runs_no_browser_service(tmp_path):
    hub = factory_hub(tmp_path)
    browsing = "name: browsing\nrequires: { browser: true }\n" + REVIEWS.split("\n", 2)[2]
    write_factory(tmp_path / "hub" / "factories" / "browsing", {"factory.yaml": browsing})
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        eager = await created(client, WorkbenchCreateCommand(name="eager", browser=True))

        fields = await _refused(
            client, LineSetupCommand(factory="browsing", workbench_id=eager.workbench_id)
        )

        assert fields == {
            "workbench_id": 'Workbench "eager" lacks what factory "browsing" requires: a browser.'
        }

    asyncio.run(scenario())


DRAFTS = {
    "factory.yaml": """\
name: drafts
requires: { image: coder }
work_item: { issue: int }
steps:
  - { id: plan, kind: agent, engine: kinby, prompt: prompts/plan.md,
      model: "anthropic:claude-opus-5-5" }
  - { id: draft, kind: agent, engine: kinby, prompt: prompts/plan.md }
  - { id: review, kind: agent, engine: codex, prompt: prompts/plan.md }
""",
    "prompts/plan.md": "Plan the issue.\n",
}


def test_setup_names_the_key_of_each_kinby_agent_steps_model_the_workbench_lacks(tmp_path):
    hub = _hub(tmp_path)
    write_factory(tmp_path / "hub" / "factories" / "drafts", DRAFTS)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        workbench_id = await _cec_backend(client)

        fields = await _refused(
            client,
            LineSetupCommand(factory="drafts", workbench_id=workbench_id, model="openai:gpt-5"),
        )

        assert fields == {
            "workbench_id": 'Workbench "cec-backend" lacks what factory "drafts" requires: '
            'secret "ANTHROPIC_API_KEY", secret "OPENAI_API_KEY".'
        }

    asyncio.run(scenario())


def test_setup_refuses_a_factory_whose_image_recipe_is_not_the_workbenchs(tmp_path):
    hub = _hub(tmp_path)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        base = await created(
            client, CEC_BACKEND.model_copy(update={"name": "base", "recipe": None})
        )
        await _commands_set(client, base.workbench_id, "checks", ["uv run pytest"])

        fields = await _refused(
            client, LineSetupCommand(factory="tickets", workbench_id=base.workbench_id)
        )

        assert fields == {
            "factory": 'Factory "tickets" requires image recipe "coder", '
            'and workbench "base" runs kinby\'s base image.'
        }

    asyncio.run(scenario())


def test_two_factories_are_set_up_on_one_workbench_and_one_instance_owns_both(tmp_path):
    hub = _hub(tmp_path)
    client = hub_client(hub)

    async def scenario() -> None:
        owner = (await created_instance(client)).instance_id
        workbench_id = await _cec_backend(client)

        tickets = await _set_up(
            client, LineSetupCommand(factory="tickets", workbench_id=workbench_id, owner_id=owner)
        )
        reviews = await _set_up(
            client, LineSetupCommand(factory="reviews", workbench_id=workbench_id, owner_id=owner)
        )
        again = await _refused(
            client, LineSetupCommand(factory="tickets", workbench_id=workbench_id, owner_id=owner)
        )

        assert await _lines(client) == [tickets, reviews]
        assert again == {
            "factory": 'Factory "tickets" is set up on workbench "cec-backend" already.'
        }

    asyncio.run(scenario())


def test_setup_refuses_an_owner_that_is_no_instance(tmp_path):
    hub = _hub(tmp_path)
    client = hub_client(hub)

    async def scenario() -> None:
        workbench_id = await _cec_backend(client)

        fields = await _refused(
            client,
            LineSetupCommand(factory="reviews", workbench_id=workbench_id, owner_id=workbench_id),
        )

        assert fields == {"owner_id": f'Instance "{workbench_id}" was not found.'}

    asyncio.run(scenario())


def test_only_a_factory_that_runs_on_a_workbench_is_set_up_on_one(tmp_path):
    hub = factory_hub(tmp_path, ROLE_FILES)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        workbench_id = (await created(client, CEC_BACKEND)).workbench_id

        fields = await _refused(
            client, LineSetupCommand(factory="tickets", workbench_id=workbench_id)
        )

        assert fields == {
            "factory": 'Factory "tickets" declares instances, so its steps run in them. '
            "Only a factory that runs on a workbench is set up on one."
        }

    asyncio.run(scenario())


def test_removing_a_line_leaves_its_workbench_as_it_is(tmp_path):
    runtime = FakeRuntime()
    hub = _hub(tmp_path, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        workbench_id = await _cec_backend(client)
        line = await _set_up(client, LineSetupCommand(factory="tickets", workbench_id=workbench_id))
        before = await got(client, workbench_id)

        removed = await client.call(LINE_REMOVE, LineRemoveCommand(line_id=line.line_id))
        gone = await client.call(LINE_GET, LineGetCommand(line_id=line.line_id))

        assert removed == LineListResult(lines=[])
        assert isinstance(gone, ErrorEnvelope)
        assert gone.code is ErrorCode.NOT_FOUND
        assert await got(client, workbench_id) == before
        assert (runtime.removed, runtime.deleted_volumes) == ([], [])

    asyncio.run(scenario())


def test_a_factory_with_lines_refuses_removal_and_names_them(tmp_path):
    hub = _hub(tmp_path)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        workbench_id = await _cec_backend(client)
        line = await _set_up(client, LineSetupCommand(factory="reviews", workbench_id=workbench_id))

        refused = await client.call(FACTORY_REMOVE, FactoryRemoveCommand(name="reviews"))
        await client.call(LINE_REMOVE, LineRemoveCommand(line_id=line.line_id))
        removed = await client.call(FACTORY_REMOVE, FactoryRemoveCommand(name="reviews"))
        listed = await client.call(FACTORY_LIST, FactoryListCommand())

        assert isinstance(refused, ErrorEnvelope)
        assert refused.message == (
            'Factory "reviews" has lines on workbench "cec-backend". '
            "Remove them before removing the factory."
        )
        assert removed == listed
        assert isinstance(listed, FactoryListResult)
        assert [factory.name for factory in listed.factories] == ["tickets"]

    asyncio.run(scenario())


def test_a_factory_whose_roles_instances_hold_refuses_removal_and_names_them(tmp_path):
    client = hub_client(factory_hub(tmp_path, ROLE_FILES))

    async def scenario() -> None:
        await created_roles(client)

        refused = await client.call(FACTORY_REMOVE, FactoryRemoveCommand(name="tickets"))

        assert isinstance(refused, ErrorEnvelope)
        assert refused.message == (
            'Factory "tickets" has instances tickets-coder, tickets-reviewer. '
            "Remove them before removing the factory."
        )

    asyncio.run(scenario())


def test_a_factory_kinby_ships_is_never_removed(tmp_path):
    shipped = tmp_path / "shipped" / "reviews"
    write_factory(shipped, {"factory.yaml": REVIEWS})
    client = hub_client(factory_hub(tmp_path, {"factory.yaml": TICKETS}))

    async def scenario() -> None:
        refused = await client.call(FACTORY_REMOVE, FactoryRemoveCommand(name="reviews"))

        assert isinstance(refused, ErrorEnvelope)
        assert refused.fields == {"name": 'kinby ships factory "reviews", so it stays.'}
        assert (shipped / "factory.yaml").exists()

    asyncio.run(scenario())


async def _racing(hub: Hub, dependent: Awaitable[object]) -> tuple[object, object]:
    """Start *dependent*, then a removal of the tickets factory, while a factory edit holds the
    store: each is past its own checks and waits on the store when the edit ends."""
    client = hub_client(hub)
    async with hub.factories._edit_lock:
        added = asyncio.ensure_future(dependent)
        await asyncio.sleep(0.2)
        removal = asyncio.ensure_future(
            client.call(FACTORY_REMOVE, FactoryRemoveCommand(name="tickets"))
        )
        await asyncio.sleep(0.2)
    return await added, await removal


def test_a_removal_racing_a_line_setup_refuses_once_the_line_is_set_up(tmp_path):
    hub = _hub(tmp_path)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        workbench_id = await _cec_backend(client)

        line, refused = await _racing(
            hub,
            client.call(LINE_SETUP, LineSetupCommand(factory="tickets", workbench_id=workbench_id)),
        )

        assert isinstance(line, LineSetupResult), line
        assert isinstance(refused, ErrorEnvelope), refused
        assert refused.message.startswith('Factory "tickets" has lines on workbench')

    asyncio.run(scenario())


def test_a_removal_racing_a_role_creation_refuses_once_the_instance_is_created(tmp_path):
    hub = factory_hub(tmp_path, ROLE_FILES)
    client = hub_client(hub)

    async def scenario() -> None:
        accepted, refused = await _racing(
            hub,
            client.call(
                INSTANCE_CREATE,
                InstanceCreateCommand(
                    factory_role=FactoryRole(factory="tickets", name="coder"),
                    model="openai:gpt-5",
                    secrets={"api_key": "sk-test"},
                ),
            ),
        )

        assert isinstance(accepted, LifecycleOperationResult), accepted
        assert isinstance(refused, ErrorEnvelope), refused
        assert refused.message.startswith('Factory "tickets" has instances tickets-coder')
        assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED

    asyncio.run(scenario())


async def _described(
    client: ContractClient, command: LineSetupDescribeCommand
) -> LineSetupDescription:
    described = await client.call(LINE_SETUP_DESCRIBE, command)
    assert isinstance(described, LineSetupDescription), described
    return described


async def _workbenches(client: ContractClient) -> list[str]:
    listed = await client.call(WORKBENCH_LIST, WorkbenchListCommand())
    assert isinstance(listed, WorkbenchListResult), listed
    return [workbench.name for workbench in listed.workbenches]


def test_the_setup_on_a_chosen_workbench_asks_only_for_what_the_factory_requires_and_it_lacks(
    tmp_path,
):
    hub = _hub(tmp_path)
    write_factory(tmp_path / "hub" / "factories" / "drafts", DRAFTS)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        bare = await created(client, WorkbenchCreateCommand(name="bare", recipe="coder"))
        holding = await _cec_backend(client)

        asked = [
            await _described(
                client, LineSetupDescribeCommand(factory="tickets", workbench_id=workbench_id)
            )
            for workbench_id in (bare.workbench_id, holding)
        ]
        keys = await _described(
            client,
            LineSetupDescribeCommand(factory="drafts", workbench_id=holding, model="openai:gpt-5"),
        )

        assert [field.name for field in asked[0].secrets] == ["GH_TOKEN"]
        assert [field.name for field in asked[0].values] == ["MOODLE_URL"]
        assert (asked[0].commands, asked[0].workbench) == (["checks"], [])
        assert asked[1] == LineSetupDescription(
            workbench=[],
            secrets=[],
            values=[],
            commands=[],
            logins=[],
            browser=False,
            suggested_picks=["tdd"],
        )
        assert [field.name for field in keys.secrets] == ["ANTHROPIC_API_KEY", "OPENAI_API_KEY"]

    asyncio.run(scenario())


def test_the_setup_on_no_workbench_asks_for_all_the_factory_requires_and_a_workbench_needs(
    tmp_path,
):
    hub = _hub(tmp_path)
    client = hub_client(hub)

    async def scenario() -> None:
        asked = await _described(client, LineSetupDescribeCommand(factory="tickets"))

        assert [field.name for field in asked.workbench] == ["name", "workspace_source"]
        assert [(field.name, field.kind) for field in asked.secrets] == [
            ("GH_TOKEN", SetupFieldKind.SECRET)
        ]
        assert [(field.name, field.required) for field in asked.values] == [
            ("MOODLE_URL", True),
            ("MOODLE_COURSE", False),
        ]
        assert (asked.commands, asked.logins, asked.browser) == (["checks"], [], False)
        assert asked.suggested_picks == ["tdd"]

    asyncio.run(scenario())


def test_the_setup_asks_for_each_login_to_sign_in_and_a_browser_the_workbench_lacks(tmp_path):
    hub = _signing_hub(tmp_path, FakeRuntime())
    client = hub_client(hub)

    async def scenario() -> None:
        bare = await created(client, WorkbenchCreateCommand(name="bare"))

        chosen = await _described(
            client, LineSetupDescribeCommand(factory="signing", workbench_id=bare.workbench_id)
        )
        new = await _described(client, LineSetupDescribeCommand(factory="signing"))

        assert ([login.id for login in chosen.logins], chosen.browser) == (["editor"], True)
        assert ([login.id for login in new.logins], new.browser) == (["editor"], False)

    asyncio.run(scenario())


def test_one_setup_creates_the_workbench_with_its_answers_and_the_line(tmp_path):
    hub = _hub(tmp_path)
    client = hub_client(hub)

    async def scenario() -> None:
        home = await _home(hub)

        set_up = await client.call(
            LINE_SETUP,
            LineSetupCommand(
                factory="tickets",
                workbench=NewWorkbench(
                    name="landing", workspace_source="https://github.com/owner/landing.git"
                ),
                secrets={"GH_TOKEN": SecretStr("ghp-secret")},
                values={"MOODLE_URL": "https://moodle.example.com"},
                commands={"checks": ["uv run pytest"]},
            ),
        )
        assert isinstance(set_up, LineSetupResult), set_up
        assert set_up.operation_id is not None
        creation = await finished(
            client,
            WorkbenchOperationResult(
                operation_id=set_up.operation_id, workbench_id=set_up.line.workbench_id
            ),
        )
        workbench = await got(client, set_up.line.workbench_id)

        assert creation.state is OperationState.SUCCEEDED
        assert (set_up.line.factory, set_up.line.owner_id) == ("tickets", home)
        assert (workbench.name, workbench.recipe, workbench.workspace_source) == (
            "landing",
            "coder",
            "https://github.com/owner/landing.git",
        )
        assert (workbench.values, workbench.secrets) == (
            {"MOODLE_URL": "https://moodle.example.com"},
            ["GH_TOKEN"],
        )
        assert workbench.repository_commands == [
            RepositoryCommandList(name="checks", commands=["uv run pytest"])
        ]
        assert await _lines(client) == [set_up.line]

    asyncio.run(scenario())


def test_one_setup_stores_its_answers_on_a_chosen_workbench_and_sets_the_line_up(tmp_path):
    hub = _hub(tmp_path)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        bare = await created(client, WorkbenchCreateCommand(name="bare", recipe="coder"))

        set_up = await client.call(
            LINE_SETUP,
            LineSetupCommand(
                factory="tickets",
                workbench_id=bare.workbench_id,
                secrets={"GH_TOKEN": SecretStr("ghp-secret")},
                values={"MOODLE_URL": "https://moodle.example.com"},
                commands={"checks": ["uv run pytest"]},
            ),
        )
        assert isinstance(set_up, LineSetupResult), set_up
        assert set_up.operation_id is not None
        stored = await finished(
            client,
            WorkbenchOperationResult(
                operation_id=set_up.operation_id, workbench_id=bare.workbench_id
            ),
        )
        workbench = await got(client, bare.workbench_id)

        assert stored.state is OperationState.SUCCEEDED
        assert (workbench.values, workbench.secrets) == (
            {"MOODLE_URL": "https://moodle.example.com"},
            ["GH_TOKEN"],
        )
        assert [commands.name for commands in workbench.repository_commands] == ["checks"]
        assert await _lines(client) == [set_up.line]

    asyncio.run(scenario())


def test_a_refused_setup_creates_neither_the_workbench_nor_the_line(tmp_path):
    hub = _hub(tmp_path)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)

        fields = await _refused(
            client,
            LineSetupCommand(
                factory="tickets",
                workbench=NewWorkbench(name="landing"),
                hub_secrets={"GH_TOKEN": "github"},
                values={"MOODLE_URL": "moodle"},
                commands={"checks": ["uv run pytest"]},
            ),
        )

        assert fields == {
            "GH_TOKEN": 'The hub keeps no secret named "github".',
            "MOODLE_URL": "Moodle address is not a URL with a scheme and a host, like "
            "https://example.com.",
        }
        assert await _workbenches(client) == []
        assert await _lines(client) == []

    asyncio.run(scenario())


def test_a_setup_that_creates_a_workbench_names_what_its_answers_leave_lacking(tmp_path):
    hub = _hub(tmp_path)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        await _cec_backend(client)

        fields = await _refused(
            client,
            LineSetupCommand(factory="tickets", workbench=NewWorkbench(name="cec-backend")),
        )

        assert fields == {
            "workbench.name": 'A workbench named "cec-backend" exists already.',
            "workbench": 'Workbench "cec-backend" lacks what factory "tickets" requires: '
            'secret "GH_TOKEN", value "MOODLE_URL", command list "checks".',
        }
        assert await _workbenches(client) == ["cec-backend"]

    asyncio.run(scenario())


def test_a_setup_names_one_workbench_chosen_or_new():
    for named in ({}, {"workbench_id": uuid4(), "workbench": NewWorkbench(name="landing")}):
        with pytest.raises(ValidationError, match="Choose a workbench or describe a new one"):
            LineSetupCommand.model_validate({"factory": "tickets", **named})
