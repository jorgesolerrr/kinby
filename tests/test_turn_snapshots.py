"""Every turn is bracketed by two workspace snapshots, whatever closes it."""

import asyncio
import logging
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from kinby.contracts import (
    ApprovalRequested,
    ChangeStatus,
    CompletionOutcome,
    ErrorCode,
    ErrorEnvelope,
    Event,
    FileChange,
    Scope,
    ThreadCreateResult,
    ThreadTurnDiffResult,
    TreeId,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnRated,
    TurnStarted,
    WorkspaceReverted,
    is_turn_closing,
)
from kinby.core import turns
from kinby.core.dispatcher import AcceptedResult, Dispatcher, TurnConfig, build_dispatcher
from kinby.core.events import EventLog
from kinby.core.snapshots import SnapshotError, SnapshotRef, SnapshotStore, WorkspaceDiff
from kinby.core.turns import (
    Emit,
    PreparedTurnRequest,
    TurnOutcome,
    TurnResult,
    TurnRunner,
)
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


class SelectivelyFailingSnapshotStore(FakeSnapshotStore):
    def __init__(self, failed_capture: int) -> None:
        super().__init__()
        self._failed_capture = failed_capture

    async def capture(self, ref: SnapshotRef) -> TreeId:
        self.refs.append(ref)
        if len(self.refs) == self._failed_capture:
            raise SnapshotError("git write-tree failed")
        return TreeId(f"{len(self.refs):040d}")


class ParksTheSecondTurnRunner:
    """Close the first turn, park the second, so the thread carries a rating and a park."""

    def __init__(self) -> None:
        self._parking = ParkingRunner()
        self.closed = False

    async def run(self, turn: PreparedTurnRequest, emit: Emit) -> TurnResult:
        if not self.closed:
            self.closed = True
            return TurnOutcome(input_tokens=4, output_tokens=2)
        return await self._parking.run(turn, emit)

    resume = does_not_park
    restore = cannot_restore


class FailingRestoreSnapshotStore(FakeSnapshotStore):
    async def restore(self, tree: TreeId) -> None:
        self.restored.append(tree)
        raise SnapshotError("git read-tree failed")


class BlockingRestoreSnapshotStore(FakeSnapshotStore):
    """Hold the work tree mid-restore so a caller can try to start a turn against it."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def restore(self, tree: TreeId) -> None:
        self.restored.append(tree)
        self.entered.set()
        await self.release.wait()


class FailingDiffSnapshotStore(FakeSnapshotStore):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    async def diff(self, before: TreeId, after: TreeId) -> WorkspaceDiff:
        self.diffs.append((before, after))
        raise self._error


def test_a_closed_turn_diff_returns_its_snapshots_and_the_store_result(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = FakeSnapshotStore()
        dispatcher, thread_id = await _thread_on(tmp_path, ScriptedRunner(), snapshots)
        snapshots.difference = WorkspaceDiff(
            files=[
                FileChange(
                    path="notes.md",
                    status=ChangeStatus.MODIFIED,
                    additions=1,
                    deletions=1,
                )
            ],
            patch="diff --git a/notes.md b/notes.md",
        )
        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": thread_id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        await _closed_turn_events(dispatcher, thread_id, count=4)

        result = await dispatcher.dispatch(
            "thread.turn.diff",
            {"thread_id": thread_id, "turn_id": started.turn_id},
            {Scope.THREAD_READ},
        )

        assert result == ThreadTurnDiffResult(
            turn_id=started.turn_id,
            before=TreeId(f"{1:040d}"),
            after=TreeId(f"{2:040d}"),
            files=snapshots.difference.files,
            patch=snapshots.difference.patch,
        )
        assert snapshots.diffs == [(TreeId(f"{1:040d}"), TreeId(f"{2:040d}"))]

    asyncio.run(scenario())


@pytest.mark.parametrize("missing", ["before", "after"])
def test_a_turn_diff_requires_both_snapshot_ids(tmp_path: Path, missing: str) -> None:
    async def scenario() -> None:
        failed_capture = 1 if missing == "before" else 2
        dispatcher, thread_id = await _thread_on(
            tmp_path,
            ScriptedRunner(),
            SelectivelyFailingSnapshotStore(failed_capture),
        )
        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": thread_id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        await _closed_turn_events(dispatcher, thread_id, count=4)
        result = await dispatcher.dispatch(
            "thread.turn.diff",
            {"thread_id": thread_id, "turn_id": started.turn_id},
            {Scope.THREAD_READ},
        )

        assert isinstance(result, ErrorEnvelope)
        assert result.code is ErrorCode.SNAPSHOT_UNAVAILABLE

    asyncio.run(scenario())


def test_a_turn_diff_requires_an_open_snapshot_store(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher, thread_id = await _thread_on(tmp_path, ScriptedRunner(), None)
        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": thread_id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        await _closed_turn_events(dispatcher, thread_id, count=4)
        result = await dispatcher.dispatch(
            "thread.turn.diff",
            {"thread_id": thread_id, "turn_id": started.turn_id},
            {Scope.THREAD_READ},
        )

        assert isinstance(result, ErrorEnvelope)
        assert result.code is ErrorCode.SNAPSHOT_UNAVAILABLE

    asyncio.run(scenario())


def test_a_running_turn_cannot_be_diffed(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = WaitingRunner()
        dispatcher, thread_id = await _thread_on(
            tmp_path,
            runner,
            FakeSnapshotStore(),
        )
        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": thread_id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        await runner.started.wait()
        result = await dispatcher.dispatch(
            "thread.turn.diff",
            {"thread_id": thread_id, "turn_id": started.turn_id},
            {Scope.THREAD_READ},
        )
        await dispatcher.dispatch(
            "thread.turn.interrupt",
            {"thread_id": thread_id},
            {Scope.THREAD_OPERATE},
        )

        assert isinstance(result, ErrorEnvelope)
        assert result.code is ErrorCode.THREAD_BUSY

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (SnapshotError("git diff failed"), ErrorCode.SNAPSHOT_UNAVAILABLE),
        (RuntimeError("parser failed"), ErrorCode.INTERNAL),
    ],
)
def test_a_turn_diff_classifies_store_failures(
    tmp_path: Path,
    failure: Exception,
    expected: ErrorCode,
) -> None:
    async def scenario() -> None:
        snapshots = FailingDiffSnapshotStore(failure)
        dispatcher, thread_id = await _thread_on(tmp_path, ScriptedRunner(), snapshots)
        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": thread_id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        await _closed_turn_events(dispatcher, thread_id, count=4)

        result = await dispatcher.dispatch(
            "thread.turn.diff",
            {"thread_id": thread_id, "turn_id": started.turn_id},
            {Scope.THREAD_READ},
        )

        assert isinstance(result, ErrorEnvelope)
        assert result.code is expected

    asyncio.run(scenario())


def test_an_unknown_turn_cannot_be_diffed(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher, thread_id = await _thread_on(
            tmp_path,
            ScriptedRunner(),
            FakeSnapshotStore(),
        )
        turn_id = UUID("11111111-1111-1111-1111-111111111111")
        result = await dispatcher.dispatch(
            "thread.turn.diff",
            {"thread_id": thread_id, "turn_id": turn_id},
            {Scope.THREAD_READ},
        )

        assert isinstance(result, ErrorEnvelope)
        assert result.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_thread_turn_diff_is_dispatched_under_thread_read(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = FakeSnapshotStore()
        snapshots.difference = WorkspaceDiff([], "")
        dispatcher, thread_id = await _thread_on(tmp_path, ScriptedRunner(), snapshots)
        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": thread_id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        await _closed_turn_events(dispatcher, thread_id, count=4)

        denied = await dispatcher.dispatch(
            "thread.turn.diff",
            {"thread_id": thread_id, "turn_id": started.turn_id},
            {Scope.THREAD_OPERATE},
        )
        result = await dispatcher.dispatch(
            "thread.turn.diff",
            {"thread_id": thread_id, "turn_id": started.turn_id},
            {Scope.THREAD_READ},
        )

        assert denied == ErrorEnvelope(
            code=ErrorCode.PERMISSION_DENIED,
            message='Missing required scope "thread:read".',
            retryable=False,
        )
        assert result == ThreadTurnDiffResult(
            turn_id=started.turn_id,
            before=TreeId(f"{1:040d}"),
            after=TreeId(f"{2:040d}"),
            files=[],
            patch="",
        )

    asyncio.run(scenario())


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


async def _closed_turn(dispatcher: Dispatcher, thread_id: UUID) -> UUID:
    """Run one scripted turn on *thread_id* to completion and return its id."""
    started = await dispatcher.dispatch(
        "thread.turn.start",
        {"thread_id": thread_id, "message": "Hello"},
        {Scope.THREAD_OPERATE},
    )
    assert isinstance(started, AcceptedResult)
    subscription = dispatcher.subscribe(
        "thread.subscribe",
        {"thread_id": thread_id},
        {Scope.THREAD_READ},
    )
    while True:
        event = cast(Event, await asyncio.wait_for(anext(subscription), timeout=1))
        if event.turn_id == started.turn_id and is_turn_closing(event.payload):
            break
    await subscription.aclose()
    return started.turn_id


def test_a_revert_snapshots_around_the_restore_and_records_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = FakeSnapshotStore()
        dispatcher, thread_id = await _thread_on(tmp_path, ScriptedRunner(), snapshots)
        turn_id = await _closed_turn(dispatcher, thread_id)

        result = await dispatcher.dispatch(
            "thread.turn.revert",
            {"thread_id": thread_id, "turn_id": turn_id},
            {Scope.THREAD_OPERATE},
        )

        assert isinstance(result, AcceptedResult)
        assert result.thread_id == thread_id
        assert result.turn_id != turn_id
        assert snapshots.restored == [TreeId(f"{1:040d}")]
        assert snapshots.refs[2:] == [
            SnapshotRef(f"refs/kinby/snapshots/{thread_id}/{result.turn_id}/before"),
            SnapshotRef(f"refs/kinby/snapshots/{thread_id}/{result.turn_id}/after"),
        ]
        events = EventLog(tmp_path).stored(thread_id)
        assert events[-1].turn_id == result.turn_id
        assert events[-1].sequence == result.sequence
        assert events[-1].payload == WorkspaceReverted(
            target_turn_id=turn_id,
            previous=TreeId(f"{3:040d}"),
            restored=TreeId(f"{4:040d}"),
        )

    asyncio.run(scenario())


def test_a_revert_is_refused_while_a_turn_runs_on_another_thread(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = FakeSnapshotStore()
        runner = WaitingRunner()
        dispatcher, thread_id = await _thread_on(tmp_path, runner, snapshots)
        other = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(other, ThreadCreateResult)
        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": other.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        await runner.started.wait()
        captures = len(snapshots.refs)

        result = await dispatcher.dispatch(
            "thread.turn.revert",
            {"thread_id": thread_id, "turn_id": started.turn_id},
            {Scope.THREAD_OPERATE},
        )
        runner.release.set()

        assert isinstance(result, ErrorEnvelope)
        assert result.code is ErrorCode.INSTANCE_BUSY
        assert snapshots.restored == []
        assert len(snapshots.refs) == captures

    asyncio.run(scenario())


def test_a_revert_is_refused_while_an_approval_is_parked(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = FakeSnapshotStore()
        dispatcher, thread_id = await _thread_on(tmp_path, ParkingRunner(), snapshots)
        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": thread_id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        events = await _closed_turn_events(dispatcher, thread_id, count=2)
        assert isinstance(events[-1].payload, ApprovalRequested)
        captures = len(snapshots.refs)

        result = await dispatcher.dispatch(
            "thread.turn.revert",
            {"thread_id": thread_id, "turn_id": started.turn_id},
            {Scope.THREAD_OPERATE},
        )

        assert isinstance(result, ErrorEnvelope)
        assert result.code is ErrorCode.INSTANCE_BUSY
        assert snapshots.restored == []
        assert len(snapshots.refs) == captures

    asyncio.run(scenario())


def test_a_revert_can_be_diffed_and_reverted_like_a_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = FakeSnapshotStore()
        dispatcher, thread_id = await _thread_on(tmp_path, ScriptedRunner(), snapshots)
        turn_id = await _closed_turn(dispatcher, thread_id)
        reverted = await dispatcher.dispatch(
            "thread.turn.revert",
            {"thread_id": thread_id, "turn_id": turn_id},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(reverted, AcceptedResult)

        difference = await dispatcher.dispatch(
            "thread.turn.diff",
            {"thread_id": thread_id, "turn_id": reverted.turn_id},
            {Scope.THREAD_READ},
        )
        reverted_again = await dispatcher.dispatch(
            "thread.turn.revert",
            {"thread_id": thread_id, "turn_id": reverted.turn_id},
            {Scope.THREAD_OPERATE},
        )

        assert isinstance(difference, ThreadTurnDiffResult)
        assert (difference.before, difference.after) == (TreeId(f"{3:040d}"), TreeId(f"{4:040d}"))
        assert isinstance(reverted_again, AcceptedResult)
        assert snapshots.restored == [TreeId(f"{1:040d}"), TreeId(f"{3:040d}")]

    asyncio.run(scenario())


def test_a_revert_needs_the_target_before_snapshot(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = SelectivelyFailingSnapshotStore(failed_capture=1)
        dispatcher, thread_id = await _thread_on(tmp_path, ScriptedRunner(), snapshots)
        turn_id = await _closed_turn(dispatcher, thread_id)

        result = await dispatcher.dispatch(
            "thread.turn.revert",
            {"thread_id": thread_id, "turn_id": turn_id},
            {Scope.THREAD_OPERATE},
        )

        assert isinstance(result, ErrorEnvelope)
        assert result.code is ErrorCode.SNAPSHOT_UNAVAILABLE
        assert snapshots.restored == []

    asyncio.run(scenario())


def test_a_revert_needs_an_open_snapshot_store(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher, thread_id = await _thread_on(tmp_path, ScriptedRunner(), None)
        turn_id = await _closed_turn(dispatcher, thread_id)

        result = await dispatcher.dispatch(
            "thread.turn.revert",
            {"thread_id": thread_id, "turn_id": turn_id},
            {Scope.THREAD_OPERATE},
        )

        assert isinstance(result, ErrorEnvelope)
        assert result.code is ErrorCode.SNAPSHOT_UNAVAILABLE

    asyncio.run(scenario())


def test_a_revert_that_cannot_restore_reports_snapshots_unavailable(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = FailingRestoreSnapshotStore()
        dispatcher, thread_id = await _thread_on(tmp_path, ScriptedRunner(), snapshots)
        turn_id = await _closed_turn(dispatcher, thread_id)
        events = len(EventLog(tmp_path).stored(thread_id))

        result = await dispatcher.dispatch(
            "thread.turn.revert",
            {"thread_id": thread_id, "turn_id": turn_id},
            {Scope.THREAD_OPERATE},
        )

        assert isinstance(result, ErrorEnvelope)
        assert result.code is ErrorCode.SNAPSHOT_UNAVAILABLE
        assert len(EventLog(tmp_path).stored(thread_id)) == events

    asyncio.run(scenario())


def test_a_turn_on_another_thread_is_refused_while_a_revert_runs(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = BlockingRestoreSnapshotStore()
        dispatcher, thread_id = await _thread_on(tmp_path, ScriptedRunner(), snapshots)
        turn_id = await _closed_turn(dispatcher, thread_id)
        other = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(other, ThreadCreateResult)
        reverting = asyncio.create_task(
            dispatcher.dispatch(
                "thread.turn.revert",
                {"thread_id": thread_id, "turn_id": turn_id},
                {Scope.THREAD_OPERATE},
            )
        )
        await asyncio.wait_for(snapshots.entered.wait(), timeout=1)
        captures = len(snapshots.refs)

        refused = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": other.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        snapshots.release.set()

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INSTANCE_BUSY
        assert len(snapshots.refs) == captures
        assert isinstance(await asyncio.wait_for(reverting, timeout=1), AcceptedResult)

    asyncio.run(scenario())


def test_a_revert_needs_only_the_target_before_snapshot(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = SelectivelyFailingSnapshotStore(failed_capture=2)
        dispatcher, thread_id = await _thread_on(tmp_path, ScriptedRunner(), snapshots)
        turn_id = await _closed_turn(dispatcher, thread_id)
        assert isinstance(EventLog(tmp_path).stored(thread_id)[-1].payload, TurnCompleted)

        result = await dispatcher.dispatch(
            "thread.turn.revert",
            {"thread_id": thread_id, "turn_id": turn_id},
            {Scope.THREAD_OPERATE},
        )

        assert isinstance(result, AcceptedResult)
        assert snapshots.restored == [TreeId(f"{1:040d}")]

    asyncio.run(scenario())


def test_a_revert_is_refused_while_an_approval_parks_behind_a_later_event(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        snapshots = FakeSnapshotStore()
        runner = ParksTheSecondTurnRunner()
        dispatcher, thread_id = await _thread_on(tmp_path, runner, snapshots)
        rated = await _closed_turn(dispatcher, thread_id)
        parked = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": thread_id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(parked, AcceptedResult)
        events = await _closed_turn_events(dispatcher, thread_id, count=4)
        assert isinstance(events[-1].payload, ApprovalRequested)
        # The rating lands after the approval, so the park is no longer the last event.
        await dispatcher.dispatch(
            "thread.turn.rate",
            {"thread_id": thread_id, "turn_id": rated, "verdict": "good", "reason": None},
            {Scope.THREAD_RATE},
        )
        assert isinstance(EventLog(tmp_path).stored(thread_id)[-1].payload, TurnRated)
        captures = len(snapshots.refs)

        result = await dispatcher.dispatch(
            "thread.turn.revert",
            {"thread_id": thread_id, "turn_id": rated},
            {Scope.THREAD_OPERATE},
        )

        assert isinstance(result, ErrorEnvelope)
        assert result.code is ErrorCode.INSTANCE_BUSY
        assert snapshots.restored == []
        assert len(snapshots.refs) == captures

    asyncio.run(scenario())


def test_a_revert_is_recorded_even_when_its_after_capture_fails(tmp_path: Path) -> None:
    async def scenario() -> None:
        # Captures 1 and 2 bracket the turn, 3 is the revert's before, 4 its after.
        snapshots = SelectivelyFailingSnapshotStore(failed_capture=4)
        dispatcher, thread_id = await _thread_on(tmp_path, ScriptedRunner(), snapshots)
        turn_id = await _closed_turn(dispatcher, thread_id)

        result = await dispatcher.dispatch(
            "thread.turn.revert",
            {"thread_id": thread_id, "turn_id": turn_id},
            {Scope.THREAD_OPERATE},
        )

        assert isinstance(result, AcceptedResult)
        assert snapshots.restored == [TreeId(f"{1:040d}")]
        assert EventLog(tmp_path).stored(thread_id)[-1].payload == WorkspaceReverted(
            target_turn_id=turn_id,
            previous=TreeId(f"{3:040d}"),
            restored=TreeId(f"{1:040d}"),
        )

    asyncio.run(scenario())


def test_thread_turn_revert_is_dispatched_under_thread_operate(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = FakeSnapshotStore()
        dispatcher, thread_id = await _thread_on(tmp_path, ScriptedRunner(), snapshots)
        turn_id = await _closed_turn(dispatcher, thread_id)

        denied = await dispatcher.dispatch(
            "thread.turn.revert",
            {"thread_id": thread_id, "turn_id": turn_id},
            {Scope.THREAD_READ},
        )

        assert denied == ErrorEnvelope(
            code=ErrorCode.PERMISSION_DENIED,
            message='Missing required scope "thread:operate".',
            retryable=False,
        )
        assert snapshots.restored == []

    asyncio.run(scenario())
