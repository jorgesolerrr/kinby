"""Cleared results: the model is sent an earlier turn's large tool result as a stub, while the
thread keeps the result itself (ADR 0122)."""

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Sequence
from contextlib import closing
from pathlib import Path
from typing import Self
from uuid import UUID

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import JsonValue

from kinby.contracts import AcceptedResult, Event, ToolResult, is_turn_closing
from kinby.core.dispatcher import ScheduledDispatcher
from kinby.instance import Instance
from tests.helpers import thread_events
from tests.test_routines import signal_runtime
from tests.test_saved_outputs import instance_at, start, until
from tests.test_scheduler import call

type Call = tuple[str, dict[str, JsonValue]]


class RecordingModel:
    """Makes the calls of each step, one model call per step, and finishes when a step makes
    none. Keeps every request it is sent."""

    def __init__(self, *steps: Sequence[Call]) -> None:
        self._steps = list(steps)
        self.requests: list[list[BaseMessage]] = []

    def bind_tools(self, tools: Sequence[StructuredTool]) -> Self:
        return self

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.requests.append(list(messages))
        calls = self._steps.pop(0)
        yield AIMessageChunk(
            content="" if calls else "Done.",
            tool_calls=[
                {
                    "id": f"call-{len(self.requests)}-{index}",
                    "name": name,
                    "args": arguments,
                    "type": "tool_call",
                }
                for index, (name, arguments) in enumerate(calls)
            ],
        )


def report(size: int) -> Call:
    return ("report", {"size": size})


def results(request: Sequence[BaseMessage]) -> list[str]:
    return [message.text for message in request if isinstance(message, ToolMessage)]


async def closed(dispatcher: ScheduledDispatcher, thread_id: UUID, turn_id: UUID) -> None:
    await until(
        dispatcher,
        thread_id,
        lambda event: event.turn_id == turn_id and is_turn_closing(event.payload),
    )
    await dispatcher.scheduler.drain()


async def two_turns(dispatcher: ScheduledDispatcher) -> UUID:
    """Run the thread's first turn and a second one after it, and return the thread."""
    first = await start(dispatcher)
    await closed(dispatcher, first.thread_id, first.turn_id)
    second = await call(
        dispatcher, "thread.turn.start", thread_id=first.thread_id, message="Again."
    )
    assert isinstance(second, AcceptedResult)
    await closed(dispatcher, first.thread_id, second.turn_id)
    return first.thread_id


def checkpoint_holds(instance: Instance, thread_id: UUID, text: str) -> bool:
    path = instance.manifest.state_dir / "checkpoints.sqlite"
    with closing(sqlite3.connect(path)) as database:
        blobs = database.execute(
            "SELECT checkpoint FROM checkpoints WHERE thread_id = ?", (str(thread_id),)
        )
        return any(text.encode() in blob for (blob,) in blobs)


async def tool_outputs(dispatcher: ScheduledDispatcher, thread_id: UUID) -> list[str]:
    outputs: list[str] = []
    items = await thread_events(dispatcher, {"thread_id": thread_id})
    async with asyncio.timeout(10):
        async for item in items:
            assert isinstance(item, Event)
            if isinstance(item.payload, ToolResult):
                outputs.append(item.payload.output)
            if len(outputs) == 3:
                break
    await items.aclose()
    return outputs


def test_an_earlier_turns_large_result_reaches_the_model_cleared(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    model = RecordingModel([report(5_000), report(1_000)], [], [report(2_000)], [])
    dispatcher, _ = signal_runtime(instance, model)

    async def scenario() -> tuple[UUID, list[str]]:
        thread_id = await two_turns(dispatcher)
        return thread_id, await tool_outputs(dispatcher, thread_id)

    thread_id, outputs = asyncio.run(scenario())

    first_call, last_call = model.requests[2:]
    [stub, small] = results(first_call)
    assert "report" in stub
    assert "5,000 characters" in stub
    assert "cleared" in stub
    assert "x" not in stub
    assert small == "x" * 1_000
    assert results(last_call) == [stub, small, "x" * 2_000]
    assert last_call[: len(first_call)] == first_call
    [earlier_calls] = [
        message.tool_calls
        for message in first_call
        if isinstance(message, AIMessage) and message.tool_calls
    ]
    assert [each["args"] for each in earlier_calls] == [{"size": 5_000}, {"size": 1_000}]
    assert outputs == ["x" * 5_000, "x" * 1_000, "x" * 2_000]
    assert checkpoint_holds(instance, thread_id, "x" * 5_000)
