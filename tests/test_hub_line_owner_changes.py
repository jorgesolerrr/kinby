"""A line changes owner, and its intake routine and the memory marked with its workbench go with
it, through the hub contract."""

import asyncio
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from uuid import UUID

import aiohttp
import pytest

from kinby.cli.client import ContractClient
from kinby.contracts import (
    FACTORY_RUN_APPROVE,
    LINE_GET,
    LINE_OWNED_HAND_IN,
    LINE_OWNER_SET,
    ErrorCode,
    ErrorEnvelope,
    FactoryRun,
    FactoryRunApproveCommand,
    FactoryRunStatus,
    Line,
    LineGetCommand,
    LineHandInCommand,
    LineOwnerSetCommand,
    RoutineListResult,
)
from kinby.core.dispatcher import (
    ScheduledDispatcher,
    ScheduledTurnConfig,
    TurnConfig,
    build_dispatcher,
)
from kinby.core.events import EventLog
from kinby.core.scheduler import SchedulerConfig
from kinby.core.turn_runner import LangGraphRunner
from kinby.hub import Hub
from kinby.memory import Fact, GraphStore, NodeId
from tests.test_contract_server import served as instance_served
from tests.test_factory_runs import detail, intake_client, settled, started, told
from tests.test_factory_waits import awaiting_approval
from tests.test_hub import FakeControl, FakeRuntime, created_instance, hub_client
from tests.test_hub_line_intake import SECRET, Owned, owned
from tests.test_hub_line_intake import _line as _intake_line
from tests.test_hub_line_runs import _handed_in, _line
from tests.test_hub_line_stops import APPROVE, REVIEWS, _beside, _hub
from tests.test_hub_lines import _home
from tests.test_hub_routing import control_token
from tests.test_routines import RoutineModel, instance_at
from tests.test_scheduler import call


def _graph(hub: Hub, instance_id: UUID) -> GraphStore:
    return GraphStore(hub.instances_directory / str(instance_id))


def _lessons(hub: Hub, instance_id: UUID) -> dict[str | None, NodeId]:
    """One lesson in the instance's memory marked with each of two workbenches, and one
    unmarked."""
    return {
        workbench: _graph(hub, instance_id).remember(
            Fact(
                node=NodeId(f"2026-11-0{day}-install-lesson"),
                date=date(2026, 11, day),
                description=f"Install lesson {day}",
                subjects=("install",),
                body="Install before the checks.",
                thread=None,
                workbench=workbench,
            )
        )
        for day, workbench in ((1, "cec-backend"), (2, "landing"), (3, None))
    }


def _recalled(hub: Hub, instance_id: UUID) -> list[NodeId]:
    return [hit.node for hit in _graph(hub, instance_id).recall("install")]


async def _teammate(client: ContractClient, runtime: FakeRuntime) -> UUID:
    """A second instance, started."""
    return await started(client, runtime, await created_instance(client, name="teammate"))


async def _owner_set(client: ContractClient, line: Line, owner_id: UUID) -> Line | ErrorEnvelope:
    changed = await client.call(
        LINE_OWNER_SET, LineOwnerSetCommand(line_id=line.line_id, owner_id=owner_id)
    )
    assert isinstance(changed, Line | ErrorEnvelope), changed
    return changed


async def _owner(client: ContractClient, line: Line) -> UUID:
    held = await client.call(LINE_GET, LineGetCommand(line_id=line.line_id))
    assert isinstance(held, Line), held
    return held.owner_id


def test_the_new_owner_gets_the_lines_run_stops_and_hands_it_work_and_the_old_owner_does_not(
    tmp_path,
):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, reviews=REVIEWS.replace("{park}", APPROVE))
    client = hub_client(hub)

    async def scenario() -> None:
        home = await _home(hub)
        teammate = await _teammate(client, runtime)
        line = await _line(client, runtime, factory="reviews")
        run = await _handed_in(client, line, 12)
        await awaiting_approval(hub, run.run_id)

        changed = await _owner_set(client, line, teammate)
        await client.call(FACTORY_RUN_APPROVE, FactoryRunApproveCommand(run_id=run.run_id))
        await settled(hub, run.run_id)
        [stop] = await told(control, 1)
        handed = [
            await intake_client(hub, instance_id).call(
                LINE_OWNED_HAND_IN, LineHandInCommand(line_id=line.line_id, work_item={"issue": 13})
            )
            for instance_id in (home, teammate)
        ]

        assert isinstance(changed, Line), changed
        assert changed.owner_id == teammate
        assert (stop.run_id, stop.status) == (run.run_id, FactoryRunStatus.DONE)
        assert [endpoint.address for endpoint, _ in control.stops] == [
            f"http://kinby-{teammate}:8787"
        ]
        refused, accepted = handed
        assert isinstance(refused, ErrorEnvelope), refused
        assert refused.code is ErrorCode.PERMISSION_DENIED
        assert isinstance(accepted, FactoryRun), accepted
        assert (await detail(hub, run.run_id)).run.instance_id == teammate

    asyncio.run(scenario())


def test_the_memory_marked_with_the_lines_workbench_moves_to_the_new_owner(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        home = await _home(hub)
        teammate = await _teammate(client, runtime)
        line = await _line(client, runtime)
        lessons = _lessons(hub, home)

        await _owner_set(client, line, teammate)

        assert _recalled(hub, teammate) == [lessons["cec-backend"]]
        assert _recalled(hub, home) == [lessons[None], lessons["landing"]]

    asyncio.run(scenario())


def test_an_owner_keeping_another_line_on_the_workbench_keeps_a_copy_of_its_memory(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, reviews=REVIEWS.replace("{park}", APPROVE))
    client = hub_client(hub)

    async def scenario() -> None:
        home = await _home(hub)
        teammate = await _teammate(client, runtime)
        line = await _line(client, runtime)
        await _beside(hub, line, "reviews")
        lessons = _lessons(hub, home)

        await _owner_set(client, line, teammate)

        assert _recalled(hub, teammate) == [lessons["cec-backend"]]
        assert _recalled(hub, home) == [lessons[None], lessons["landing"], lessons["cec-backend"]]

    asyncio.run(scenario())


def test_the_change_refuses_a_line_with_a_step_running(tmp_path):
    control = FakeControl()
    control.step_release.clear()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        home = await _home(hub)
        teammate = await _teammate(client, runtime)
        line = await _line(client, runtime)
        lessons = _lessons(hub, home)
        run = await _handed_in(client, line, 12)
        async with asyncio.timeout(5):
            while not control.steps:
                await asyncio.sleep(0.01)

        refused = await _owner_set(client, line, teammate)
        control.step_release.set()

        assert isinstance(refused, ErrorEnvelope), refused
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert refused.message == (
            f'Factory run "{run.run_id}" of the line is running step "test". '
            "Change the owner once the step ends."
        )
        assert await _owner(client, line) == home
        assert _recalled(hub, teammate) == []
        assert lessons["cec-backend"] in _recalled(hub, home)

    asyncio.run(scenario())


@pytest.mark.parametrize("owner", ["workbench", "missing"])
def test_the_change_refuses_an_owner_that_is_no_instance(tmp_path, owner):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        home = await _home(hub)
        line = await _line(client, runtime)
        named = line.workbench_id if owner == "workbench" else UUID(int=7)

        refused = await _owner_set(client, line, named)

        assert isinstance(refused, ErrorEnvelope), refused
        assert refused.code is ErrorCode.INVALID_SETUP
        assert refused.fields == {"owner_id": f'Instance "{named}" was not found.'}
        assert await _owner(client, line) == home

    asyncio.run(scenario())


@asynccontextmanager
async def _served_teammate(
    hub: Owned, tmp_path: Path
) -> AsyncIterator[tuple[UUID, ScheduledDispatcher]]:
    """A second instance running in this process, which a delivery and the relay reach."""
    teammate = await _teammate(hub.client, hub.runtime)
    (tmp_path / "teammate").mkdir()
    instance = instance_at(tmp_path / "teammate")
    log = EventLog(instance.manifest.state_dir, clock=hub.clock)
    runner = LangGraphRunner(instance, event_log=log, model_factory=lambda _: RoutineModel())
    dispatcher = build_dispatcher(
        instance.manifest.state_dir,
        event_log=log,
        turns=ScheduledTurnConfig(
            TurnConfig(runner.prepare_for_turn, runner.permission_ceiling, runner),
            SchedulerConfig(instance, hub.clock),
        ),
    )
    token = control_token(hub.hub, str(teammate))
    async with instance_served(instance, dispatcher, token=token) as own:
        hub.runtime.addresses[str(teammate)] = f"http://{own.host}:{own.port}"
        yield teammate, dispatcher


async def _routine_names(dispatcher: ScheduledDispatcher) -> list[str]:
    listed = await call(dispatcher, "routine.list")
    assert isinstance(listed, RoutineListResult), listed
    return [routine.name for routine in listed.routines]


async def _delivered(hub: Owned, line: Line, delivery: str) -> int:
    """Post a signed delivery to the line's own address on the hub."""
    data = json.dumps({"action": "labeled"}).encode()
    signature = hmac.new(SECRET, data, hashlib.sha256).hexdigest()
    async with (
        aiohttp.ClientSession() as session,
        session.post(
            f"{hub.address}/lines/{line.line_id}/signals",
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Delivery": delivery,
                "X-Hub-Signature-256": f"sha256={signature}",
            },
        ) as response,
    ):
        return response.status


def test_the_intake_routine_moves_to_the_new_owner_and_keeps_its_delivery_address(
    tmp_path, monkeypatch
):
    async def scenario() -> None:
        async with (
            owned(tmp_path, monkeypatch) as hub,
            _served_teammate(hub, tmp_path) as (teammate, dispatcher),
        ):
            line = await _intake_line(hub)
            before = await _delivered(hub, line, "delivery-1")
            await hub.fired()

            changed = await _owner_set(hub.client, line, teammate)
            after = await _delivered(hub, line, "delivery-2")

            assert isinstance(changed, Line), changed
            assert (before, after) == (202, 202)
            assert await _routine_names(hub.dispatcher) == []
            assert await _routine_names(dispatcher) == ["tickets-cec-backend"]

    asyncio.run(scenario())


def test_a_change_that_fails_part_way_leaves_the_line_its_routine_and_memory_with_the_old_owner(
    tmp_path, monkeypatch
):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            teammate = await _teammate(hub.client, hub.runtime)
            del hub.runtime.addresses[str(teammate)]
            line = await _intake_line(hub)
            lessons = _lessons(hub.hub, hub.home)

            refused = await _owner_set(hub.client, line, teammate)

            assert isinstance(refused, ErrorEnvelope), refused
            assert refused.code is ErrorCode.INVALID_SETUP
            assert refused.fields["owner_id"].startswith(
                'Instance "teammate" did not take the intake routine "tickets-cec-backend": '
            )
            assert await _owner(hub.client, line) == hub.home
            assert await _routine_names(hub.dispatcher) == ["tickets-cec-backend"]
            assert _recalled(hub.hub, hub.home) == [
                lessons[None],
                lessons["landing"],
                lessons["cec-backend"],
            ]
            assert _recalled(hub.hub, teammate) == []

    asyncio.run(scenario())


def test_an_old_owner_that_cannot_delete_the_routine_keeps_the_line_and_the_new_owner_gets_none(
    tmp_path, monkeypatch
):
    async def scenario() -> None:
        async with (
            owned(tmp_path, monkeypatch) as hub,
            _served_teammate(hub, tmp_path) as (teammate, dispatcher),
        ):
            line = await _intake_line(hub)
            lessons = _lessons(hub.hub, hub.home)
            await _delivered(hub, line, "delivery-1")

            refused = await _owner_set(hub.client, line, teammate)

            assert isinstance(refused, ErrorEnvelope), refused
            assert refused.code is ErrorCode.ROUTINE_PENDING
            assert await _owner(hub.client, line) == hub.home
            assert await _routine_names(hub.dispatcher) == ["tickets-cec-backend"]
            assert await _routine_names(dispatcher) == []
            assert lessons["cec-backend"] in _recalled(hub.hub, hub.home)
            assert _recalled(hub.hub, teammate) == []

    asyncio.run(scenario())
