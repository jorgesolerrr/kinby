import asyncio
import signal
from io import StringIO
from pathlib import Path

from kinby.cli.client import ContractClient
from kinby.cli.repl import run_repl
from kinby.contracts import (
    THREAD_CREATE,
    MessageDelta,
    Scope,
    ThreadCreateCommand,
    ThreadCreateResult,
    ToolCall,
    ToolResult,
    Warning,
)
from kinby.core.dispatcher import TurnConfig, build_dispatcher
from kinby.core.turns import Emit, TurnOutcome, TurnRequest
from tests.helpers import does_not_park, fixed_model_name


class ReplRunner:
    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
        await emit(MessageDelta(text="Hi"))
        await emit(MessageDelta(text=" there"))
        return TurnOutcome()

    resume = does_not_park


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


def test_repl_streams_a_full_turn_through_the_dispatcher(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_model_name, ReplRunner()),
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


def test_repl_interrupts_a_running_turn_on_ctrl_c(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = InterruptibleReplRunner()
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_model_name, runner),
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
            turns=TurnConfig(fixed_model_name, ToolEventRunner()),
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


def test_repl_starts_another_turn_on_an_existing_thread(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_model_name, ReplRunner()),
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
