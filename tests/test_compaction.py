"""Compaction on demand: the model is sent a compaction summary in place of the thread's older
messages, and the newest turns whole, while the thread keeps every message (ADR 0123)."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Self
from uuid import UUID

from langchain_core.messages import AIMessageChunk, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import JsonValue

from kinby.contracts import (
    AcceptedResult,
    ErrorCode,
    ErrorEnvelope,
    Event,
    ModelCompleted,
    ThreadCompacted,
    ToolCall,
    is_turn_closing,
)
from kinby.core.dispatcher import ScheduledDispatcher
from kinby.instance import Instance
from tests.test_cleared_results import checkpoint_holds, closed
from tests.test_routines import signal_runtime
from tests.test_saved_outputs import instance_at, until
from tests.test_scheduler import call

type Call = tuple[str, dict[str, JsonValue]]
#: A model step: the calls one model call makes, or the text it answers with.
type Step = Sequence[Call] | str

#: A user message of about 12,500 tokens: two of them pass what a compaction keeps.
LONG = 50_000
SKILL_BODY = "Deploy with the blue-green steps."


class CompactingModel:
    """Takes one step per model call: makes its calls, or answers with its text. Keeps every
    request it is sent and the names of the tools it was given."""

    def __init__(self, *steps: Step) -> None:
        self._steps = list(steps)
        self.requests: list[list[BaseMessage]] = []
        self.tools: set[str] = set()

    def bind_tools(self, tools: Sequence[StructuredTool]) -> Self:
        self.tools |= {tool.name for tool in tools}
        return self

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.requests.append(list(messages))
        step = self._steps.pop(0)
        if isinstance(step, str):
            yield AIMessageChunk(content=step)
            return
        yield AIMessageChunk(
            content="" if step else "Done.",
            tool_calls=[
                {
                    "id": f"call-{len(self.requests)}-{index}",
                    "name": name,
                    "args": arguments,
                    "type": "tool_call",
                }
                for index, (name, arguments) in enumerate(step)
            ],
        )


def with_skill(instance: Instance) -> Instance:
    skill = instance.path / "skills" / "deploying" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        f"---\nname: deploying\ndescription: How to deploy.\n---\n{SKILL_BODY}\n", encoding="utf-8"
    )
    return instance


async def turn(dispatcher: ScheduledDispatcher, thread_id: UUID, message: str) -> None:
    started = await call(dispatcher, "thread.turn.start", thread_id=thread_id, message=message)
    assert isinstance(started, AcceptedResult)
    await closed(dispatcher, thread_id, started.turn_id)


async def new_thread(dispatcher: ScheduledDispatcher) -> UUID:
    thread = await call(dispatcher, "thread.create", title="Deploy")
    return thread.id


async def compact(dispatcher: ScheduledDispatcher, thread_id: UUID, focus: str) -> Event:
    accepted = await call(dispatcher, "thread.compact", thread_id=thread_id, focus=focus)
    assert isinstance(accepted, AcceptedResult)
    return await until(dispatcher, thread_id, lambda event: event.sequence == accepted.sequence)


def texts(request: Sequence[BaseMessage]) -> list[str]:
    return [message.text for message in request[1:]]


def test_the_model_is_sent_the_summary_in_place_of_the_turns_before_the_boundary(
    tmp_path: Path,
) -> None:
    instance = with_skill(instance_at(tmp_path))
    model = CompactingModel(
        [("skill", {"name": "deploying"}), ("report", {"size": 5_000})],
        [],
        [],
        "Summary one.",
        [],
    )
    dispatcher, log = signal_runtime(instance, model)

    async def scenario() -> tuple[UUID, Event]:
        thread_id = await new_thread(dispatcher)
        await turn(dispatcher, thread_id, "a" * LONG)
        await turn(dispatcher, thread_id, "b" * LONG)
        compacted = await compact(dispatcher, thread_id, "the release date")
        await turn(dispatcher, thread_id, "Again.")
        return thread_id, compacted

    thread_id, compacted = asyncio.run(scenario())

    [summary_call] = model.requests[3]
    assert "a" * LONG in summary_call.text
    assert SKILL_BODY in summary_call.text
    assert "b" * LONG not in summary_call.text
    assert "the release date" in summary_call.text
    assert isinstance(compacted.payload, ThreadCompacted)
    assert compacted.payload.summary == "Summary one."
    events = log.thread_events(thread_id)
    recorded = events[events.index(compacted) - 1]
    assert isinstance(recorded.payload, ModelCompleted)
    assert recorded.turn_id == compacted.turn_id

    next_turn = model.requests[4]
    [summary, kept, answer, asked] = next_turn[1:]
    assert isinstance(summary, HumanMessage)
    assert "Summary one." in summary.text
    assert [kept.text, answer.text, asked.text] == ["b" * LONG, "Done.", "Again."]
    assert not any(isinstance(message, ToolMessage) for message in next_turn)
    assert checkpoint_holds(instance, thread_id, "a" * LONG)
    assert checkpoint_holds(instance, thread_id, SKILL_BODY)
    assert checkpoint_holds(instance, thread_id, "x" * 5_000)
    assert not any("compact" in name for name in model.tools)


def test_a_second_compaction_summarizes_the_first_summary_and_the_turns_since(
    tmp_path: Path,
) -> None:
    model = CompactingModel([], [], "Summary one.", [], "Summary two.", [])
    dispatcher, _ = signal_runtime(instance_at(tmp_path), model)

    async def scenario() -> None:
        thread_id = await new_thread(dispatcher)
        await turn(dispatcher, thread_id, "a" * LONG)
        await turn(dispatcher, thread_id, "b" * LONG)
        await compact(dispatcher, thread_id, "the release date")
        await turn(dispatcher, thread_id, "c" * LONG)
        await compact(dispatcher, thread_id, "the release date")
        await turn(dispatcher, thread_id, "Again.")

    asyncio.run(scenario())

    [second_summary_call] = model.requests[4]
    assert "Summary one." in second_summary_call.text
    assert "b" * LONG in second_summary_call.text
    assert "a" * LONG not in second_summary_call.text
    assert "c" * LONG not in second_summary_call.text
    [summary, *rest] = texts(model.requests[5])
    assert "Summary two." in summary
    assert "Summary one." not in summary
    assert rest == ["c" * LONG, "Done.", "Again."]


def test_a_thread_whose_turns_all_fit_has_nothing_to_compact(tmp_path: Path) -> None:
    model = CompactingModel([])
    dispatcher, _ = signal_runtime(instance_at(tmp_path), model)

    async def scenario() -> object:
        thread_id = await new_thread(dispatcher)
        await turn(dispatcher, thread_id, "Go.")
        return await call(dispatcher, "thread.compact", thread_id=thread_id)

    refused = asyncio.run(scenario())

    assert isinstance(refused, ErrorEnvelope)
    assert refused.code is ErrorCode.INVALID_ARGUMENT
    assert len(model.requests) == 1


def test_a_thread_with_a_running_turn_does_not_compact(tmp_path: Path) -> None:
    model = CompactingModel([("wait", {})])
    dispatcher, _ = signal_runtime(instance_at(tmp_path), model)

    async def scenario() -> object:
        thread_id = await new_thread(dispatcher)
        await call(dispatcher, "thread.turn.start", thread_id=thread_id, message="Go.")
        await until(dispatcher, thread_id, lambda event: isinstance(event.payload, ToolCall))
        refused = await call(dispatcher, "thread.compact", thread_id=thread_id)
        await call(dispatcher, "thread.turn.interrupt", thread_id=thread_id)
        await until(dispatcher, thread_id, lambda event: is_turn_closing(event.payload))
        return refused

    refused = asyncio.run(scenario())

    assert isinstance(refused, ErrorEnvelope)
    assert refused.code is ErrorCode.THREAD_BUSY
