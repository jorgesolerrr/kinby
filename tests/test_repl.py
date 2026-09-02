import asyncio
import signal
from io import StringIO
from pathlib import Path
from queue import Queue
from uuid import UUID

from kinby.cli.client import ContractClient
from kinby.cli.repl import run_repl
from kinby.contracts import (
    THREAD_CREATE,
    ApprovalRequested,
    MemoryRecapped,
    MessageDelta,
    PermissionMode,
    Scope,
    ThreadCreateCommand,
    ThreadCreateResult,
    ToolCall,
    ToolResult,
    Warning,
)
from kinby.core.dispatcher import TurnConfig, build_dispatcher
from kinby.core.events import EventLog
from kinby.core.turns import ApprovalDecision, Emit, ParkedTurn, TurnOutcome, TurnRequest
from kinby.instance import load_instance
from kinby.memory import GraphStore, RecapWriter
from tests.helpers import (
    cannot_restore,
    does_not_park,
    fixed_permission_ceiling,
    fixed_turn_preparation,
)


class ReplRunner:
    def __init__(self) -> None:
        self.modes: list[PermissionMode] = []

    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
        self.modes.append(turn.permission_mode)
        await emit(MessageDelta(text="Hi"))
        await emit(MessageDelta(text=" there"))
        return TurnOutcome()

    resume = does_not_park
    restore = cannot_restore


class InterruptibleReplRunner:
    def __init__(self) -> None:
        self.first_turn_started = asyncio.Event()
        self.turn_count = 0

    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
        self.turn_count += 1
        if self.turn_count == 1:
            self.first_turn_started.set()
            await asyncio.Event().wait()
        await emit(MessageDelta(text="Done"))
        return TurnOutcome()

    resume = does_not_park
    restore = cannot_restore


class ToolEventRunner:
    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
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


class ApprovalReplRunner:
    def __init__(self) -> None:
        self.decisions: list[ApprovalDecision] = []
        self.parked = asyncio.Event()
        self.parked_turn: TurnRequest | None = None

    async def restore(self, thread_id: UUID, turn_id: UUID) -> TurnRequest | None:
        if (
            self.parked_turn is not None
            and self.parked_turn.thread_id == thread_id
            and self.parked_turn.turn_id == turn_id
        ):
            return self.parked_turn
        return None

    async def run(self, turn: TurnRequest, emit: Emit) -> ParkedTurn:
        self.parked_turn = turn
        await emit(
            ApprovalRequested(
                approval_id=UUID("11111111-1111-1111-1111-111111111111"),
                name="write_note",
                arguments={"note": "remember me"},
                rule="mode.ask.write",
            )
        )
        self.parked.set()
        return ParkedTurn()

    async def resume(
        self,
        turn: TurnRequest,
        decision: ApprovalDecision,
        emit: Emit,
    ) -> TurnOutcome:
        self.decisions.append(decision)
        await emit(
            ToolCall(
                call_id="write-1",
                name="write_note",
                arguments={"note": "remember me"},
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
            stdin=StringIO("Hello\n"),
            stdout=first_out,
            stderr=stderr,
        )
        second = await run_repl(
            client,
            created.id,
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
