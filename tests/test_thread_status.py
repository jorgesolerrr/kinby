import asyncio
from pathlib import Path
from uuid import UUID, uuid4

from kinby.contracts import (
    AcceptedResult,
    ApprovalRequested,
    Event,
    EventType,
    PermissionMode,
    Scope,
    ThreadCreateResult,
    ThreadListResult,
    ThreadStatus,
    ThreadSummary,
)
from kinby.core.dispatcher import Dispatcher, TurnConfig, build_dispatcher
from kinby.core.turns import Emit, ParkedTurn, PreparedTurnRequest, TurnOutcome
from kinby.instance.permissions import GatePolicy
from tests.helpers import (
    cannot_restore,
    does_not_park,
    fixed_permission_ceiling,
    fixed_turn_preparation,
    thread_events,
)


class ScriptedRunner:
    """Run each turn as its message says: hold it open, park it on an approval, or fail it."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, turn: PreparedTurnRequest, emit: Emit) -> TurnOutcome | ParkedTurn:
        match turn.message:
            case "hold":
                self.started.set()
                await self.release.wait()
            case "park":
                await emit(
                    ApprovalRequested(
                        approval_id=uuid4(), name="write_file", arguments={}, rule="scripted"
                    )
                )
                return ParkedTurn()
            case "fail":
                raise RuntimeError("provider unavailable")
        return TurnOutcome()

    resume = does_not_park
    restore = cannot_restore


def _dispatcher(tmp_path: Path, runner: ScriptedRunner) -> Dispatcher:
    return build_dispatcher(
        tmp_path,
        turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, runner),
    )


async def _create(dispatcher: Dispatcher) -> ThreadCreateResult:
    created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
    assert isinstance(created, ThreadCreateResult)
    return created


async def _start(dispatcher: Dispatcher, thread_id: UUID, message: str) -> AcceptedResult:
    accepted = await dispatcher.dispatch(
        "thread.turn.start",
        {"thread_id": thread_id, "message": message},
        {Scope.THREAD_OPERATE},
    )
    assert isinstance(accepted, AcceptedResult)
    return accepted


async def _until(dispatcher: Dispatcher, thread_id: UUID, closing: EventType) -> Event:
    """Follow the thread until the event of type ``closing`` arrives."""
    subscription = await thread_events(dispatcher, {"thread_id": thread_id})
    try:
        while True:
            event = await asyncio.wait_for(anext(subscription), timeout=1)
            assert isinstance(event, Event)
            if event.type is closing:
                return event
    finally:
        await subscription.aclose()


async def _listed(dispatcher: Dispatcher) -> list[ThreadSummary]:
    listed = await dispatcher.dispatch("thread.list", {}, {Scope.THREAD_READ})
    assert isinstance(listed, ThreadListResult)
    return listed.threads


def test_a_new_thread_is_idle_and_last_active_when_it_was_created(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(tmp_path)
        created = await _create(dispatcher)

        [thread] = await _listed(dispatcher)

        assert thread.status is ThreadStatus.IDLE
        assert thread.last_activity_at == created.created_at

    asyncio.run(scenario())


def test_a_thread_runs_while_its_turn_is_open(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = ScriptedRunner()
        dispatcher = _dispatcher(tmp_path, runner)
        created = await _create(dispatcher)
        await _start(dispatcher, created.id, "hold")
        await asyncio.wait_for(runner.started.wait(), timeout=1)

        [thread] = await _listed(dispatcher)

        assert thread.status is ThreadStatus.RUNNING
        runner.release.set()

    asyncio.run(scenario())


def test_a_thread_awaits_approval_while_its_turn_is_parked(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path, ScriptedRunner())
        created = await _create(dispatcher)
        await _start(dispatcher, created.id, "park")
        await _until(dispatcher, created.id, EventType.APPROVAL_REQUESTED)

        [thread] = await _listed(dispatcher)

        assert thread.status is ThreadStatus.AWAITING_APPROVAL

    asyncio.run(scenario())


def test_a_thread_fails_with_its_last_turn_until_the_next_turn_starts(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = ScriptedRunner()
        dispatcher = _dispatcher(tmp_path, runner)
        created = await _create(dispatcher)
        await _start(dispatcher, created.id, "fail")
        await _until(dispatcher, created.id, EventType.TURN_FAILED)

        [failed] = await _listed(dispatcher)
        await _start(dispatcher, created.id, "hold")
        await asyncio.wait_for(runner.started.wait(), timeout=1)
        [running] = await _listed(dispatcher)

        assert failed.status is ThreadStatus.FAILED
        assert running.status is ThreadStatus.RUNNING
        runner.release.set()

    asyncio.run(scenario())


def test_a_thread_is_idle_once_its_turn_completes(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path, ScriptedRunner())
        created = await _create(dispatcher)
        await _start(dispatcher, created.id, "done")
        await _until(dispatcher, created.id, EventType.TURN_COMPLETED)

        [thread] = await _listed(dispatcher)

        assert thread.status is ThreadStatus.IDLE

    asyncio.run(scenario())


def test_a_thread_is_idle_once_its_turn_is_interrupted(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = ScriptedRunner()
        dispatcher = _dispatcher(tmp_path, runner)
        created = await _create(dispatcher)
        await _start(dispatcher, created.id, "hold")
        await asyncio.wait_for(runner.started.wait(), timeout=1)
        interrupted = await dispatcher.dispatch(
            "thread.turn.interrupt", {"thread_id": created.id}, {Scope.THREAD_OPERATE}
        )
        assert isinstance(interrupted, AcceptedResult)

        [thread] = await _listed(dispatcher)

        assert thread.status is ThreadStatus.IDLE

    asyncio.run(scenario())


def test_a_parked_turn_that_is_interrupted_leaves_the_thread_idle(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path, ScriptedRunner())
        created = await _create(dispatcher)
        await _start(dispatcher, created.id, "park")
        await _until(dispatcher, created.id, EventType.APPROVAL_REQUESTED)
        interrupted = await dispatcher.dispatch(
            "thread.turn.interrupt", {"thread_id": created.id}, {Scope.THREAD_OPERATE}
        )
        assert isinstance(interrupted, AcceptedResult)

        [thread] = await _listed(dispatcher)

        assert thread.status is ThreadStatus.IDLE

    asyncio.run(scenario())


def _pin(dispatcher: Dispatcher, thread_id: UUID, mode: PermissionMode) -> None:
    pinned = asyncio.run(
        dispatcher.dispatch(
            "thread.mode.set", {"thread_id": thread_id, "mode": mode}, {Scope.THREAD_ADMIN}
        )
    )
    assert isinstance(pinned, AcceptedResult)


def test_an_unpinned_thread_runs_in_the_instance_default_mode(tmp_path: Path) -> None:
    dispatcher = build_dispatcher(
        tmp_path, permissions=lambda: GatePolicy(mode=PermissionMode.AUTO)
    )
    asyncio.run(_create(dispatcher))

    [thread] = asyncio.run(_listed(dispatcher))

    assert thread.mode is PermissionMode.AUTO
    assert thread.mode_pinned is False


def test_a_pinned_thread_runs_in_its_pinned_mode(tmp_path: Path) -> None:
    dispatcher = _dispatcher(tmp_path, ScriptedRunner())
    created = asyncio.run(_create(dispatcher))
    _pin(dispatcher, created.id, PermissionMode.READ_ONLY)

    [thread] = asyncio.run(_listed(dispatcher))

    assert thread.mode is PermissionMode.READ_ONLY
    assert thread.mode_pinned is True


def test_a_mode_above_a_lowered_ceiling_is_capped_at_the_ceiling(tmp_path: Path) -> None:
    ceiling = PermissionMode.FULL_ACCESS
    dispatcher = build_dispatcher(
        tmp_path,
        turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, ScriptedRunner()),
        permissions=lambda: GatePolicy(mode=PermissionMode.FULL_ACCESS, ceiling=ceiling),
    )
    pinned = asyncio.run(_create(dispatcher))
    unpinned = asyncio.run(_create(dispatcher))
    _pin(dispatcher, pinned.id, PermissionMode.FULL_ACCESS)

    ceiling = PermissionMode.ASK
    listed = asyncio.run(dispatcher.dispatch("thread.list", {}, {Scope.THREAD_READ}))

    assert isinstance(listed, ThreadListResult)
    assert listed.ceiling is PermissionMode.ASK
    modes = {thread.id: (thread.mode, thread.mode_pinned) for thread in listed.threads}
    assert modes == {
        pinned.id: (PermissionMode.ASK, True),
        unpinned.id: (PermissionMode.ASK, False),
    }


def test_threads_list_the_most_recently_active_first(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path, ScriptedRunner())
        older = await _create(dispatcher)
        newer = await _create(dispatcher)
        before = [thread.id for thread in await _listed(dispatcher)]

        await _start(dispatcher, older.id, "done")
        completed = await _until(dispatcher, older.id, EventType.TURN_COMPLETED)
        after = await _listed(dispatcher)

        assert before == [newer.id, older.id]
        assert [thread.id for thread in after] == [older.id, newer.id]
        assert after[0].last_activity_at == completed.timestamp
        assert after[1].last_activity_at == newer.created_at

    asyncio.run(scenario())
