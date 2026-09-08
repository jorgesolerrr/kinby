import asyncio
import signal
from io import StringIO
from pathlib import Path
from queue import Queue
from uuid import UUID

import pytest
from pydantic import JsonValue

from kinby.cli.client import ContractClient
from kinby.cli.repl import run_repl
from kinby.contracts import (
    THREAD_CREATE,
    THREAD_TURN_INTERRUPT,
    THREAD_TURN_START,
    AcceptedResult,
    ApprovalRequested,
    ChangeStatus,
    FileChange,
    GateDecider,
    GateOutcome,
    MemoryRecapped,
    MessageDelta,
    ModelCompleted,
    PermissionMode,
    Scope,
    ThreadCreateCommand,
    ThreadCreateResult,
    ThreadTurnInterruptCommand,
    ThreadTurnStartCommand,
    ToolCall,
    ToolGated,
    ToolResult,
    TreeId,
    TurnRated,
    TurnStarted,
    TurnVerdict,
    Warning,
    WorkspaceReverted,
)
from kinby.core import turns
from kinby.core.dispatcher import TurnConfig, build_dispatcher
from kinby.core.events import EventLog
from kinby.core.snapshots import WorkspaceDiff
from kinby.core.turns import ApprovalDecision, Emit, ParkedTurn, PreparedTurnRequest, TurnOutcome
from kinby.instance import FeedbackPolicy, load_instance
from kinby.memory import GraphStore, RecapWriter
from tests.helpers import (
    FakeSnapshotStore,
    cannot_restore,
    does_not_park,
    fixed_permission_ceiling,
    fixed_turn_preparation,
)


def test_diff_without_an_id_prints_the_last_closed_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = FakeSnapshotStore()
        snapshots.difference = WorkspaceDiff(
            [
                FileChange(
                    path="notes.md",
                    status=ChangeStatus.MODIFIED,
                    additions=1,
                    deletions=1,
                )
            ],
            "diff --git a/notes.md b/notes.md\n-old\n+new",
        )
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ReplRunner(),
                snapshots=snapshots,
            ),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        stdout = StringIO()
        stderr = StringIO()

        first_exit_code = await run_repl(
            client,
            created.id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO("Hello\n"),
            stdout=stdout,
            stderr=stderr,
        )
        exit_code = await run_repl(
            client,
            created.id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO("/diff\n"),
            stdout=stdout,
            stderr=stderr,
        )

        assert first_exit_code == 0
        assert exit_code == 0
        assert stdout.getvalue() == (
            "> Hi there\n"
            "> "
            "> modified notes.md +1 -1\n"
            "diff --git a/notes.md b/notes.md\n"
            "-old\n"
            "+new\n"
            "> "
        )
        assert stderr.getvalue() == ""

    asyncio.run(scenario())


def test_diff_accepts_a_full_id_and_a_unique_eight_character_prefix(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = FakeSnapshotStore()
        snapshots.difference = WorkspaceDiff([], "the patch")
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ReplRunner(),
                snapshots=snapshots,
            ),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        stdout = StringIO()
        stderr = StringIO()

        first_exit_code = await run_repl(
            client,
            created.id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO("Hello\n"),
            stdout=stdout,
            stderr=stderr,
        )
        turn_id = next(
            event.turn_id
            for event in EventLog(tmp_path).stored(created.id)
            if isinstance(event.payload, TurnStarted)
        )
        exit_code = await run_repl(
            client,
            created.id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO(f"/diff {str(turn_id)[:8]}\n/diff {turn_id}\n"),
            stdout=stdout,
            stderr=stderr,
        )

        assert first_exit_code == 0
        assert exit_code == 0
        assert stdout.getvalue() == "> Hi there\n> > the patch\n> the patch\n> "
        assert stderr.getvalue() == ""

    asyncio.run(scenario())


def test_diff_rejects_an_ambiguous_turn_id_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        turn_ids = [
            UUID("aaaaaaaa-1111-1111-1111-111111111111"),
            UUID("aaaaaaaa-2222-2222-2222-222222222222"),
        ]
        snapshots = FakeSnapshotStore()
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ReplRunner(),
                snapshots=snapshots,
            ),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        ids = iter(turn_ids)
        monkeypatch.setattr(turns, "uuid4", lambda: next(ids))
        await run_repl(
            client,
            created.id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO("First\nSecond\n"),
            stdout=StringIO(),
            stderr=StringIO(),
        )
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_repl(
            client,
            created.id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO("/diff aaaaaaaa\n"),
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 0
        assert stdout.getvalue() == "> > "
        assert stderr.getvalue() == (
            'INVALID_ARGUMENT: Turn id prefix "aaaaaaaa" matches more than one turn.\n'
        )
        assert snapshots.diffs == []

    asyncio.run(scenario())


def test_diff_prefix_for_a_running_turn_reports_thread_busy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        turn_id = UUID("aaaaaaaa-1111-1111-1111-111111111111")
        monkeypatch.setattr(turns, "uuid4", lambda: turn_id)
        runner = InterruptibleReplRunner()
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                runner,
                snapshots=FakeSnapshotStore(),
            ),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        started = await client.call(
            THREAD_TURN_START,
            ThreadTurnStartCommand(thread_id=created.id, message="Hello"),
        )
        assert isinstance(started, AcceptedResult)
        await runner.first_turn_started.wait()
        stderr = StringIO()

        exit_code = await run_repl(
            client,
            created.id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO("/diff aaaaaaaa\n"),
            stdout=StringIO(),
            stderr=stderr,
        )
        interrupted = await client.call(
            THREAD_TURN_INTERRUPT,
            ThreadTurnInterruptCommand(thread_id=created.id),
        )

        assert exit_code == 0
        assert isinstance(interrupted, AcceptedResult)
        assert stderr.getvalue() == (
            f'THREAD_BUSY: Thread "{created.id}" already has a running turn.\n'
        )

    asyncio.run(scenario())


def test_diff_rejects_a_short_turn_id_prefix(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = FakeSnapshotStore()
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ReplRunner(),
                snapshots=snapshots,
            ),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        stderr = StringIO()

        exit_code = await run_repl(
            client,
            created.id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO("Hello\n/diff 1111111\n"),
            stdout=StringIO(),
            stderr=stderr,
        )

        assert exit_code == 0
        assert stderr.getvalue() == (
            "INVALID_ARGUMENT: A turn id prefix must contain at least eight characters.\n"
        )
        assert snapshots.diffs == []

    asyncio.run(scenario())


def test_diff_reports_an_unmatched_turn_id_prefix_as_not_found(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = FakeSnapshotStore()
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ReplRunner(),
                snapshots=snapshots,
            ),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        stderr = StringIO()

        exit_code = await run_repl(
            client,
            created.id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO("Hello\n/diff deadbeef\n"),
            stdout=StringIO(),
            stderr=stderr,
        )

        assert exit_code == 0
        assert stderr.getvalue() == (
            'NOT_FOUND: Turn id prefix "deadbeef" was not found on this thread.\n'
        )
        assert snapshots.diffs == []

    asyncio.run(scenario())


def _one_file_diff() -> WorkspaceDiff:
    return WorkspaceDiff(
        [FileChange(path="notes.md", status=ChangeStatus.MODIFIED, additions=1, deletions=1)],
        "diff --git a/notes.md b/notes.md\n-old\n+new\n",
    )


async def _thread_after_one_turn(
    tmp_path: Path,
    snapshots: FakeSnapshotStore | None,
) -> tuple[ContractClient, UUID, UUID]:
    """A client on a thread whose one scripted turn has closed, plus that turn's id."""
    dispatcher = build_dispatcher(
        tmp_path,
        turns=TurnConfig(
            fixed_turn_preparation,
            fixed_permission_ceiling,
            ReplRunner(),
            snapshots=snapshots,
        ),
    )
    client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
    created = await client.call(THREAD_CREATE, ThreadCreateCommand())
    assert isinstance(created, ThreadCreateResult)
    exit_code = await run_repl(
        client,
        created.id,
        feedback=FeedbackPolicy.OFF,
        stdin=StringIO("Hello\n"),
        stdout=StringIO(),
        stderr=StringIO(),
    )
    assert exit_code == 0
    turn_id = next(
        event.turn_id
        for event in EventLog(tmp_path).stored(created.id)
        if isinstance(event.payload, TurnStarted)
    )
    return client, created.id, turn_id


def test_revert_prints_the_file_list_and_does_nothing_without_a_y(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = FakeSnapshotStore()
        snapshots.restore_difference = _one_file_diff()
        client, thread_id, turn_id = await _thread_after_one_turn(tmp_path, snapshots)
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_repl(
            client,
            thread_id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO("/revert\nn\n/revert\n\n"),
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 0
        prompt = f"modified notes.md +1 -1\nRevert 1 files to before {turn_id}? [y/N] "
        assert stdout.getvalue() == f"> {prompt}> {prompt}> "
        assert stderr.getvalue() == ""
        assert snapshots.restored == []
        assert len(snapshots.refs) == 2

    asyncio.run(scenario())


def test_revert_previews_every_change_from_the_current_workspace(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshots = FakeSnapshotStore()
        snapshots.restore_difference = WorkspaceDiff(
            [
                FileChange(
                    path="notes.md",
                    status=ChangeStatus.MODIFIED,
                    additions=1,
                    deletions=1,
                ),
                FileChange(
                    path="later.md",
                    status=ChangeStatus.DELETED,
                    additions=0,
                    deletions=1,
                ),
            ],
            "",
        )
        client, thread_id, turn_id = await _thread_after_one_turn(tmp_path, snapshots)
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_repl(
            client,
            thread_id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO("/revert\nn\n"),
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 0
        assert stdout.getvalue() == (
            "> modified notes.md +1 -1\n"
            "deleted later.md +0 -1\n"
            f"Revert 2 files to before {turn_id}? [y/N] > "
        )
        assert stderr.getvalue() == ""
        assert snapshots.restore_previews == [TreeId(f"{1:040d}")]
        assert snapshots.restored == []

    asyncio.run(scenario())


def test_revert_sends_the_command_on_y_and_then_targets_the_revert_by_default(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        snapshots = FakeSnapshotStore()
        snapshots.restore_difference = _one_file_diff()
        client, thread_id, turn_id = await _thread_after_one_turn(tmp_path, snapshots)
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_repl(
            client,
            thread_id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO("/revert\ny\n/revert\ny\n"),
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 0
        reverted = [
            event.payload
            for event in EventLog(tmp_path).stored(thread_id)
            if isinstance(event.payload, WorkspaceReverted)
        ]
        assert len(reverted) == 2
        revert_id = next(
            event.turn_id
            for event in EventLog(tmp_path).stored(thread_id)
            if isinstance(event.payload, WorkspaceReverted)
        )
        assert reverted[1].target_turn_id == revert_id
        assert snapshots.restored == [TreeId(f"{1:040d}"), TreeId(f"{3:040d}")]
        assert stdout.getvalue() == (
            f"> modified notes.md +1 -1\nRevert 1 files to before {turn_id}? [y/N] "
            f"Workspace reverted to before {turn_id}.\n"
            f"> modified notes.md +1 -1\nRevert 1 files to before {revert_id}? [y/N] "
            f"Workspace reverted to before {revert_id}.\n"
            "> "
        )
        assert stderr.getvalue() == ""

    asyncio.run(scenario())


def test_revert_renders_a_refusal_through_the_error_envelope(tmp_path: Path) -> None:
    async def scenario() -> None:
        client, thread_id, turn_id = await _thread_after_one_turn(tmp_path, None)
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_repl(
            client,
            thread_id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO("/revert\n"),
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 0
        assert stdout.getvalue() == "> > "
        assert stderr.getvalue() == (
            f'SNAPSHOT_UNAVAILABLE: Workspace snapshots are unavailable for turn "{turn_id}".\n'
        )

    asyncio.run(scenario())


class ReplRunner:
    def __init__(self) -> None:
        self.modes: list[PermissionMode] = []

    async def run(self, turn: PreparedTurnRequest, emit: Emit) -> TurnOutcome:
        self.modes.append(turn.permission_mode)
        await emit(MessageDelta(text="Hi"))
        await emit(MessageDelta(text=" there"))
        await emit(
            ModelCompleted(
                model="openai:gpt-5",
                input_tokens=1,
                output_tokens=1,
                duration_ms=1,
            )
        )
        return TurnOutcome()

    resume = does_not_park
    restore = cannot_restore


class InterruptibleReplRunner:
    def __init__(self) -> None:
        self.first_turn_started = asyncio.Event()
        self.turn_count = 0

    async def run(self, turn: PreparedTurnRequest, emit: Emit) -> TurnOutcome:
        self.turn_count += 1
        if self.turn_count == 1:
            self.first_turn_started.set()
            await asyncio.Event().wait()
        await emit(MessageDelta(text="Done"))
        return TurnOutcome()

    resume = does_not_park
    restore = cannot_restore


class ToolEventRunner:
    async def run(self, turn: PreparedTurnRequest, emit: Emit) -> TurnOutcome:
        await emit(
            ToolCall(
                call_id="call-1",
                name="weather",
                arguments={"city": "Quito"},
            )
        )
        await emit(
            ToolResult(
                call_id="call-1",
                name="weather",
                output="18 C",
                error=False,
            )
        )
        await emit(Warning(sources=("tools/weather.py",), message="Using cached tool set."))
        return TurnOutcome()

    resume = does_not_park
    restore = cannot_restore


class DeniedToolEventRunner:
    async def run(self, turn: PreparedTurnRequest, emit: Emit) -> TurnOutcome:
        await emit(ToolCall(call_id="bash-1", name="bash", arguments={}, write=True))
        await emit(
            ToolGated(
                call_id="bash-1",
                name="bash",
                action=GateOutcome.DENY,
                rule="bash.deny[0]",
                decided_by=GateDecider.POLICY,
            )
        )
        await emit(
            ToolResult(
                call_id="bash-1",
                name="bash",
                output='Tool "bash" was denied by policy rule "bash.deny[0]".',
                error=True,
            )
        )
        return TurnOutcome()

    resume = does_not_park
    restore = cannot_restore


class FailingReplRunner:
    async def run(self, turn: PreparedTurnRequest, emit: Emit) -> TurnOutcome:
        raise RuntimeError("provider unavailable")

    resume = does_not_park
    restore = cannot_restore


class ApprovalReplRunner:
    def __init__(self, arguments: dict[str, JsonValue] | None = None) -> None:
        self.decisions: list[ApprovalDecision] = []
        self.parked = asyncio.Event()
        self.parked_turn: PreparedTurnRequest | None = None
        self.arguments = arguments if arguments is not None else {"note": "remember me"}

    async def restore(self, thread_id: UUID, turn_id: UUID) -> PreparedTurnRequest | None:
        if (
            self.parked_turn is not None
            and self.parked_turn.thread_id == thread_id
            and self.parked_turn.turn_id == turn_id
        ):
            return self.parked_turn
        return None

    async def run(self, turn: PreparedTurnRequest, emit: Emit) -> ParkedTurn:
        self.parked_turn = turn
        await emit(
            ApprovalRequested(
                approval_id=UUID("11111111-1111-1111-1111-111111111111"),
                name="write_note",
                arguments=self.arguments,
                rule="mode.ask.write",
            )
        )
        self.parked.set()
        return ParkedTurn()

    async def resume(
        self,
        turn: PreparedTurnRequest,
        decision: ApprovalDecision,
        emit: Emit,
    ) -> TurnOutcome:
        self.decisions.append(decision)
        await emit(
            ToolCall(
                call_id="write-1",
                name="write_note",
                arguments=self.arguments,
            )
        )
        await emit(
            ToolResult(
                call_id="write-1",
                name="write_note",
                output="remember me",
                error=False,
            )
        )
        await emit(MessageDelta(text="Done"))
        return TurnOutcome()


class BlockingInput(StringIO):
    def __init__(self, *lines: str) -> None:
        super().__init__()
        self._lines: Queue[str] = Queue()
        for line in lines:
            self._lines.put(line)

    def readline(self, size: int = -1, /) -> str:
        return self._lines.get()

    def send(self, line: str) -> None:
        self._lines.put(line)


def test_repl_rates_a_completed_turn_good_with_a_reason(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ReplRunner(),
            ),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_repl(
            client,
            created.id,
            feedback=FeedbackPolicy.EVERY_TURN,
            stdin=StringIO("Hello\ng\nAnswered directly.\n"),
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 0
        assert stdout.getvalue() == (
            "> Hi there\nRate this turn [g]ood/[b]ad/[enter to skip]: Reason (optional): > "
        )
        assert stderr.getvalue() == ""
        ratings = [
            event.payload
            for event in EventLog(tmp_path).stored(created.id)
            if isinstance(event.payload, TurnRated)
        ]
        assert ratings == [TurnRated(verdict=TurnVerdict.GOOD, reason="Answered directly.")]

    asyncio.run(scenario())


def test_repl_rates_a_completed_turn_bad_without_a_reason(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ReplRunner(),
            ),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_repl(
            client,
            created.id,
            feedback=FeedbackPolicy.EVERY_TURN,
            stdin=StringIO("Hello\nb\n\n"),
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 0
        assert "Rate this turn [g]ood/[b]ad/[enter to skip]: Reason (optional): " in (
            stdout.getvalue()
        )
        assert stderr.getvalue() == ""
        ratings = [
            event.payload
            for event in EventLog(tmp_path).stored(created.id)
            if isinstance(event.payload, TurnRated)
        ]
        assert ratings == [TurnRated(verdict=TurnVerdict.BAD)]

    asyncio.run(scenario())


def test_repl_skips_a_rating_on_enter(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ReplRunner(),
            ),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        stdout = StringIO()

        exit_code = await run_repl(
            client,
            created.id,
            feedback=FeedbackPolicy.EVERY_TURN,
            stdin=StringIO("Hello\n\n"),
            stdout=stdout,
            stderr=StringIO(),
        )

        assert exit_code == 0
        assert "Rate this turn [g]ood/[b]ad/[enter to skip]: " in stdout.getvalue()
        assert not any(
            isinstance(event.payload, TurnRated) for event in EventLog(tmp_path).stored(created.id)
        )

    asyncio.run(scenario())


def test_repl_does_not_ask_for_a_failed_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                FailingReplRunner(),
            ),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_repl(
            client,
            created.id,
            feedback=FeedbackPolicy.EVERY_TURN,
            stdin=StringIO("Hello\n"),
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 0
        assert "Rate this turn" not in stdout.getvalue()
        assert "The model turn failed unexpectedly." in stderr.getvalue()
        assert not any(
            isinstance(event.payload, TurnRated) for event in EventLog(tmp_path).stored(created.id)
        )

    asyncio.run(scenario())


def test_repl_does_not_ask_for_an_interrupted_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = InterruptibleReplRunner()
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, runner),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        stdout = StringIO()
        repl = asyncio.create_task(
            run_repl(
                client,
                created.id,
                feedback=FeedbackPolicy.EVERY_TURN,
                stdin=StringIO("First\n"),
                stdout=stdout,
                stderr=StringIO(),
            )
        )
        await asyncio.wait_for(runner.first_turn_started.wait(), timeout=1)

        signal.raise_signal(signal.SIGINT)
        exit_code = await asyncio.wait_for(repl, timeout=1)

        assert exit_code == 0
        assert stdout.getvalue() == "> (interrupted)\n> "
        assert "Rate this turn" not in stdout.getvalue()

    asyncio.run(scenario())


def test_repl_streams_a_full_turn_through_the_dispatcher(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ReplRunner(),
            ),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_repl(
            client,
            created.id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO("Hello\n"),
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 0
        assert stdout.getvalue() == "> Hi there\n> "
        assert stderr.getvalue() == ""

    asyncio.run(scenario())


def test_repl_does_not_print_recap_events(tmp_path: Path) -> None:
    async def scenario() -> None:
        state_dir = tmp_path / ".state"
        event_log = EventLog(state_dir)
        (tmp_path / "kinby.toml").write_text(
            ('id = "test"\n\n[models]\nmain = "openai:main"\n\n[memory]\nrecap = "off"\n'),
            encoding="utf-8",
        )
        recap = RecapWriter(event_log, GraphStore(tmp_path), load_instance(tmp_path))
        dispatcher = build_dispatcher(
            state_dir,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ReplRunner(),
                recap,
            ),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        stdin = BlockingInput("Hello\n")
        stdout = StringIO()
        stderr = StringIO()
        repl = asyncio.create_task(
            run_repl(
                client,
                created.id,
                feedback=FeedbackPolicy.OFF,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
            )
        )
        for _ in range(20):
            if any(
                isinstance(event.payload, MemoryRecapped) for event in event_log.stored(created.id)
            ):
                break
            await asyncio.sleep(0)
        else:
            raise AssertionError("the first recap did not finish")

        stdin.send("Again\n")
        stdin.send("")
        exit_code = await asyncio.wait_for(repl, timeout=1)
        await asyncio.wait_for(recap.drain(), timeout=1)

        assert exit_code == 0
        assert stdout.getvalue() == "> Hi there\n> Hi there\n> "
        assert stderr.getvalue() == ""

    asyncio.run(scenario())


def test_repl_pins_the_mode_before_starting_the_next_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = ReplRunner()
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, runner),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_repl(
            client,
            created.id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO("/mode auto\nHello\n"),
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 0
        assert runner.modes == [PermissionMode.AUTO]
        assert stdout.getvalue() == "> Permission mode set to auto.\n> Hi there\n> "
        assert stderr.getvalue() == ""

    asyncio.run(scenario())


def test_repl_interrupts_a_running_turn_on_ctrl_c(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = InterruptibleReplRunner()
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, runner),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        stdout = StringIO()
        stderr = StringIO()
        repl = asyncio.create_task(
            run_repl(
                client,
                created.id,
                feedback=FeedbackPolicy.OFF,
                stdin=StringIO("First\nSecond\n"),
                stdout=stdout,
                stderr=stderr,
            )
        )
        await asyncio.wait_for(runner.first_turn_started.wait(), timeout=1)

        signal.raise_signal(signal.SIGINT)
        exit_code = await asyncio.wait_for(repl, timeout=1)

        assert exit_code == 0
        assert stdout.getvalue() == "> (interrupted)\n> Done\n> "
        assert stderr.getvalue() == ""

    asyncio.run(scenario())


def test_repl_renders_tool_and_warning_events(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ToolEventRunner(),
            ),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_repl(
            client,
            created.id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO("Hello\n"),
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 0
        assert stdout.getvalue() == (
            '> [tool.call] weather {"city": "Quito"}\n[tool.result] weather (ok): 18 C\n\n> '
        )
        assert stderr.getvalue() == "[warning] tools/weather.py: Using cached tool set.\n"

    asyncio.run(scenario())


def test_repl_renders_a_denial_from_the_gate_event(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                DeniedToolEventRunner(),
            ),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_repl(
            client,
            created.id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO("Do not run this\n"),
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 0
        assert stdout.getvalue() == (
            '> [tool.call] bash {}\n[tool.gated] bash denied by policy rule "bash.deny[0]"\n\n> '
        )
        assert stderr.getvalue() == ""

    asyncio.run(scenario())


def test_repl_answers_a_parked_approval(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = ApprovalReplRunner()
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, runner),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await asyncio.wait_for(
            run_repl(
                client,
                created.id,
                feedback=FeedbackPolicy.OFF,
                stdin=StringIO("Remember this\nyes\n"),
                stdout=stdout,
                stderr=stderr,
            ),
            timeout=1,
        )

        assert exit_code == 0
        assert runner.decisions == [ApprovalDecision.APPROVE]
        assert stdout.getvalue() == (
            '> Approve write_note {"note": "remember me"} under rule "mode.ask.write"? '
            "[yes/no] "
            '[tool.call] write_note {"note": "remember me"}\n'
            "[tool.result] write_note (ok): remember me\nDone\n> "
        )
        assert stderr.getvalue() == ""

    asyncio.run(scenario())


def test_repl_renders_a_multiline_approval_argument_as_a_block(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = ApprovalReplRunner({"content": "first line\nsecond line\nthird line"})
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, runner),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await asyncio.wait_for(
            run_repl(
                client,
                created.id,
                feedback=FeedbackPolicy.OFF,
                stdin=StringIO("Write this\nyes\n"),
                stdout=stdout,
                stderr=stderr,
            ),
            timeout=1,
        )

        assert exit_code == 0
        assert runner.decisions == [ApprovalDecision.APPROVE]
        assert stdout.getvalue() == (
            '> Approve write_note under rule "mode.ask.write":\n'
            "content:\n"
            "  first line\n"
            "  second line\n"
            "  third line\n"
            "[yes/no] "
            '[tool.call] write_note {"content": "first line\\nsecond line\\nthird line"}\n'
            "[tool.result] write_note (ok): remember me\nDone\n> "
        )
        assert stderr.getvalue() == ""

    asyncio.run(scenario())


def test_repl_renders_mixed_approval_arguments_in_key_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = ApprovalReplRunner(
            {
                "name": "morning",
                "enabled": False,
                "content": "first line\nsecond line",
            }
        )
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, runner),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await asyncio.wait_for(
            run_repl(
                client,
                created.id,
                feedback=FeedbackPolicy.OFF,
                stdin=StringIO("Write this\nyes\n"),
                stdout=stdout,
                stderr=stderr,
            ),
            timeout=1,
        )

        assert exit_code == 0
        assert runner.decisions == [ApprovalDecision.APPROVE]
        assert stdout.getvalue() == (
            '> Approve write_note under rule "mode.ask.write":\n'
            "content:\n"
            "  first line\n"
            "  second line\n"
            "enabled: false\n"
            "name: morning\n"
            "[yes/no] "
            '[tool.call] write_note {"content": "first line\\nsecond line", '
            '"enabled": false, "name": "morning"}\n'
            "[tool.result] write_note (ok): remember me\nDone\n> "
        )
        assert stderr.getvalue() == ""

    asyncio.run(scenario())


def test_repl_interrupts_while_waiting_for_approval(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = ApprovalReplRunner()
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, runner),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        stdin = BlockingInput("Remember this\n")
        stdout = StringIO()
        stderr = StringIO()
        repl = asyncio.create_task(
            run_repl(
                client,
                created.id,
                feedback=FeedbackPolicy.OFF,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
            )
        )
        await asyncio.wait_for(runner.parked.wait(), timeout=1)

        signal.raise_signal(signal.SIGINT)
        for _ in range(10):
            if "(interrupted)" in stdout.getvalue():
                break
            await asyncio.sleep(0)
        stdin.send("")
        exit_code = await asyncio.wait_for(repl, timeout=1)

        assert exit_code == 0
        assert runner.decisions == []
        assert stdout.getvalue() == (
            '> Approve write_note {"note": "remember me"} under rule "mode.ask.write"? '
            "[yes/no] (interrupted)\n> "
        )
        assert stderr.getvalue() == ""

    asyncio.run(scenario())


def test_repl_starts_another_turn_on_an_existing_thread(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ReplRunner(),
            ),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        assert isinstance(created, ThreadCreateResult)
        first_out = StringIO()
        second_out = StringIO()
        stderr = StringIO()

        first = await run_repl(
            client,
            created.id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO("Hello\n"),
            stdout=first_out,
            stderr=stderr,
        )
        second = await run_repl(
            client,
            created.id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO("Again\n"),
            stdout=second_out,
            stderr=stderr,
        )

        assert first == 0
        assert second == 0
        assert first_out.getvalue() == "> Hi there\n> "
        assert second_out.getvalue() == "> Hi there\n> "
        assert stderr.getvalue() == ""

    asyncio.run(scenario())
