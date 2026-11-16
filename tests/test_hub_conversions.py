"""An instance that picked a factory role converts into a workbench and a line owned by the home
instance, through the hub contract."""

import asyncio
import hashlib
import hmac
import json
import shutil
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from uuid import UUID, uuid4

import aiohttp
import pytest

from kinby.contracts import (
    FACTORY_RUN_CANCEL,
    FACTORY_RUN_LIST,
    INSTANCE_CONVERT,
    INSTANCE_CREATE,
    INSTANCE_LIST,
    INSTANCE_RELAY_READ,
    LINE_LIST,
    LINE_SETUP,
    WORKBENCH_GET,
    WORKBENCH_LIST,
    WORKBENCH_PICKS_GET,
    WORKBENCH_START,
    DeclaredSetup,
    ErrorCode,
    ErrorEnvelope,
    FactoryRun,
    FactoryRunCancelCommand,
    FactoryRunListCommand,
    FactoryRunListResult,
    FactoryRunStatus,
    InstanceConvertCommand,
    InstanceCreateCommand,
    InstanceListCommand,
    InstanceListResult,
    InstanceRelayReadCommand,
    InstanceRelayReadResult,
    LifecycleOperationResult,
    Line,
    LineListCommand,
    LineListResult,
    LineSetupCommand,
    LineSetupResult,
    OperationState,
    RepositoryCommandList,
    RoutineListResult,
    ThreadListResult,
    Workbench,
    WorkbenchGetCommand,
    WorkbenchListCommand,
    WorkbenchListResult,
    WorkbenchOperationResult,
    WorkbenchPicksGetCommand,
    WorkbenchPicksResult,
    WorkbenchStartCommand,
)
from kinby.core.threads import ThreadStore
from kinby.instance import Serve
from kinby.instance.layout import MANIFEST_NAME, STATE_DIR
from kinby.memory import Episode, Fact, GraphStore, NodeId, RecappedTurn
from kinby.workbench import WorkbenchServer, WorkbenchSteps
from tests.test_factory_runs import detail, intake_client
from tests.test_hub import finished_operation, instance_environment
from tests.test_hub_factories import write_factory
from tests.test_hub_line_intake import LANDING, SECRET, TICKETS, Owned, owned
from tests.test_hub_routing import control_token
from tests.test_hub_workbenches import created
from tests.test_scheduler import call

#: The tickets factory rewritten to run on a workbench.
NEW = TICKETS | {
    "factory.yaml": TICKETS["factory.yaml"]
    .replace("name: GH_TOKEN", "name: GITHUB_TOKEN")
    .replace("intake:", "  commands: [checks]\nintake:")
}
#: What the coder template of the tickets factory as it was wrote into the instance's manifest.
WORKSPACE = """
[workspace]
source = "https://github.com/owner/cec-backend.git"

[workspace.commands]
checks = ["uv run pytest"]
"""


def _factory(owned: Owned, files: dict[str, str]) -> None:
    folder = owned.hub.directory / "factories" / "tickets"
    shutil.rmtree(folder)
    write_factory(folder, files)


async def _coder(owned: Owned, *, signed: bool = True) -> UUID:
    """An instance that picked the coder role of the tickets factory and the tdd skill, holding
    the webhook's secret when its deliveries are *signed*.

    No factory declares a role any more, so the instance is created vanilla with what the role's
    template wrote, and its record is given the role.
    """
    _factory(owned, NEW)
    webhook = {"WEBHOOK_SECRET": SECRET.decode()} if signed else {}
    accepted = await owned.client.call(
        INSTANCE_CREATE,
        InstanceCreateCommand(
            manifest_id="cec-backend",
            model="openai:gpt-5",
            secrets={"api_key": "sk-test", "GITHUB_TOKEN": "ghp-test", **webhook},
            picks=["tdd"],
        ),
    )
    assert isinstance(accepted, LifecycleOperationResult), accepted
    assert (await finished_operation(owned.client, accepted)).state is OperationState.SUCCEEDED
    coder = accepted.instance_id
    manifest = owned.hub.instances_directory / str(coder) / MANIFEST_NAME
    manifest.write_text(manifest.read_text(encoding="utf-8") + WORKSPACE, encoding="utf-8")
    with sqlite3.connect(owned.hub.directory / "registry.sqlite") as connection:
        connection.execute(
            "INSERT INTO factory_members (instance_id, factory, name, recipe, setup) "
            "VALUES (?, 'tickets', 'coder', 'coder', ?)",
            (str(coder), DeclaredSetup(setup_fields=[]).model_dump_json()),
        )
    return coder


def _handed_in(owned: Owned, coder: UUID, issue: int) -> FactoryRun:
    """A run of the tickets factory the instance handed in before lines, on no line, parked at
    its review."""
    run, _ = owned.hub.registry.open_run(coder, "tickets", {"issue": issue}, "review")
    return owned.hub.registry.move_run(run.run_id, FactoryRunStatus.PARKED, "review")


async def _converted(owned: Owned, instance_id: UUID) -> Line | ErrorEnvelope:
    converted = await owned.client.call(
        INSTANCE_CONVERT, InstanceConvertCommand(instance_id=instance_id)
    )
    assert isinstance(converted, Line | ErrorEnvelope), converted
    return converted


async def _started(owned: Owned, workbench_id: UUID, address: str | None = None) -> None:
    start = await owned.client.call(
        WORKBENCH_START, WorkbenchStartCommand(workbench_id=workbench_id)
    )
    assert isinstance(start, WorkbenchOperationResult), start
    assert (await finished_operation(owned.client, start)).state is OperationState.SUCCEEDED
    owned.runtime.addresses[str(workbench_id)] = address or f"http://kinby-{workbench_id}:8787"


async def _instances(owned: Owned) -> set[UUID]:
    listed = await owned.client.call(INSTANCE_LIST, InstanceListCommand())
    assert isinstance(listed, InstanceListResult), listed
    return {summary.instance_id for summary in listed.instances}


async def _workbenches(owned: Owned) -> set[UUID]:
    listed = await owned.client.call(WORKBENCH_LIST, WorkbenchListCommand())
    assert isinstance(listed, WorkbenchListResult), listed
    return {workbench.workbench_id for workbench in listed.workbenches}


async def _lines(owned: Owned) -> list[Line]:
    listed = await owned.client.call(LINE_LIST, LineListCommand())
    assert isinstance(listed, LineListResult), listed
    return listed.lines


async def _routines(owned: Owned) -> list[str]:
    listed = await call(owned.dispatcher, "routine.list")
    assert isinstance(listed, RoutineListResult), listed
    return [routine.name for routine in listed.routines]


def _graph(owned: Owned, instance_id: UUID) -> GraphStore:
    return GraphStore(owned.hub.instances_directory / str(instance_id))


def _remembered(owned: Owned, instance_id: UUID) -> tuple[NodeId, NodeId]:
    """A fact and an episode the instance remembered."""
    graph = _graph(owned, instance_id)
    fact = graph.remember(
        Fact(
            node=NodeId("2026-11-01-install-lesson"),
            date=date(2026, 11, 1),
            description="Install before the checks",
            subjects=("install",),
            body="Run uv sync before the checks.",
            thread=None,
        )
    )
    episode = graph.remember(
        Episode(
            node=NodeId("2026-11-02-implemented-issue-12"),
            date=date(2026, 11, 2),
            description="Implemented issue 12, install first",
            subjects=("install",),
            body="The checks failed until uv sync ran.",
            recaps=RecappedTurn(thread=uuid4(), turn=uuid4()),
            tools=("bash",),
        )
    )
    return fact, episode


def _recalled(owned: Owned, instance_id: UUID) -> list[NodeId]:
    """The facts and, as a search bounded by date finds them, the episodes about installs."""
    graph = _graph(owned, instance_id)
    return [hit.node for hit in graph.recall("install", after=date(2026, 1, 1))]


async def _signed(url: str, delivery: str) -> int:
    data = json.dumps({"action": "labeled"}).encode()
    signature = hmac.new(SECRET, data, hashlib.sha256).hexdigest()
    async with (
        aiohttp.ClientSession() as session,
        session.post(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Delivery": delivery,
                "X-Hub-Signature-256": f"sha256={signature}",
            },
        ) as response,
    ):
        return response.status


@asynccontextmanager
async def _workbench_served(owned: Owned, workbench_id: UUID) -> AsyncIterator[str]:
    """The workbench's runtime over its directory, in this process."""
    directory = owned.hub.instances_directory / str(workbench_id)
    server = WorkbenchServer(
        Serve("127.0.0.1", 0),
        control_token(owned.hub, str(workbench_id)),
        WorkbenchSteps(directory, owned.hub.directory / "factories"),
    )
    address = await server.start()
    try:
        yield f"http://{address.host}:{address.port}"
    finally:
        await server.stop()


def test_a_converted_instance_is_a_workbench_with_what_it_held_and_a_line_owned_by_home(
    tmp_path, monkeypatch
):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            coder = await _coder(hub)

            line = await _converted(hub, coder)
            workbench = await hub.client.call(
                WORKBENCH_GET, WorkbenchGetCommand(workbench_id=coder)
            )
            picks = await hub.client.call(
                WORKBENCH_PICKS_GET, WorkbenchPicksGetCommand(workbench_id=coder)
            )

            assert isinstance(line, Line), line
            assert (line.factory, line.workbench_id, line.owner_id) == ("tickets", coder, hub.home)
            assert line.model == "openai:gpt-5"
            assert line.intake == "tickets_cec-backend"
            assert await _lines(hub) == [line]
            assert await _routines(hub) == ["tickets_cec-backend"]
            assert isinstance(workbench, Workbench), workbench
            assert workbench.name == "cec-backend"
            assert workbench.recipe == "coder"
            assert workbench.workspace_source == "https://github.com/owner/cec-backend.git"
            assert "GITHUB_TOKEN" in workbench.secrets
            assert workbench.repository_commands == [
                RepositoryCommandList(name="checks", commands=["uv run pytest"])
            ]
            assert isinstance(picks, WorkbenchPicksResult), picks
            assert [pick.name for pick in picks.picks] == ["tdd"]
            assert instance_environment(hub.hub, coder)["GITHUB_TOKEN"] == "ghp-test"
            container = hub.runtime.created[-1]
            assert container.instance_id == str(coder)
            assert container.command[0] == "workbench"
            assert f"kinby-{coder}-workspace" in [item.source for item in container.storage]

    asyncio.run(scenario())


def test_a_converted_instance_is_gone_from_the_instance_list(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            coder = await _coder(hub)
            before = await _instances(hub)

            await _converted(hub, coder)

            assert before == {hub.home, coder}
            assert await _instances(hub) == {hub.home}
            assert await _workbenches(hub) == {coder}

    asyncio.run(scenario())


def test_its_facts_and_episodes_move_to_home_marked_with_the_workbench(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            coder = await _coder(hub)
            fact, episode = _remembered(hub, coder)

            await _converted(hub, coder)

            home = _graph(hub, hub.home)
            assert _recalled(hub, hub.home) == [episode, fact]
            assert [home.open(node).workbench for node in (fact, episode)] == [
                "cec-backend",
                "cec-backend",
            ]
            assert _recalled(hub, coder) == []

    asyncio.run(scenario())


def test_its_threads_and_its_past_runs_still_read_through_the_hub(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            coder = await _coder(hub)
            state = hub.hub.instances_directory / str(coder) / STATE_DIR
            thread = ThreadStore(state).create("Implement issue 12").id
            run = _handed_in(hub, coder, 12)
            await hub.client.call(FACTORY_RUN_CANCEL, FactoryRunCancelCommand(run_id=run.run_id))

            await _converted(hub, coder)
            async with _workbench_served(hub, coder) as address:
                await _started(hub, coder, address)
                threads = await intake_client(hub.hub, hub.home).call(
                    INSTANCE_RELAY_READ,
                    InstanceRelayReadCommand(instance_id=coder, method="thread.list", params={}),
                )
            runs = await hub.client.call(FACTORY_RUN_LIST, FactoryRunListCommand(instance_id=coder))
            past = await detail(hub.hub, run.run_id)

            assert isinstance(threads, InstanceRelayReadResult), threads
            [listed] = ThreadListResult.model_validate(threads.result).threads
            assert (listed.id, listed.title) == (thread, "Implement issue 12")
            assert isinstance(runs, FactoryRunListResult), runs
            assert [(listed.run_id, listed.status) for listed in runs.runs] == [
                (run.run_id, FactoryRunStatus.CANCELLED)
            ]
            assert (past.run.instance_id, past.run.line_id) == (coder, None)
            assert past.run.status is FactoryRunStatus.CANCELLED

    asyncio.run(scenario())


def test_the_stop_of_a_run_the_instance_never_heard_reaches_no_workbench(tmp_path, monkeypatch):
    monkeypatch.setattr("kinby.hub.runs.BACKOFF_SECONDS", 0.01)

    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            coder = await _coder(hub)
            run = _handed_in(hub, coder, 12)
            await hub.client.call(FACTORY_RUN_CANCEL, FactoryRunCancelCommand(run_id=run.run_id))

            await _converted(hub, coder)
            await _started(hub, coder)
            await asyncio.sleep(0.2)

            assert hub.control.stops == []

    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["/instances/{coder}/signals/scan", "/signals/scan"])
def test_a_delivery_to_the_instances_webhook_address_reaches_the_lines_intake(
    tmp_path, monkeypatch, path
):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            coder = await _coder(hub)
            hub.hub.registry.set_signal_alias(coder)
            line = await _converted(hub, coder)
            assert isinstance(line, Line), line
            await _started(hub, coder)
            hub.control.found = [[{"issue": 12}]]

            status = await _signed(f"{hub.address}{path.format(coder=coder)}", "delivery-1")
            await hub.fired()
            runs = await hub.client.call(
                FACTORY_RUN_LIST, FactoryRunListCommand(line_id=line.line_id)
            )

            assert status == 202
            [(endpoint, intake)] = hub.control.intakes
            assert endpoint.address == f"http://kinby-{coder}:8787"
            assert intake.signal["delivery_id"] == "delivery-1"
            assert isinstance(runs, FactoryRunListResult), runs
            assert [run.work_item for run in runs.runs] == [{"issue": 12}]

    asyncio.run(scenario())


def test_converting_an_instance_without_the_delivery_secret_refuses_and_names_it(
    tmp_path, monkeypatch
):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            coder = await _coder(hub, signed=False)

            refused = await _converted(hub, coder)

            assert isinstance(refused, ErrorEnvelope), refused
            assert refused.code is ErrorCode.INVALID_SETUP
            assert refused.fields == {
                "instance_id": 'Instance "cec-backend" lacks what factory "tickets" requires: '
                'secret "WEBHOOK_SECRET".'
            }
            assert await _instances(hub) == {hub.home, coder}
            assert await _lines(hub) == []

    asyncio.run(scenario())


def test_converting_an_instance_with_unfinished_runs_refuses_and_names_them(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            coder = await _coder(hub)
            run = _handed_in(hub, coder, 12)

            refused = await _converted(hub, coder)

            assert isinstance(refused, ErrorEnvelope), refused
            assert refused.code is ErrorCode.INVALID_ARGUMENT
            assert refused.message == (
                f'Instance "cec-backend" has unfinished runs: tickets for issue 12 ({run.run_id}). '
                "Finish or cancel them before converting it."
            )
            assert await _instances(hub) == {hub.home, coder}
            assert await _lines(hub) == []

    asyncio.run(scenario())


def test_converting_an_instance_that_owns_a_line_refuses_and_names_it(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            coder = await _coder(hub)
            write_factory(
                hub.hub.directory / "factories" / "reviews",
                {
                    "factory.yaml": "name: reviews\nrequires: { image: coder }\n"
                    "work_item: { issue: int }\n"
                    "steps:\n  - { id: review, kind: approve, summary: Review the issue. }\n"
                },
            )
            landing = (await created(hub.client, LANDING)).workbench_id
            set_up = await hub.client.call(
                LINE_SETUP,
                LineSetupCommand(factory="reviews", workbench_id=landing, owner_id=coder),
            )
            assert isinstance(set_up, LineSetupResult), set_up

            refused = await _converted(hub, coder)

            assert isinstance(refused, ErrorEnvelope), refused
            assert refused.code is ErrorCode.INVALID_ARGUMENT
            assert refused.message == (
                'Instance "cec-backend" owns the lines "reviews" on "landing". '
                "Give them another owner before converting it."
            )
            assert await _lines(hub) == [set_up.line]

    asyncio.run(scenario())


async def _unchanged(owned: Owned, coder: UUID, memory: tuple[NodeId, NodeId]) -> None:
    """The instance as it was before a conversion that failed."""
    fact, episode = memory
    assert await _instances(owned) == {owned.home, coder}
    assert await _workbenches(owned) == set()
    assert await _lines(owned) == []
    assert _recalled(owned, coder) == [episode, fact]
    assert _recalled(owned, owned.home) == []
    record = owned.hub.registry.instance(coder)
    assert record is not None
    assert record.factory is not None
    assert [item.destination for item in record.storage] == ["/instance", "/instance/workspace"]


def test_a_conversion_whose_owner_refuses_the_intake_leaves_the_instance_as_it_was(
    tmp_path, monkeypatch
):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            coder = await _coder(hub)
            memory = _remembered(hub, coder)
            del hub.runtime.addresses[str(hub.home)]

            refused = await _converted(hub, coder)

            assert isinstance(refused, ErrorEnvelope), refused
            assert refused.code is ErrorCode.INVALID_SETUP
            await _unchanged(hub, coder, memory)

    asyncio.run(scenario())


@pytest.mark.parametrize("made", [False, True], ids=["refused", "made-then-raised"])
def test_a_conversion_whose_workbench_container_fails_leaves_the_instance_as_it_was(
    tmp_path, monkeypatch, made
):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            coder = await _coder(hub)
            memory = _remembered(hub, coder)
            environment = instance_environment(hub.hub, coder)
            create = hub.runtime.create

            async def no_workbench(spec):
                """As Docker: a name in use is refused, and a create can raise after it made the
                container."""
                if spec.instance_id in hub.runtime.states:
                    raise RuntimeError(f'The container name "{spec.instance_id}" is in use.')
                if spec.command[0] == "workbench":
                    if made:
                        await create(spec)
                    raise RuntimeError("Docker refused the container.")
                await create(spec)

            monkeypatch.setattr(hub.runtime, "create", no_workbench)

            refused = await _converted(hub, coder)

            assert isinstance(refused, ErrorEnvelope), refused
            assert "Docker refused the container." in refused.message
            await _unchanged(hub, coder, memory)
            assert await _routines(hub) == []
            assert instance_environment(hub.hub, coder) == environment
            assert hub.runtime.created[-1].command == ("serve",)
            assert str(coder) in hub.runtime.states

    asyncio.run(scenario())
