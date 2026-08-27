import asyncio
from io import StringIO
from pathlib import Path

from kinby.cli.client import ContractClient
from kinby.cli.repl import run_repl
from kinby.contracts import EventType, Scope, ThreadCreateResult
from kinby.core.dispatcher import build_dispatcher
from kinby.core.turns import Emit, TurnOutcome, TurnRequest


class ReplRunner:
    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
        await emit(EventType.MESSAGE_DELTA, {"text": "Hi"})
        await emit(EventType.MESSAGE_DELTA, {"text": " there"})
        return TurnOutcome()


def test_repl_streams_a_full_turn_through_the_dispatcher(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            model="openai:gpt-5",
            runner=ReplRunner(),
        )
        client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
        created = await client.call("thread.create", {"title": None})
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
