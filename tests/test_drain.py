"""Instance draining: the runtime refuses new work and waits for what it accepted."""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import aiohttp
import pytest

from kinby.contracts import (
    INSTANCE_DRAIN,
    INSTANCE_STOP,
    ApprovalRequested,
    Capability,
    ContractModel,
    ControlToken,
    DrainState,
    ErrorCode,
    ErrorEnvelope,
    Event,
    FrameType,
    InstanceDrainCommand,
    InstanceDrainResult,
    InstanceStopCommand,
    LifecycleOperationResult,
    MemoryRecapped,
    MessageDelta,
    OperationGetResult,
    OperationState,
    Scope,
    SignalReceived,
    ThreadCreateResult,
    TreeId,
    TurnCompleted,
    TurnInterrupted,
    TurnStarted,
)
from kinby.core import boot_instance
from kinby.core.contract_server import CONTROL_TOKEN_VARIABLE
from kinby.core.dispatcher import (
    Dispatcher,
    ScheduledDispatcher,
    ScheduledTurnConfig,
    TurnConfig,
    build_dispatcher,
)
from kinby.core.events import EventLog
from kinby.core.runtime import InstanceRuntime
from kinby.core.scheduler import SchedulerConfig
from kinby.core.snapshots import SnapshotRef, SnapshotStore
from kinby.core.turns import (
    ApprovalDecision,
    Emit,
    ParkedTurn,
    PreparedTurnRequest,
    TurnOutcome,
    TurnRunner,
)
from kinby.hub import HttpInstanceControl, Hub, InstanceAddress
from kinby.instance import Instance, Serve, load_instance
from kinby.memory import GraphStore, RecapWriter
from tests.helpers import (
    FakeSnapshotStore,
    cannot_restore,
    does_not_park,
    fixed_permission_ceiling,
    fixed_turn_preparation,
    turn_config_stub,
)
from tests.test_contract_server import connected, frame, health, served
from tests.test_hub import FakeRuntime, hub_at, hub_client, started_instance
from tests.test_routines import instance_at, routine_file
from tests.test_scheduler import FakeClock, ScriptedRunner

_APPROVAL_ID = UUID("22222222-2222-2222-2222-222222222222")
_CLOCK = FakeClock(datetime(2026, 9, 21, tzinfo=UTC))


class WaitingRunner:
    """Hold a turn open until the test releases it."""

    restore = cannot_restore
    resume = does_not_park

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, turn: PreparedTurnRequest, emit: Emit) -> TurnOutcome:
        self.started.set()
        await self.release.wait()
        await emit(MessageDelta(text="Finished"))
        return TurnOutcome(input_tokens=1, output_tokens=1)


class ParkingRunner:
    """Park on an approval, then finish when the answer arrives."""

    def __init__(self) -> None:
        self.parked: PreparedTurnRequest | None = None

    async def restore(self, thread_id: UUID, turn_id: UUID) -> PreparedTurnRequest | None:
        return self.parked

    async def run(self, turn: PreparedTurnRequest, emit: Emit) -> ParkedTurn:
        self.parked = turn
        await emit(
            ApprovalRequested(
                approval_id=_APPROVAL_ID,
                name="write",
                arguments={},
                rule="scripted",
            )
        )
        return ParkedTurn()

    async def resume(
        self,
        turn: PreparedTurnRequest,
        decision: ApprovalDecision,
        emit: Emit,
    ) -> TurnOutcome:
        await emit(MessageDelta(text="Approved"))
        return TurnOutcome(input_tokens=1, output_tokens=1)


class SlowSnapshots(FakeSnapshotStore):
    """Hold the snapshot a turn takes between reserving the instance and recording the start."""

    def __init__(self) -> None:
        super().__init__()
        self.capturing = asyncio.Event()
        self.release = asyncio.Event()

    async def capture(self, ref: SnapshotRef) -> TreeId:
        self.capturing.set()
        await self.release.wait()
        return await super().capture(ref)


def booted(
    instance: Instance,
    runner: TurnRunner,
    snapshots: SnapshotStore | None = None,
    recap: RecapWriter | None = None,
) -> InstanceRuntime:
    """A runtime over a scripted runner, without the model turns boot_instance opens."""
    dispatcher: ScheduledDispatcher = build_dispatcher(
        instance.manifest.state_dir,
        turns=ScheduledTurnConfig(
            TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                runner,
                recap,
                snapshots,
            ),
            SchedulerConfig(instance, _CLOCK),
        ),
    )
    runtime = InstanceRuntime(dispatcher, recap)
    dispatcher.register(INSTANCE_DRAIN, runtime.drain)
    return runtime


async def call(dispatcher: Dispatcher, method: str, **payload: object) -> ContractModel:
    return await dispatcher.dispatch(method, payload, set(Scope))


async def opened_thread(dispatcher: Dispatcher) -> UUID:
    created = await call(dispatcher, "thread.create")
    assert isinstance(created, ThreadCreateResult)
    return created.id


def recorded(instance: Instance) -> list[Event]:
    return list(EventLog(instance.manifest.state_dir).all_events())


async def until(ready: Callable[[], bool], timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while not ready():
            await asyncio.sleep(0)


def test_draining_refuses_new_work_before_it_reserves_or_records_anything(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        runtime = booted(instance, WaitingRunner())
        thread = await opened_thread(runtime.dispatcher)

        drained = await runtime.drain(InstanceDrainCommand())
        started = await call(
            runtime.dispatcher, "thread.turn.start", thread_id=str(thread), message="Hi"
        )
        fired = await call(runtime.dispatcher, "routine.run", name="news")
        delivered = await call(
            runtime.dispatcher,
            "routine.run",
            name="news",
            payload={"body": "manual", "content_type": "text/plain"},
        )
        reverted = await call(
            runtime.dispatcher,
            "thread.turn.revert",
            thread_id=str(thread),
            turn_id=str(uuid4()),
        )
        listed = await call(runtime.dispatcher, "thread.list")

        assert drained.state is DrainState.DRAINED
        for refused in (started, fired, delivered, reverted):
            assert isinstance(refused, ErrorEnvelope)
            assert refused.code is ErrorCode.INSTANCE_DRAINING
        assert not isinstance(listed, ErrorEnvelope)
        assert recorded(instance) == []

    asyncio.run(scenario())


def test_draining_waits_for_an_accepted_turn_to_close(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        runner = WaitingRunner()
        runtime = booted(instance, runner)
        thread = await opened_thread(runtime.dispatcher)
        await call(runtime.dispatcher, "thread.turn.start", thread_id=str(thread), message="Hi")
        await asyncio.wait_for(runner.started.wait(), timeout=5)

        draining = asyncio.create_task(runtime.drain(InstanceDrainCommand()))
        await asyncio.sleep(0)
        pending = not draining.done()
        second = await opened_thread(runtime.dispatcher)
        racing = await call(
            runtime.dispatcher, "thread.turn.start", thread_id=str(second), message="Hi"
        )
        runner.release.set()
        result = await asyncio.wait_for(draining, timeout=5)

        assert pending is True
        assert isinstance(racing, ErrorEnvelope)
        assert racing.code is ErrorCode.INSTANCE_DRAINING
        assert result.state is DrainState.DRAINED
        assert [type(event.payload).__name__ for event in recorded(instance)] == [
            TurnStarted.__name__,
            MessageDelta.__name__,
            TurnCompleted.__name__,
        ]

    asyncio.run(scenario())


def test_a_parked_approval_keeps_the_drain_pending_until_it_is_answered(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        runtime = booted(instance, ParkingRunner())
        thread = await opened_thread(runtime.dispatcher)
        await call(runtime.dispatcher, "thread.turn.start", thread_id=str(thread), message="Hi")
        await until(
            lambda: any(isinstance(e.payload, ApprovalRequested) for e in recorded(instance))
        )

        draining = asyncio.create_task(runtime.drain(InstanceDrainCommand()))
        await asyncio.sleep(0)
        parked = not draining.done()
        answered = await call(
            runtime.dispatcher,
            "thread.approval.respond",
            thread_id=str(thread),
            approval_id=str(_APPROVAL_ID),
            answer="yes",
        )
        result = await asyncio.wait_for(draining, timeout=5)

        assert parked is True
        assert not isinstance(answered, ErrorEnvelope)
        assert result.state is DrainState.DRAINED
        assert any(isinstance(event.payload, TurnCompleted) for event in recorded(instance))

    asyncio.run(scenario())


def test_force_escalates_a_pending_drain_and_interrupts_the_parked_approval(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        runtime = booted(instance, ParkingRunner())
        thread = await opened_thread(runtime.dispatcher)
        await call(runtime.dispatcher, "thread.turn.start", thread_id=str(thread), message="Hi")
        await until(
            lambda: any(isinstance(e.payload, ApprovalRequested) for e in recorded(instance))
        )

        draining = asyncio.create_task(runtime.drain(InstanceDrainCommand()))
        await asyncio.sleep(0)
        forced = await asyncio.wait_for(
            runtime.drain(InstanceDrainCommand(force=True)),
            timeout=5,
        )
        result = await asyncio.wait_for(draining, timeout=5)

        assert forced.state is DrainState.INTERRUPTED
        assert result.state is DrainState.INTERRUPTED
        assert any(isinstance(event.payload, TurnInterrupted) for event in recorded(instance))

    asyncio.run(scenario())


def test_force_interrupts_a_parked_approval_restored_after_a_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        parked = booted(instance, ParkingRunner())
        thread = await opened_thread(parked.dispatcher)
        await call(parked.dispatcher, "thread.turn.start", thread_id=str(thread), message="Hi")
        await until(
            lambda: any(isinstance(e.payload, ApprovalRequested) for e in recorded(instance))
        )
        await parked.stop_after_running_routine()

        restarted = booted(load_instance(tmp_path), ParkingRunner())
        result = await asyncio.wait_for(
            restarted.drain(InstanceDrainCommand(force=True)),
            timeout=10,
        )

        assert result.state is DrainState.INTERRUPTED
        assert any(isinstance(event.payload, TurnInterrupted) for event in recorded(instance))

    asyncio.run(scenario())


def test_force_interrupts_a_running_user_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        runner = WaitingRunner()
        runtime = booted(instance, runner)
        thread = await opened_thread(runtime.dispatcher)
        await call(runtime.dispatcher, "thread.turn.start", thread_id=str(thread), message="Hi")
        await asyncio.wait_for(runner.started.wait(), timeout=5)

        await asyncio.wait_for(runtime.drain(InstanceDrainCommand(force=True)), timeout=10)

        assert any(isinstance(event.payload, TurnInterrupted) for event in recorded(instance))

    asyncio.run(scenario())


def test_the_drain_waits_for_a_turn_that_was_still_reserving_the_instance(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        snapshots = SlowSnapshots()
        runtime = booted(instance, WaitingRunner(), snapshots)
        thread = await opened_thread(runtime.dispatcher)
        starting = asyncio.create_task(
            call(runtime.dispatcher, "thread.turn.start", thread_id=str(thread), message="Hi")
        )
        await asyncio.wait_for(snapshots.capturing.wait(), timeout=5)

        draining = asyncio.create_task(runtime.drain(InstanceDrainCommand(force=True)))
        await asyncio.sleep(0)
        waiting = not draining.done()
        snapshots.release.set()
        result = await asyncio.wait_for(draining, timeout=10)
        await starting

        assert waiting is True
        assert result.state is DrainState.INTERRUPTED
        assert any(isinstance(event.payload, TurnStarted) for event in recorded(instance))
        assert any(isinstance(event.payload, TurnInterrupted) for event in recorded(instance))

    asyncio.run(scenario())


def test_a_delivery_taken_while_draining_fires_at_the_next_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIGNAL_SECRET", "secret")

    async def scenario() -> tuple[int, list[str]]:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: Issues\nsignal:\n  secret: SIGNAL_SECRET")
        runtime = booted(instance, WaitingRunner())
        async with served(instance, runtime.dispatcher) as address:
            await runtime.drain(InstanceDrainCommand())
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    f"http://{address.host}:{address.port}/signals/news",
                    data="issue opened",
                    headers={"Authorization": "Bearer secret"},
                ) as response,
            ):
                status = response.status
        while_draining = [type(event.payload).__name__ for event in recorded(instance)]

        restarted = booted(instance_at(tmp_path), ScriptedRunner())
        restarted.scheduler.start()
        await until(lambda: any(isinstance(e.payload, TurnCompleted) for e in recorded(instance)))
        await restarted.stop_after_running_routine()
        return status, while_draining

    status, while_draining = asyncio.run(scenario())

    assert status == 202
    assert while_draining == [SignalReceived.__name__]


def test_health_reports_the_drain_and_only_the_control_route_serves_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "kinby.core.runtime.turn_config",
        turn_config_stub(
            lambda: TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                WaitingRunner(),
            )
        ),
    )

    async def scenario() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        instance = instance_at(tmp_path)
        runtime = await boot_instance(instance)
        async with served(instance, runtime.dispatcher) as address:
            reported = await health(address)
            async with connected(address, path="/control") as socket:
                await _drain_call(socket)
                drained = await frame(socket)
            async with connected(address) as socket:
                await _drain_call(socket)
                denied = await frame(socket)
        return reported, drained, denied

    reported, drained, denied = asyncio.run(scenario())

    assert reported["capabilities"] == [Capability.WS.value, Capability.DRAIN.value]
    assert drained["type"] == FrameType.RESULT.value
    assert drained["result"] == InstanceDrainResult(state=DrainState.DRAINED).model_dump(
        mode="json"
    )
    assert denied["type"] == FrameType.ERROR.value
    assert isinstance(denied["error"], dict)
    assert denied["error"]["code"] == ErrorCode.PERMISSION_DENIED.value


async def _drain_call(socket: aiohttp.ClientWebSocketResponse) -> None:
    await socket.send_str(
        json.dumps({"type": "call", "id": "1", "method": "instance.drain", "params": {}})
    )


class ServedRuntime(FakeRuntime):
    """A container whose private server is the instance this test serves itself."""

    def __init__(self) -> None:
        super().__init__()
        self.serving: Serve | None = None

    def address(self, instance_id: str, port: int) -> InstanceAddress:
        assert self.serving is not None
        return InstanceAddress(f"http://{self.serving.host}:{self.serving.port}")


def test_the_hub_stops_a_served_instance_through_its_private_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parked approval holds the stop; answering it lets recap and the container finish."""
    monkeypatch.setenv("SIGNAL_SECRET", "secret")

    async def scenario() -> tuple[OperationGetResult, OperationGetResult, list[str], list[str]]:
        (tmp_path / "instance").mkdir()
        instance_at(tmp_path / "instance")
        with (tmp_path / "instance" / "kinby.toml").open("a") as manifest:
            manifest.write('[memory]\nrecap = "off"\n')
        instance = load_instance(tmp_path / "instance")
        routine_file(instance, "description: Issues\nsignal:\n  secret: SIGNAL_SECRET")
        log = EventLog(instance.manifest.state_dir)
        runtime = booted(
            instance,
            ParkingRunner(),
            recap=RecapWriter(log, GraphStore(instance.path), instance),
        )
        thread = await opened_thread(runtime.dispatcher)
        await call(runtime.dispatcher, "thread.turn.start", thread_id=str(thread), message="Hi")
        await until(
            lambda: any(isinstance(e.payload, ApprovalRequested) for e in recorded(instance))
        )

        container = ServedRuntime()
        hub = hub_at(tmp_path / "hub", runtime=container, control=HttpInstanceControl())
        client = hub_client(hub)
        managed = await started_instance(client, hub)
        token = ControlToken(container.created[0].env[CONTROL_TOKEN_VARIABLE])
        async with served(instance, runtime.dispatcher, token=token) as address:
            container.serving = address
            stopping = await client.call(
                INSTANCE_STOP,
                InstanceStopCommand(instance_id=managed.instance_id),
            )
            assert isinstance(stopping, LifecycleOperationResult)
            await until(lambda: _draining(hub, stopping.operation_id))
            pending = hub.registry.operation(stopping.operation_id)
            delivery = await _post_signal(address)
            answered = await call(
                runtime.dispatcher,
                "thread.approval.respond",
                thread_id=str(thread),
                approval_id=str(_APPROVAL_ID),
                answer="yes",
            )
            assert not isinstance(answered, ErrorEnvelope)
            outcome = await _finished(hub, stopping.operation_id)
        while_stopping = [type(event.payload).__name__ for event in recorded(instance)]

        restarted = booted(load_instance(tmp_path / "instance"), ScriptedRunner())
        restarted.scheduler.start()
        await until(lambda: _turns_started(instance) == 2, timeout=10)
        await restarted.stop_after_running_routine()
        assert delivery == 202
        assert pending is not None
        return (
            pending,
            outcome,
            while_stopping,
            [type(event.payload).__name__ for event in recorded(instance)],
        )

    pending, outcome, while_stopping, after_restart = asyncio.run(scenario())

    assert pending.state is OperationState.RUNNING
    assert pending.detail == "Draining accepted work."
    assert outcome.state is OperationState.SUCCEEDED
    assert outcome.detail == "Instance stopped."
    assert "Instance drained. Stopping the container." in [step.detail for step in outcome.steps]
    # The turn that was parked finished, its recap ran, and the delivery only waited.
    assert while_stopping.count(TurnCompleted.__name__) == 1
    assert MemoryRecapped.__name__ in while_stopping
    assert while_stopping.count(TurnStarted.__name__) == 1
    assert SignalReceived.__name__ in while_stopping
    assert after_restart.count(TurnStarted.__name__) == 2


def _turns_started(instance: Instance) -> int:
    return sum(isinstance(event.payload, TurnStarted) for event in recorded(instance))


def _draining(hub: Hub, operation_id: UUID) -> bool:
    operation = hub.registry.operation(operation_id)
    return operation is not None and operation.detail == "Draining accepted work."


async def _finished(hub: Hub, operation_id: UUID) -> OperationGetResult:
    await until(
        lambda: (
            (operation := hub.registry.operation(operation_id)) is not None
            and operation.state in {OperationState.SUCCEEDED, OperationState.FAILED}
        ),
        timeout=10,
    )
    operation = hub.registry.operation(operation_id)
    assert operation is not None
    return operation


async def _post_signal(address: Serve) -> int:
    async with (
        aiohttp.ClientSession() as session,
        session.post(
            f"http://{address.host}:{address.port}/signals/news",
            data="issue opened",
            headers={"Authorization": "Bearer secret"},
        ) as response,
    ):
        return response.status
