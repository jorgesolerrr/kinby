"""Every turn is bracketed by two workspace snapshots, whatever closes it."""

import asyncio
import logging
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from kinby.contracts import (
    ApprovalRequested,
    CompletionOutcome,
    Event,
    Scope,
    ThreadCreateResult,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnStarted,
)
from kinby.core import turns
from kinby.core.dispatcher import AcceptedResult, Dispatcher, TurnConfig, build_dispatcher
from kinby.core.snapshots import SnapshotStore
from kinby.core.turns import Emit, PreparedTurnRequest, TurnOutcome, TurnRunner
from tests.helpers import (
    FakeSnapshotStore,
    cannot_restore,
    does_not_park,
    fixed_permission_ceiling,
    fixed_turn_preparation,
)
from tests.test_turns import (
    FailingRunner,
    NoWorkAfterModelCallRunner,
    ParkingRunner,
    ScriptedRunner,
    WaitingRunner,
)


async def _closed_turn_events(
    dispatcher: Dispatcher,
    thread_id: UUID,
    *,
    count: int,
) -> list[Event]:
    subscription = dispatcher.subscribe(
        "thread.subscribe",
        {"thread_id": thread_id},
        {Scope.THREAD_READ},
    )
    events = [await asyncio.wait_for(anext(subscription), timeout=1) for _ in range(count)]
    await subscription.aclose()
    return cast(list[Event], events)


async def _thread_on(
    tmp_path: Path,
    runner: TurnRunner,
    snapshots: SnapshotStore | None,
) -> tuple[Dispatcher, UUID]:
    """A dispatcher whose turns run *runner* and snapshot into *snapshots*, and one thread."""
    dispatcher = build_dispatcher(
        tmp_path,
        turns=TurnConfig(
            fixed_turn_preparation,
            fixed_permission_ceiling,
            runner,
            snapshots=snapshots,
        ),
    )
    created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
    assert isinstance(created, ThreadCreateResult)
    return dispatcher, created.id


def test_a_completed_turn_carries_the_snapshots_taken_around_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = FakeSnapshotStore()
        dispatcher, thread_id = await _thread_on(tmp_path, ScriptedRunner(), snapshots)

        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": thread_id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )

        assert isinstance(started, AcceptedResult)
        events = await _closed_turn_events(dispatcher, thread_id, count=4)
        assert isinstance(events[0].payload, TurnStarted)
        assert events[0].payload.snapshot == f"{1:040d}"
        assert isinstance(events[-1].payload, TurnCompleted)
        assert events[-1].payload.snapshot == f"{2:040d}"
        assert snapshots.refs == [
            f"refs/kinby/snapshots/{thread_id}/{started.turn_id}/before",
            f"refs/kinby/snapshots/{thread_id}/{started.turn_id}/after",
        ]

    asyncio.run(scenario())


def test_a_failed_turn_carries_the_snapshot_taken_when_it_closed(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = FakeSnapshotStore()
        dispatcher, thread_id = await _thread_on(tmp_path, FailingRunner(), snapshots)

        await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": thread_id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )

        events = await _closed_turn_events(dispatcher, thread_id, count=2)
        failed = events[-1].payload
        assert isinstance(failed, TurnFailed)
        assert failed.snapshot == f"{2:040d}"

    asyncio.run(scenario())


def test_an_interrupted_turn_carries_the_snapshot_taken_when_it_closed(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = FakeSnapshotStore()
        runner = WaitingRunner()
        dispatcher, thread_id = await _thread_on(tmp_path, runner, snapshots)
        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": thread_id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        await asyncio.wait_for(runner.started.wait(), timeout=1)

        await dispatcher.dispatch(
            "thread.turn.interrupt",
            {"thread_id": thread_id},
            {Scope.THREAD_OPERATE},
        )

        events = await _closed_turn_events(dispatcher, thread_id, count=2)
        interrupted = events[-1].payload
        assert isinstance(interrupted, TurnInterrupted)
        assert interrupted.snapshot == f"{2:040d}"
        assert snapshots.refs[-1] == (f"refs/kinby/snapshots/{thread_id}/{started.turn_id}/after")

    asyncio.run(scenario())


def test_a_parked_turn_snapshots_only_once_it_resumes_and_closes(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = FakeSnapshotStore()
        dispatcher, thread_id = await _thread_on(tmp_path, ParkingRunner(), snapshots)
        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": thread_id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        parked = await _closed_turn_events(dispatcher, thread_id, count=2)

        requested = parked[-1].payload
        assert isinstance(requested, ApprovalRequested)
        assert snapshots.refs == [f"refs/kinby/snapshots/{thread_id}/{started.turn_id}/before"]

        await dispatcher.dispatch(
            "thread.approval.respond",
            {
                "thread_id": thread_id,
                "approval_id": str(requested.approval_id),
                "answer": "yes",
            },
            {Scope.THREAD_OPERATE},
        )

        events = await _closed_turn_events(dispatcher, thread_id, count=4)
        completed = events[-1].payload
        assert isinstance(completed, TurnCompleted)
        assert completed.snapshot == f"{2:040d}"
        assert snapshots.refs[-1] == f"refs/kinby/snapshots/{thread_id}/{started.turn_id}/after"

    asyncio.run(scenario())


def test_a_capture_that_fails_leaves_no_snapshot_and_the_turn_completes(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        dispatcher, thread_id = await _thread_on(
            tmp_path, ScriptedRunner(), FakeSnapshotStore(failing=True)
        )

        with caplog.at_level(logging.WARNING, logger="kinby.core.turns"):
            await dispatcher.dispatch(
                "thread.turn.start",
                {"thread_id": thread_id, "message": "Hello"},
                {Scope.THREAD_OPERATE},
            )
            events = await _closed_turn_events(dispatcher, thread_id, count=4)

        started, completed = events[0].payload, events[-1].payload
        assert isinstance(started, TurnStarted)
        assert isinstance(completed, TurnCompleted)
        assert started.snapshot is None
        assert completed.snapshot is None
        assert [record.message for record in caplog.records] == [
            "The workspace snapshot could not be taken.",
            "The workspace snapshot could not be taken.",
        ]

    asyncio.run(scenario())


def test_without_a_snapshot_store_a_turn_records_no_snapshots(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher, thread_id = await _thread_on(tmp_path, ScriptedRunner(), None)

        await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": thread_id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )

        events = await _closed_turn_events(dispatcher, thread_id, count=4)
        started, completed = events[0].payload, events[-1].payload
        assert isinstance(started, TurnStarted)
        assert isinstance(completed, TurnCompleted)
        assert started.snapshot is None
        assert completed.snapshot is None

    asyncio.run(scenario())


def test_a_no_work_turn_is_bracketed_like_any_other(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = FakeSnapshotStore()
        dispatcher, thread_id = await _thread_on(tmp_path, NoWorkAfterModelCallRunner(), snapshots)

        await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": thread_id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )

        events = await _closed_turn_events(dispatcher, thread_id, count=3)
        completed = events[-1].payload
        assert isinstance(completed, TurnCompleted)
        assert completed.outcome is CompletionOutcome.NO_WORK
        assert completed.snapshot == f"{2:040d}"

    asyncio.run(scenario())


class StubbornRunner:
    """A runner whose cleanup swallows cancellation, as ADR 0008 warns it may."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()

    async def run(self, turn: PreparedTurnRequest, emit: Emit) -> TurnOutcome:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            await self.release.wait()
        self.finished.set()
        return TurnOutcome()

    resume = does_not_park
    restore = cannot_restore


def test_an_interrupt_does_not_wait_forever_on_a_runner_that_swallows_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(turns, "_SETTLE_SECONDS", 0.01)
        failures: list[dict[str, object]] = []
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: failures.append(context)
        )
        runner = StubbornRunner()
        snapshots = FakeSnapshotStore()
        dispatcher, thread_id = await _thread_on(tmp_path, runner, snapshots)
        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": thread_id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        await runner.started.wait()

        interrupted = await asyncio.wait_for(
            dispatcher.dispatch(
                "thread.turn.interrupt",
                {"thread_id": thread_id},
                {Scope.THREAD_OPERATE},
            ),
            timeout=5,
        )

        assert isinstance(interrupted, AcceptedResult)
        assert snapshots.refs[-1] == f"refs/kinby/snapshots/{thread_id}/{started.turn_id}/after"
        runner.release.set()
        await runner.finished.wait()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert snapshots.refs == [
            f"refs/kinby/snapshots/{thread_id}/{started.turn_id}/before",
            f"refs/kinby/snapshots/{thread_id}/{started.turn_id}/after",
        ]
        assert failures == []

    asyncio.run(scenario())


def test_an_interrupt_owns_completion_when_the_runner_stops_during_the_wait(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        failures: list[dict[str, object]] = []
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: failures.append(context)
        )
        runner = StubbornRunner()
        snapshots = FakeSnapshotStore()
        dispatcher, thread_id = await _thread_on(tmp_path, runner, snapshots)
        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": thread_id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        await runner.started.wait()

        interrupting = asyncio.create_task(
            dispatcher.dispatch(
                "thread.turn.interrupt",
                {"thread_id": thread_id},
                {Scope.THREAD_OPERATE},
            )
        )
        await runner.cancelled.wait()
        runner.release.set()
        interrupted = await asyncio.wait_for(interrupting, timeout=1)
        await runner.finished.wait()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert isinstance(interrupted, AcceptedResult)
        assert snapshots.refs == [
            f"refs/kinby/snapshots/{thread_id}/{started.turn_id}/before",
            f"refs/kinby/snapshots/{thread_id}/{started.turn_id}/after",
        ]
        assert failures == []

    asyncio.run(scenario())
