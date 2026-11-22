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
from aiohttp import web
from pydantic import SecretStr

from kinby.cli.client import ContractClient
from kinby.contracts import (
    FACTORY_RUN_LIST,
    LINE_INTAKE,
    LINE_REMOVE,
    LINE_SETUP,
    WORKBENCH_LIST,
    WORKBENCH_START,
    AcceptedResult,
    ErrorCode,
    ErrorEnvelope,
    FactoryRun,
    FactoryRunListCommand,
    FactoryRunListResult,
    IntakeRunCommand,
    Line,
    LineIntakeCommand,
    LineRemoveCommand,
    LineSetupCommand,
    LineSetupResult,
    NewWorkbench,
    OperationState,
    RoutineListResult,
    RoutineRunOutcome,
    RoutineSummary,
    WorkbenchCreateCommand,
    WorkbenchListCommand,
    WorkbenchListResult,
    WorkbenchOperationResult,
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
from kinby.hub import ControlUnreachable, HttpInstanceControl, Hub, control
from kinby.instance import Instance
from kinby.plugins import intake
from tests.test_contract_server import served as instance_served
from tests.test_factory_runs import intake_client, started
from tests.test_hub import (
    FakeControl,
    FakeRuntime,
    control_server,
    created_instance,
    finished_operation,
    hub_at,
    hub_client,
    instance_environment,
)
from tests.test_hub_factories import write_factory
from tests.test_hub_routing import control_token
from tests.test_hub_server import served, url
from tests.test_hub_workbenches import CEC_BACKEND, created, finished
from tests.test_routines import RoutineModel, instance_at, routine_file
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
    monkeypatch.setenv("KINBY_CONTROL_TOKEN", token)
    monkeypatch.setenv("KINBY_HUB_SCOPES", ",".join(hub.instance_scopes(home)))
    async with served(hub) as address, instance_served(instance, dispatcher, token=token) as own:
        monkeypatch.setenv("KINBY_HUB_URL", url(address, f"/instances/{home}/hub"))
        runtime.addresses[str(home)] = f"http://{own.host}:{own.port}"
        yield Owned(hub, runtime, control, url(address, ""), home, instance, dispatcher, clock)
    hub.close()


def _signing(workbench: WorkbenchCreateCommand, secret: bytes) -> WorkbenchCreateCommand:
    """The workbench, holding *secret* as the secret its line's deliveries are signed with."""
    return workbench.model_copy(
        update={"secrets": workbench.secrets | {"WEBHOOK_SECRET": SecretStr(secret.decode())}}
    )


async def _started_workbench(
    owned: Owned, workbench: WorkbenchCreateCommand = CEC_BACKEND, secret: bytes = SECRET
) -> UUID:
    workbench_id = (await created(owned.client, _signing(workbench, secret))).workbench_id
    start = await owned.client.call(
        WORKBENCH_START, WorkbenchStartCommand(workbench_id=workbench_id)
    )
    assert (await finished(owned.client, start)).state is OperationState.SUCCEEDED
    owned.runtime.addresses[str(workbench_id)] = f"http://kinby-{workbench_id}:8787"
    return workbench_id


async def _line(
    owned: Owned, workbench: WorkbenchCreateCommand = CEC_BACKEND, secret: bytes = SECRET
) -> Line:
    workbench_id = await _started_workbench(owned, workbench, secret)
    set_up = await owned.client.call(
        LINE_SETUP, LineSetupCommand(factory="tickets", workbench_id=workbench_id)
    )
    assert isinstance(set_up, LineSetupResult), set_up
    return set_up.line


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


async def _delivered(
    owned: Owned, line: Line, body: dict[str, object], secret: bytes = SECRET
) -> int:
    """Post a delivery signed with *secret* to the line's address on the hub."""
    data = json.dumps(body).encode()
    signature = hmac.new(secret, data, hashlib.sha256).hexdigest()
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
            assert line.intake == "tickets_cec-backend"
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
                "tickets_cec-backend",
                "tickets_landing",
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

            held = instance_environment(hub.hub, hub.home)
            owner = [spec for spec in hub.runtime.created if spec.instance_id == str(hub.home)]
            assert owner
            for secret in ("GH_TOKEN", "WEBHOOK_SECRET"):
                assert secret not in held
                assert all(secret not in spec.env for spec in owner)

    asyncio.run(scenario())


def test_the_hub_rejects_a_delivery_not_signed_with_the_secret_of_the_lines_workbench(
    tmp_path, monkeypatch
):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            line = await _line(hub)

            status = await _delivered(hub, line, {"action": "labeled"}, secret=b"another-secret")
            await hub.fired()

            assert status == 401
            assert hub.control.intakes == []
            assert await _runs(hub, line) == []

    asyncio.run(scenario())


def test_one_owner_takes_two_lines_of_a_factory_whose_deliveries_are_signed_apart(
    tmp_path, monkeypatch
):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            backend = await _line(hub, secret=b"backend-secret")
            landing = await _line(hub, LANDING, secret=b"landing-secret")
            hub.control.found = [[{"issue": 4}], [{"issue": 4}]]

            statuses = [
                await _delivered(hub, line, {"action": "labeled"}, secret=secret)
                for line, secret in (
                    (backend, b"landing-secret"),
                    (landing, b"backend-secret"),
                    (backend, b"backend-secret"),
                    (landing, b"landing-secret"),
                )
            ]
            # The owner runs one routine turn at a time: one tick for each line's intake.
            await hub.fired()
            await hub.fired()

            assert (backend.owner_id, landing.owner_id) == (hub.home, hub.home)
            assert statuses == [401, 401, 202, 202]
            assert [run.work_item for run in await _runs(hub, backend)] == [{"issue": 4}]
            assert [run.work_item for run in await _runs(hub, landing)] == [{"issue": 4}]

    asyncio.run(scenario())


def test_removing_the_line_removes_its_intake_routine(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            line = await _line(hub)

            removed = await hub.client.call(LINE_REMOVE, LineRemoveCommand(line_id=line.line_id))

            assert not isinstance(removed, ErrorEnvelope), removed
            assert await _routines(hub) == []

    asyncio.run(scenario())


def test_a_line_is_not_set_up_on_a_workbench_without_its_delivery_secret(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            workbench_id = (await created(hub.client)).workbench_id

            refused = await hub.client.call(
                LINE_SETUP, LineSetupCommand(factory="tickets", workbench_id=workbench_id)
            )

            assert isinstance(refused, ErrorEnvelope), refused
            assert refused.code is ErrorCode.INVALID_SETUP
            assert refused.fields == {
                "workbench_id": 'Workbench "cec-backend" lacks what factory "tickets" requires: '
                'secret "WEBHOOK_SECRET".'
            }
            assert await _routines(hub) == []

    asyncio.run(scenario())


def test_a_line_set_up_with_its_delivery_secret_keeps_it_on_the_workbench(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            workbench_id = (await created(hub.client)).workbench_id

            set_up = await hub.client.call(
                LINE_SETUP,
                LineSetupCommand(
                    factory="tickets",
                    workbench_id=workbench_id,
                    secrets={"WEBHOOK_SECRET": SecretStr(SECRET.decode())},
                ),
            )
            assert isinstance(set_up, LineSetupResult), set_up
            assert set_up.operation_id is not None
            await finished_operation(
                hub.client,
                WorkbenchOperationResult(
                    operation_id=set_up.operation_id, workbench_id=workbench_id
                ),
            )

            assert instance_environment(hub.hub, workbench_id)["WEBHOOK_SECRET"] == "webhook-secret"
            assert "WEBHOOK_SECRET" not in instance_environment(hub.hub, hub.home)

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


def test_a_setup_without_the_delivery_secret_creates_no_workbench(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            refused = await hub.client.call(
                LINE_SETUP,
                LineSetupCommand(
                    factory="tickets",
                    workbench=NewWorkbench(name="landing"),
                    secrets={"GH_TOKEN": SecretStr("ghp-secret")},
                ),
            )
            listed = await hub.client.call(WORKBENCH_LIST, WorkbenchListCommand())

            assert isinstance(refused, ErrorEnvelope), refused
            assert refused.fields == {
                "workbench": 'Workbench "landing" lacks what factory "tickets" requires: '
                'secret "WEBHOOK_SECRET".'
            }
            assert listed == WorkbenchListResult(workbenches=[])

    asyncio.run(scenario())


NINE_ONE = datetime(2026, 11, 13, 9, 1, tzinfo=UTC)


async def _holding_intake(owned: Owned) -> Line:
    """The line, whose intake the scheduler fired and the workbench holds unanswered."""
    line = await _line(owned)
    owned.control.intake_release.clear()
    # The first pass arms the routines from the clock.
    await owned.dispatcher.scheduler.tick()
    owned.clock.now = NINE_ONE
    async with asyncio.timeout(5):
        await owned.dispatcher.scheduler.tick()
        await owned.control.intake_asked.wait()
    return line


async def _released(owned: Owned) -> None:
    owned.control.intake_release.set()
    await owned.dispatcher.scheduler.drain()


async def _user_turn(owned: Owned) -> AcceptedResult | ErrorEnvelope:
    thread = await call(owned.dispatcher, "thread.create")
    return await call(owned.dispatcher, "thread.turn.start", thread_id=thread.id, message="Hi")


def test_a_user_turn_starts_while_the_lines_intake_waits_on_its_workbench(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            await _holding_intake(hub)

            started = await _user_turn(hub)
            await _released(hub)

            assert isinstance(started, AcceptedResult), started

    asyncio.run(scenario())


def test_another_routine_fires_while_the_lines_intake_runs_but_the_intake_waits_for_itself(
    tmp_path, monkeypatch
):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            routine_file(hub.instance, "description: News\nschedule: 30 9 * * *")
            line = await _holding_intake(hub)
            assert await _delivered(hub, line, {"action": "labeled"}) == 202

            hub.clock.now = datetime(2026, 11, 13, 9, 31, tzinfo=UTC)
            async with asyncio.timeout(5):
                await hub.dispatcher.scheduler.tick()
                await hub.dispatcher.scheduler.tick()
            news = next(routine for routine in await _routines(hub) if routine.name == "news")
            intakes_while_live = len(hub.control.intakes)
            await _released(hub)
            await hub.fired()

            assert news.last_run is not None
            assert intakes_while_live == 1
            assert len(hub.control.intakes) == 2

    asyncio.run(scenario())


def test_the_scheduler_does_not_spin_on_a_live_intake_that_is_due_again(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            await _holding_intake(hub)
            hub.clock.now = datetime(2026, 11, 14, 9, 1, tzinfo=UTC)
            scheduler = hub.dispatcher.scheduler
            tick = scheduler.tick
            passes = 0

            async def counted() -> None:
                nonlocal passes
                passes += 1
                await tick()

            monkeypatch.setattr(scheduler, "tick", counted)
            scheduler.start()
            # Yields to the loop only: a spinning worker passes again on each round.
            for _ in range(200):
                await asyncio.sleep(0)
            await scheduler.stop()
            await _released(hub)

            assert passes == 1

    asyncio.run(scenario())


def test_a_revert_is_refused_while_the_lines_intake_runs(tmp_path, monkeypatch):
    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            done = await _user_turn(hub)
            assert isinstance(done, AcceptedResult), done
            await hub.dispatcher.scheduler.drain()
            await _holding_intake(hub)

            refused = await call(
                hub.dispatcher,
                "thread.turn.revert",
                thread_id=done.thread_id,
                turn_id=done.turn_id,
            )
            await _released(hub)

            assert isinstance(refused, ErrorEnvelope), refused
            assert refused.code is ErrorCode.ROUTINES_RUNNING

    asyncio.run(scenario())


def test_an_intake_the_hub_never_answers_fails_its_run(tmp_path, monkeypatch):
    monkeypatch.setattr(intake, "HUB_INTAKE_SECONDS", 0.1)

    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            line = await _holding_intake(hub)
            async with asyncio.timeout(5):
                await hub.dispatcher.scheduler.drain()
            [routine] = await _routines(hub)
            await _released(hub)

            assert routine.name == line.intake
            assert routine.last_run is not None
            assert routine.last_run.outcome is RoutineRunOutcome.FAILED
            assert routine.failure_count == 1

    asyncio.run(scenario())


def test_the_hub_stops_waiting_on_a_workbench_that_never_answers_its_intake(monkeypatch):
    monkeypatch.setattr(control, "INTAKE_SECONDS", 0.1)

    async def silent(opened: web.WebSocketResponse) -> None:
        await opened.receive()
        await opened.receive()

    async def scenario() -> None:
        async with control_server(silent) as endpoint, asyncio.timeout(5):
            with pytest.raises(ControlUnreachable, match="did not answer"):
                await HttpInstanceControl().run_intake(
                    endpoint, IntakeRunCommand(factory="tickets", call="scan_issues")
                )

    asyncio.run(scenario())
