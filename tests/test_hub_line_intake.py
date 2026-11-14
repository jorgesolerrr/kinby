"""Setting up a line of a factory that declares an intake creates its intake routine in the owner,
whose code step runs the factory's tool on the line's workbench, through the hub contract."""

import asyncio
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import aiohttp
import pytest

from kinby.cli.client import ContractClient
from kinby.contracts import (
    FACTORY_RUN_LIST,
    LINE_INTAKE,
    LINE_REMOVE,
    LINE_SETUP,
    WORKBENCH_START,
    ErrorCode,
    ErrorEnvelope,
    FactoryRun,
    FactoryRunListCommand,
    FactoryRunListResult,
    Line,
    LineIntakeCommand,
    LineRemoveCommand,
    LineSetupCommand,
    OperationState,
    RoutineListResult,
    RoutineSummary,
    WorkbenchCreateCommand,
    WorkbenchStartCommand,
)
from kinby.core.dispatcher import (
    ScheduledDispatcher,
    ScheduledTurnConfig,
    TurnConfig,
    build_dispatcher,
)
from kinby.core.events import EventLog
from kinby.core.scheduler import SchedulerConfig
from kinby.core.turn_runner import ChatModel, LangGraphRunner
from kinby.hub import Hub
from kinby.instance import Instance
from tests.test_contract_server import served as instance_served
from tests.test_factory_runs import intake_client, started
from tests.test_hub import (
    FakeControl,
    FakeRuntime,
    created_instance,
    hub_at,
    hub_client,
    instance_environment,
)
from tests.test_hub_factories import write_factory
from tests.test_hub_routing import control_token
from tests.test_hub_server import served, url
from tests.test_hub_workbenches import CEC_BACKEND, created, finished
from tests.test_routines import RoutineModel, instance_at
from tests.test_scheduler import FakeClock, call

TICKETS = {
    "factory.yaml": """\
name: tickets
requires:
  image: coder
  secrets:
    - { name: GH_TOKEN, label: GitHub token, description: Reads the issues., type: text,
        required: true }
intake:
  call: scan_issues
  schedule: 0 9 * * *
  delivery: { auth: hmac-sha256, secret: WEBHOOK_SECRET, signature_header: X-Hub-Signature-256,
              delivery_header: X-GitHub-Delivery }
work_item: { issue: int }
steps:
  - { id: review, kind: approve, summary: Review the issue. }
""",
    "tools/scan.py": """\
from kinby.plugins.tools import tool


@tool(write=True)
def scan_issues(signal: dict[str, object]) -> list[dict[str, int]]:
    \"\"\"Find the issues labeled ready.\"\"\"
    return []
""",
}
LANDING = CEC_BACKEND.model_copy(
    update={"name": "landing", "workspace_source": "https://github.com/owner/landing.git"}
)
SECRET = b"webhook-secret"
BEFORE_NINE = datetime(2026, 11, 13, 8, 59, tzinfo=UTC)


@dataclass(frozen=True)
class Owned:
    """A served hub with the tickets factory, and its home instance running in this process."""

    hub: Hub
    runtime: FakeRuntime
    control: FakeControl
    address: str
    home: UUID
    instance: Instance
    dispatcher: ScheduledDispatcher
    clock: FakeClock

    @property
    def client(self) -> ContractClient:
        return hub_client(self.hub)

    async def fired(self) -> None:
        """Let the home instance's scheduler start every routine due, and wait for its turns."""
        await self.dispatcher.scheduler.tick()
        await self.dispatcher.scheduler.drain()


@asynccontextmanager
async def owned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: ChatModel | None = None
) -> AsyncIterator[Owned]:
    """The served hub, whose home instance answers with *model*, or says it is done."""
    control = FakeControl()
    runtime = FakeRuntime()
    write_factory(tmp_path / "hub" / "factories" / "tickets", TICKETS)
    hub = hub_at(
        tmp_path / "hub", runtime=runtime, control=control, shipped_factories=tmp_path / "shipped"
    )
    recovery = await hub.recover()
    assert recovery.home is not None
    home = await started(hub_client(hub), runtime, recovery.home)
    (tmp_path / "home").mkdir()
    instance = instance_at(tmp_path / "home")
    clock = FakeClock(BEFORE_NINE)
    log = EventLog(instance.manifest.state_dir, clock=clock)
    answering = model or RoutineModel()
    runner = LangGraphRunner(instance, event_log=log, model_factory=lambda _: answering)
    dispatcher = build_dispatcher(
        instance.manifest.state_dir,
        event_log=log,
        turns=ScheduledTurnConfig(
            TurnConfig(runner.prepare_for_turn, runner.permission_ceiling, runner),
            SchedulerConfig(instance, clock),
        ),
    )
    token = control_token(hub, str(home))
    monkeypatch.setenv("WEBHOOK_SECRET", SECRET.decode())
    monkeypatch.setenv("KINBY_CONTROL_TOKEN", token)
    monkeypatch.setenv("KINBY_HUB_SCOPES", ",".join(hub.instance_scopes(home)))
    async with served(hub) as address, instance_served(instance, dispatcher, token=token) as own:
        monkeypatch.setenv("KINBY_HUB_URL", url(address, f"/instances/{home}/hub"))
        runtime.addresses[str(home)] = f"http://{own.host}:{own.port}"
        yield Owned(hub, runtime, control, url(address, ""), home, instance, dispatcher, clock)
    hub.close()


async def _started_workbench(owned: Owned, workbench: WorkbenchCreateCommand = CEC_BACKEND) -> UUID:
    workbench_id = (await created(owned.client, workbench)).workbench_id
    start = await owned.client.call(
        WORKBENCH_START, WorkbenchStartCommand(workbench_id=workbench_id)
    )
    assert (await finished(owned.client, start)).state is OperationState.SUCCEEDED
    owned.runtime.addresses[str(workbench_id)] = f"http://kinby-{workbench_id}:8787"
    return workbench_id


async def _line(owned: Owned, workbench: WorkbenchCreateCommand = CEC_BACKEND) -> Line:
    workbench_id = await _started_workbench(owned, workbench)
    line = await owned.client.call(
        LINE_SETUP, LineSetupCommand(factory="tickets", workbench_id=workbench_id)
    )
    assert isinstance(line, Line), line
    return line


async def _routines(owned: Owned) -> list[RoutineSummary]:
    listed = await call(owned.dispatcher, "routine.list")
    assert isinstance(listed, RoutineListResult), listed
    return listed.routines


async def _runs(owned: Owned, line: Line) -> list[FactoryRun]:
    listed = await owned.client.call(
        FACTORY_RUN_LIST, FactoryRunListCommand(factory="tickets", line_id=line.line_id)
    )
    assert isinstance(listed, FactoryRunListResult), listed
    return listed.runs


async def _delivered(owned: Owned, line: Line, body: dict[str, object]) -> int:
    """Post a signed delivery to the line's address on the hub."""
    data = json.dumps(body).encode()
    signature = hmac.new(SECRET, data, hashlib.sha256).hexdigest()
    async with (
        aiohttp.ClientSession() as session,
        session.post(
            f"{owned.address}/instances/{line.owner_id}/signals/{line.intake}",
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Delivery": "delivery-1",
                "X-Hub-Signature-256": f"sha256={signature}",
            },
        ) as response,
    ):
        return response.status


def test_setting_up_a_line_creates_its_intake_routine_in_the_owner(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            line = await _line(hub)
            [routine] = await _routines(hub)

            assert line.owner_id == hub.home
            assert line.intake == "tickets-cec-backend"
            assert routine.name == line.intake
            assert routine.schedule == "0 9 * * *"
            assert routine.code_step == "run_line_intake"
            assert routine.signal is not None
            assert routine.signal.path == f"/signals/{line.intake}"

    asyncio.run(scenario())


def test_a_delivery_to_the_lines_address_runs_its_intake_on_the_workbench_and_starts_runs(
    tmp_path, monkeypatch
):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            line = await _line(hub)
            hub.control.found = [[{"issue": 12}, {"issue": 13}]]

            status = await _delivered(hub, line, {"action": "labeled"})
            await hub.fired()
            runs = await _runs(hub, line)

            assert status == 202
            [(endpoint, intake)] = hub.control.intakes
            assert endpoint.address == f"http://kinby-{line.workbench_id}:8787"
            assert (intake.factory, intake.call) == ("tickets", "scan_issues")
            assert intake.signal["body"] == {"action": "labeled"}
            assert intake.signal["delivery_id"] == "delivery-1"
            assert [run.work_item for run in runs] == [{"issue": 12}, {"issue": 13}]

    asyncio.run(scenario())


def test_a_scheduled_intake_fires_on_its_schedule(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            line = await _line(hub)
            hub.control.found = [[{"issue": 7}]]

            await hub.fired()
            before = list(hub.control.intakes)
            hub.clock.now = datetime(2026, 11, 13, 9, 1, tzinfo=UTC)
            await hub.fired()

            assert before == []
            assert [intake.signal for _, intake in hub.control.intakes] == [{}]
            assert [run.work_item for run in await _runs(hub, line)] == [{"issue": 7}]

    asyncio.run(scenario())


def test_two_lines_of_one_factory_have_different_delivery_addresses(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            backend = await _line(hub)
            landing = await _line(hub, LANDING)
            hub.control.found = [[{"issue": 4}]]

            await _delivered(hub, landing, {"action": "labeled"})
            await hub.fired()

            assert [routine.name for routine in await _routines(hub)] == [
                "tickets-cec-backend",
                "tickets-landing",
            ]
            assert [endpoint.address for endpoint, _ in hub.control.intakes] == [
                f"http://kinby-{landing.workbench_id}:8787"
            ]
            assert await _runs(hub, backend) == []
            assert [run.work_item for run in await _runs(hub, landing)] == [{"issue": 4}]

    asyncio.run(scenario())


def test_the_owners_container_never_receives_the_workbenchs_secrets(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            line = await _line(hub)
            await _delivered(hub, line, {"action": "labeled"})
            await hub.fired()

            assert "GH_TOKEN" not in instance_environment(hub.hub, hub.home)
            owner = [spec for spec in hub.runtime.created if spec.instance_id == str(hub.home)]
            assert owner
            assert all("GH_TOKEN" not in spec.env for spec in owner)

    asyncio.run(scenario())


def test_removing_the_line_removes_its_intake_routine(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            line = await _line(hub)

            removed = await hub.client.call(LINE_REMOVE, LineRemoveCommand(line_id=line.line_id))

            assert not isinstance(removed, ErrorEnvelope), removed
            assert await _routines(hub) == []

    asyncio.run(scenario())


def test_a_line_is_not_set_up_when_its_owner_cannot_take_the_intake_routine(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            monkeypatch.delenv("WEBHOOK_SECRET")
            workbench_id = await _started_workbench(hub)

            refused = await hub.client.call(
                LINE_SETUP, LineSetupCommand(factory="tickets", workbench_id=workbench_id)
            )

            assert isinstance(refused, ErrorEnvelope), refused
            assert refused.code is ErrorCode.INVALID_SETUP
            assert refused.fields["owner_id"].endswith("Signal secret WEBHOOK_SECRET is unset.")
            assert await _routines(hub) == []

    asyncio.run(scenario())


def test_the_hub_refuses_an_intake_from_a_routine_that_is_no_intake_of_the_instances_lines(
    tmp_path, monkeypatch
):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            line = await _line(hub)
            assert line.intake is not None
            other = (await created_instance(hub.client)).instance_id

            answers = [
                await intake_client(hub.hub, instance_id).call(
                    LINE_INTAKE, LineIntakeCommand(routine=routine)
                )
                for instance_id, routine in ((hub.home, "news"), (other, line.intake))
            ]

            for answer in answers:
                assert isinstance(answer, ErrorEnvelope), answer
                assert answer.code is ErrorCode.PERMISSION_DENIED
            assert hub.control.intakes == []

    asyncio.run(scenario())


def test_an_intake_the_workbench_did_not_run_hands_the_line_no_work(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            line = await _line(hub)
            assert line.intake is not None
            del hub.runtime.addresses[str(line.workbench_id)]

            answer = await intake_client(hub.hub, hub.home).call(
                LINE_INTAKE, LineIntakeCommand(routine=line.intake)
            )

            assert isinstance(answer, ErrorEnvelope), answer
            assert answer.message.startswith(
                'The intake of line "tickets" did not run on workbench "cec-backend": '
            )
            assert await _runs(hub, line) == []

    asyncio.run(scenario())
