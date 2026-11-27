"""A workbench searches and opens the memory of the owner of its run's line on its hub route,
which the hub relays to that owner: only the nodes marked with the workbench and the unmarked, each
recall recorded under the run (ADR 0134)."""

import asyncio
from datetime import date
from uuid import UUID, uuid4

import pytest

from kinby.contracts import (
    LINE_HAND_IN,
    LINE_SETUP,
    WORKBENCH_MEMORY_OPEN,
    WORKBENCH_MEMORY_SEARCH,
    WORKBENCH_RECREATE,
    WORKBENCH_START,
    ErrorCode,
    ErrorEnvelope,
    FactoryRun,
    LineHandInCommand,
    LineSetupCommand,
    LineSetupResult,
    MemoryOpenResult,
    MemoryRecalled,
    MemorySearchResult,
    NodeId,
    OperationState,
    Scope,
    WorkbenchMemoryOpenCommand,
    WorkbenchMemorySearchCommand,
    WorkbenchRecreateCommand,
    WorkbenchStartCommand,
)
from kinby.core.events import EventLog
from kinby.instance import Instance
from kinby.memory import GraphStore
from kinby.memory.facade import Fact, new_node_id
from kinby.plugins.hub import HUB_SCOPES_VARIABLE, HUB_URL_VARIABLE
from tests.helpers import every_event
from tests.test_contract_server import served as instance_served
from tests.test_factory_runs import factory_hub, intake_client, settled, started
from tests.test_hub import FakeControl, FakeRuntime, created_instance, hub_client
from tests.test_hub_factories import write_factory
from tests.test_hub_line_intake import LANDING, Owned, _started_workbench, owned
from tests.test_hub_routing import control_token
from tests.test_hub_workbenches import CEC_BACKEND, created, finished
from tests.test_routines import instance_at
from tests.test_scheduler import runtime

CHORES = """\
name: chores
requires: { image: coder }
work_item: { task: str }
steps:
  - { id: draft, kind: command, run: ["make draft"] }
"""


def _lessons(instance: Instance, *workbenches: str | None) -> dict[str | None, NodeId]:
    """An install lesson in *instance*'s memory marked with each of *workbenches*."""
    graph = GraphStore(instance.path)
    return {
        workbench: graph.remember(
            Fact(
                node=new_node_id(date(2026, 11, 1), f"Install on {workbench or 'any'}"),
                date=date(2026, 11, 1),
                description=f"Install on {workbench or 'any workbench'}",
                subjects=("install",),
                body="Install before the checks.",
                thread=None,
                workbench=workbench,
            )
        )
        for workbench in workbenches
    }


async def _run(
    owned: Owned, workbench_id: UUID, factory: str = "chores", owner_id: UUID | None = None
) -> FactoryRun:
    """A settled run of a line of *factory* on the workbench, owned by *owner_id* or the home."""
    set_up = await owned.client.call(
        LINE_SETUP, LineSetupCommand(factory=factory, workbench_id=workbench_id, owner_id=owner_id)
    )
    assert isinstance(set_up, LineSetupResult), set_up
    run = await owned.client.call(
        LINE_HAND_IN, LineHandInCommand(line_id=set_up.line.line_id, work_item={"task": "jobs"})
    )
    assert isinstance(run, FactoryRun), run
    await settled(owned.hub, run.run_id)
    return run


def _search(run: FactoryRun, query: str = "install") -> WorkbenchMemorySearchCommand:
    return WorkbenchMemorySearchCommand(run_id=run.run_id, step="draft", attempt=1, query=query)


def _open(run: FactoryRun, node: NodeId) -> WorkbenchMemoryOpenCommand:
    return WorkbenchMemoryOpenCommand(run_id=run.run_id, step="draft", attempt=1, node=node)


@pytest.fixture(autouse=True)
def _chores(tmp_path) -> None:
    write_factory(tmp_path / "hub" / "factories" / "chores", {"factory.yaml": CHORES})


def test_every_workbench_holds_the_memory_scope_and_its_containers_get_the_route(tmp_path):
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", FakeControl(), runtime)
    client = hub_client(hub)

    async def scenario() -> UUID:
        workbench_id = (await created(client, CEC_BACKEND)).workbench_id
        start = await client.call(WORKBENCH_START, WorkbenchStartCommand(workbench_id=workbench_id))
        assert (await finished(client, start)).state is OperationState.SUCCEEDED
        recreate = await client.call(
            WORKBENCH_RECREATE, WorkbenchRecreateCommand(workbench_id=workbench_id)
        )
        assert (await finished(client, recreate)).state is OperationState.SUCCEEDED
        return workbench_id

    workbench_id = asyncio.run(scenario())

    assert hub.instance_scopes(workbench_id) == {Scope.WORKBENCH_MEMORY}
    assert [
        (spec.env.get(HUB_URL_VARIABLE), spec.env.get(HUB_SCOPES_VARIABLE))
        for spec in runtime.created
    ] == [(f"http://hub:8080/instances/{workbench_id}/hub", "workbench:memory")] * 2


def test_a_workbench_reads_its_owners_memory_marked_with_it_and_unmarked_and_recalls_it(
    tmp_path, monkeypatch
):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            lessons = _lessons(hub.instance, "cec-backend", "landing", None)
            workbench_id = await _started_workbench(hub)
            run = await _run(hub, workbench_id)
            workbench = intake_client(hub.hub, workbench_id)

            searched = await workbench.call(WORKBENCH_MEMORY_SEARCH, _search(run))
            opened = await workbench.call(WORKBENCH_MEMORY_OPEN, _open(run, lessons[None]))
            sibling = await workbench.call(WORKBENCH_MEMORY_OPEN, _open(run, lessons["landing"]))

            assert isinstance(searched, MemorySearchResult), searched
            assert {hit.node for hit in searched.hits} == {lessons["cec-backend"], lessons[None]}
            assert isinstance(opened, MemoryOpenResult), opened
            assert opened.body == "Install before the checks."
            assert isinstance(sibling, ErrorEnvelope), sibling
            assert sibling.code is ErrorCode.PERMISSION_DENIED
            assert [
                (event.thread_id, set(event.payload.nodes), event.payload.step)
                for event in every_event(EventLog(hub.instance.manifest.state_dir))
                if isinstance(event.payload, MemoryRecalled)
            ] == [
                (run.run_id, {lessons["cec-backend"], lessons[None]}, "draft"),
                (run.run_id, {lessons[None]}, "draft"),
            ]

    asyncio.run(scenario())


def test_the_hub_refuses_an_unknown_run_and_a_run_of_another_workbench(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            [lesson] = _lessons(hub.instance, None).values()
            workbench_id = await _started_workbench(hub)
            landing_run = await _run(hub, await _started_workbench(hub, LANDING))
            unknown = landing_run.model_copy(update={"run_id": uuid4()})
            workbench = intake_client(hub.hub, workbench_id)

            refused = []
            for run in (landing_run, unknown):
                refused.append(await workbench.call(WORKBENCH_MEMORY_SEARCH, _search(run)))
                refused.append(await workbench.call(WORKBENCH_MEMORY_OPEN, _open(run, lesson)))

            assert [isinstance(answer, ErrorEnvelope) and answer.code for answer in refused] == [
                ErrorCode.PERMISSION_DENIED
            ] * 4

    asyncio.run(scenario())


def test_each_lookup_goes_to_the_owner_of_its_runs_line(tmp_path, monkeypatch):
    write_factory(
        tmp_path / "hub" / "factories" / "errands",
        {"factory.yaml": CHORES.replace("name: chores", "name: errands")},
    )

    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            bob_id = await started(hub.client, hub.runtime, await created_instance(hub.client))
            (tmp_path / "bob").mkdir()
            bob = instance_at(tmp_path / "bob")
            [homes] = _lessons(hub.instance, None).values()
            [bobs] = _lessons(bob, None).values()
            workbench_id = await _started_workbench(hub)
            home_run = await _run(hub, workbench_id)
            bob_run = await _run(hub, workbench_id, "errands", bob_id)
            workbench = intake_client(hub.hub, workbench_id)
            bobs_runtime = runtime(bob, hub.clock)

            async with instance_served(
                bob, bobs_runtime, token=control_token(hub.hub, str(bob_id))
            ) as served:
                hub.runtime.addresses[str(bob_id)] = f"http://{served.host}:{served.port}"
                found = {
                    run.run_id: await workbench.call(WORKBENCH_MEMORY_SEARCH, _search(run))
                    for run in (home_run, bob_run)
                }

            assert {
                run_id: [hit.node for hit in searched.hits]
                for run_id, searched in found.items()
                if isinstance(searched, MemorySearchResult)
            } == {home_run.run_id: [homes], bob_run.run_id: [bobs]}

    asyncio.run(scenario())


@pytest.mark.parametrize("owner", ["stopped", "unreachable"])
def test_a_lookup_whose_owner_is_stopped_or_unreachable_is_unavailable(
    tmp_path, monkeypatch, owner
):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            workbench_id = await _started_workbench(hub)
            run = await _run(hub, workbench_id)
            if owner == "stopped":
                del hub.runtime.addresses[str(hub.home)]
            else:
                hub.runtime.addresses[str(hub.home)] = "http://127.0.0.1:9"

            unavailable = await intake_client(hub.hub, workbench_id).call(
                WORKBENCH_MEMORY_SEARCH, _search(run)
            )

            assert isinstance(unavailable, ErrorEnvelope), unavailable
            assert unavailable.retryable
            assert unavailable.message.startswith('The memory of "home", which owns the line')

    asyncio.run(scenario())
