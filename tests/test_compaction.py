"""Compaction on demand and at the start of a turn over the compaction trigger: the model is sent
a compaction summary in place of the thread's older messages, and the newest turns whole, while
the thread keeps every message (ADR 0123)."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Self
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import (
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
    UsageMetadata,
)
from langchain_core.tools import StructuredTool
from pydantic import JsonValue

from kinby.contracts import (
    AcceptedResult,
    ErrorCode,
    ErrorEnvelope,
    Event,
    ModelCompleted,
    Payload,
    StatsGetResult,
    ThreadCompacted,
    ToolCall,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    Warning,
    is_turn_closing,
)
from kinby.core.dispatcher import ScheduledDispatcher
from kinby.core.events import EventLog
from kinby.core.turn_runner import LangGraphRunner
from kinby.instance import Instance, load_instance
from tests.helpers import historical_event_log
from tests.test_cleared_results import checkpoint_holds, closed
from tests.test_routines import signal_runtime
from tests.test_saved_outputs import instance_at, until
from tests.test_scheduler import call

type Call = tuple[str, dict[str, JsonValue]]


@dataclass(frozen=True)
class Sized:
    """A text answer whose call reports *input_tokens*: the thread's context size after it."""

    text: str
    input_tokens: int


#: A model step: the calls one model call makes, the text it answers with, what it raises, or an
#: event it waits on without answering.
type Step = Sequence[Call] | str | Sized | Exception | asyncio.Event

#: A user message of about 12,500 tokens: two of them pass what a compaction keeps.
LONG = 50_000
SKILL_BODY = "Deploy with the blue-green steps."
#: What the model reports each text answer, such as a summary, used.
TEXT_USAGE = UsageMetadata(input_tokens=20_000, output_tokens=1_000, total_tokens=21_000)


class CompactingModel:
    """Takes one step per model call: makes its calls, or answers with its text and
    ``TEXT_USAGE``. Keeps every request it is sent and the names of the tools it was given."""

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
        if isinstance(step, Exception):
            raise step
        if isinstance(step, asyncio.Event):
            await step.wait()
            return
        if isinstance(step, str):
            yield AIMessageChunk(content=step, usage_metadata=TEXT_USAGE)
            return
        if isinstance(step, Sized):
            usage = UsageMetadata(
                input_tokens=step.input_tokens,
                output_tokens=100,
                total_tokens=step.input_tokens + 100,
            )
            yield AIMessageChunk(content=step.text, usage_metadata=usage)
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


def test_a_compaction_on_demand_counts_apart_under_the_latest_turn_and_adds_to_its_cost(
    tmp_path: Path,
) -> None:
    model = CompactingModel([], [], "Summary one.")
    dispatcher, _ = signal_runtime(instance_at(tmp_path), model)

    async def scenario() -> object:
        thread_id = await new_thread(dispatcher)
        await turn(dispatcher, thread_id, "a" * LONG)
        await turn(dispatcher, thread_id, "b" * LONG)
        await compact(dispatcher, thread_id, "the release date")
        return await call(dispatcher, "stats.get", records=True)

    stats = asyncio.run(scenario())

    assert isinstance(stats, StatsGetResult)
    first, latest = stats.records
    assert (first.compaction_input_tokens, first.compaction_output_tokens) == (0, 0)
    assert (latest.compaction_input_tokens, latest.compaction_output_tokens) == (20_000, 1_000)
    assert (latest.input_tokens, latest.output_tokens) == (20_000, 1_000)
    assert latest.cost == pytest.approx(0.035)


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


def test_an_empty_summary_leaves_the_thread_uncompacted(tmp_path: Path) -> None:
    model = CompactingModel([], [], " \n", [])
    dispatcher, log = signal_runtime(instance_at(tmp_path), model)

    async def scenario() -> tuple[UUID, object]:
        thread_id = await new_thread(dispatcher)
        await turn(dispatcher, thread_id, "a" * LONG)
        await turn(dispatcher, thread_id, "b" * LONG)
        refused = await call(dispatcher, "thread.compact", thread_id=thread_id)
        await turn(dispatcher, thread_id, "Again.")
        return thread_id, refused

    thread_id, refused = asyncio.run(scenario())

    assert isinstance(refused, ErrorEnvelope)
    assert not any(
        isinstance(event.payload, ThreadCompacted) for event in log.thread_events(thread_id)
    )
    assert texts(model.requests[3]) == ["a" * LONG, "Done.", "b" * LONG, "Done.", "Again."]


#: A context size over the default compaction trigger of 100,000 tokens.
OVER = 150_000


def with_manifest(instance: Instance, text: str) -> Instance:
    manifest = instance.path / "kinby.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8") + text, encoding="utf-8")
    return load_instance(instance.path)


async def three_turns(dispatcher: ScheduledDispatcher) -> UUID:
    """Run two long turns and a third after them, and return the thread."""
    thread_id = await new_thread(dispatcher)
    await turn(dispatcher, thread_id, "a" * LONG)
    await turn(dispatcher, thread_id, "b" * LONG)
    await turn(dispatcher, thread_id, "Again.")
    return thread_id


def latest_turn(log: EventLog, thread_id: UUID) -> list[Event]:
    events = log.thread_events(thread_id)
    return [event for event in events if event.turn_id == events[-1].turn_id]


def compactions(log: EventLog, thread_id: UUID) -> list[ThreadCompacted]:
    return [
        event.payload
        for event in log.thread_events(thread_id)
        if isinstance(event.payload, ThreadCompacted)
    ]


def test_a_turn_over_the_trigger_compacts_once_before_its_first_model_call(
    tmp_path: Path,
) -> None:
    model = CompactingModel([], Sized("Done.", OVER), "Summary one.", Sized("Done.", OVER))
    dispatcher, log = signal_runtime(instance_at(tmp_path), model)

    thread_id = asyncio.run(three_turns(dispatcher))

    [summary_call] = model.requests[2]
    assert "a" * LONG in summary_call.text
    assert "b" * LONG not in summary_call.text
    [summary, *rest] = texts(model.requests[3])
    assert "Summary one." in summary
    assert rest == ["b" * LONG, "Done.", "Again."]
    assert len(model.requests) == 4
    events = latest_turn(log, thread_id)
    kinds = [type(event.payload) for event in events]
    assert kinds.index(ThreadCompacted) < kinds.index(ModelCompleted)
    assert kinds.count(ThreadCompacted) == 1
    assert kinds[-1] is TurnCompleted


def test_a_turn_under_the_trigger_does_not_compact(tmp_path: Path) -> None:
    model = CompactingModel([], Sized("Done.", 100_000), [])
    dispatcher, log = signal_runtime(instance_at(tmp_path), model)

    thread_id = asyncio.run(three_turns(dispatcher))

    assert len(model.requests) == 3
    assert texts(model.requests[2]) == ["a" * LONG, "Done.", "b" * LONG, "Done.", "Again."]
    assert compactions(log, thread_id) == []


def test_the_trigger_drops_to_the_window_less_the_reserve(tmp_path: Path) -> None:
    instance = with_manifest(
        instance_at(tmp_path),
        '[prices."openai:gpt-5"]\ninput = 1.25\noutput = 10\nwindow = 50000\n',
    )
    model = CompactingModel([], Sized("Done.", 40_000), "Summary one.", [])
    dispatcher, log = signal_runtime(instance, model)

    thread_id = asyncio.run(three_turns(dispatcher))

    [compaction] = compactions(log, thread_id)
    assert compaction.summary == "Summary one."


def test_a_trigger_set_in_the_manifest_applies(tmp_path: Path) -> None:
    instance = with_manifest(instance_at(tmp_path), "[compaction]\ntrigger = 30000\n")
    model = CompactingModel([], Sized("Done.", 40_000), "Summary one.", [])
    dispatcher, log = signal_runtime(instance, model)

    thread_id = asyncio.run(three_turns(dispatcher))

    [compaction] = compactions(log, thread_id)
    assert compaction.summary == "Summary one."


def test_with_the_trigger_off_no_turn_compacts_on_its_own(tmp_path: Path) -> None:
    instance = with_manifest(instance_at(tmp_path), "[compaction]\nautomatic = false\n")
    model = CompactingModel([], Sized("Done.", 500_000), [])
    dispatcher, log = signal_runtime(instance, model)

    thread_id = asyncio.run(three_turns(dispatcher))

    assert len(model.requests) == 3
    assert compactions(log, thread_id) == []


def test_a_failed_summary_call_is_recorded_and_the_turn_runs_uncompacted(
    tmp_path: Path,
) -> None:
    model = CompactingModel([], Sized("Done.", OVER), RuntimeError("provider down"), [])
    dispatcher, log = signal_runtime(instance_at(tmp_path), model)

    thread_id = asyncio.run(three_turns(dispatcher))

    assert texts(model.requests[3]) == ["a" * LONG, "Done.", "b" * LONG, "Done.", "Again."]
    events = latest_turn(log, thread_id)
    [warning] = [event.payload for event in events if isinstance(event.payload, Warning)]
    assert "provider down" in warning.message
    assert isinstance(events[-1].payload, TurnCompleted)
    assert compactions(log, thread_id) == []


def test_a_thread_compacted_since_its_latest_model_call_does_not_compact_again(
    tmp_path: Path,
) -> None:
    model = CompactingModel([], Sized("Done.", OVER), "Summary one.", [])
    dispatcher, log = signal_runtime(instance_at(tmp_path), model)

    async def scenario() -> UUID:
        thread_id = await new_thread(dispatcher)
        await turn(dispatcher, thread_id, "a" * LONG)
        await turn(dispatcher, thread_id, "b" * LONG)
        await compact(dispatcher, thread_id, "the release date")
        await turn(dispatcher, thread_id, "Again.")
        return thread_id

    thread_id = asyncio.run(scenario())

    assert len(model.requests) == 4
    assert len(compactions(log, thread_id)) == 1


def test_an_automatic_compaction_counts_apart_in_its_turn(tmp_path: Path) -> None:
    model = CompactingModel([], Sized("Done.", OVER), "Summary one.", [])
    dispatcher, _ = signal_runtime(instance_at(tmp_path), model)

    async def scenario() -> object:
        await three_turns(dispatcher)
        return await call(dispatcher, "stats.get", records=True)

    stats = asyncio.run(scenario())

    assert isinstance(stats, StatsGetResult)
    *_, latest = stats.records
    assert (latest.compaction_input_tokens, latest.compaction_output_tokens) == (20_000, 1_000)
    assert (latest.input_tokens, latest.output_tokens) == (20_000, 1_000)
    assert latest.cost == pytest.approx(0.035)
    assert stats.warnings == []


def test_a_stalled_summary_call_fails_the_turn_on_its_seconds_budget(tmp_path: Path) -> None:
    instance = with_manifest(instance_at(tmp_path), "[budgets]\nseconds = 0.5\n")
    model = CompactingModel([], Sized("Done.", OVER), asyncio.Event())
    dispatcher, log = signal_runtime(instance, model)

    thread_id = asyncio.run(three_turns(dispatcher))

    assert latest_turn(log, thread_id)[-1].payload == TurnFailed(
        code=ErrorCode.BUDGET_EXCEEDED,
        message="The turn exceeded the seconds budget of 0.5.",
    )
    assert len(model.requests) == 3


def test_the_daily_budget_counts_a_compaction_made_before_midnight_in_a_turn_closed_after(
    tmp_path: Path,
) -> None:
    instance = with_manifest(instance_at(tmp_path), "[budgets]\nusd_per_day = 1\n")
    today = date(2026, 11, 10)
    midnight = datetime(2026, 11, 10, tzinfo=UTC)
    thread_id, turn_id = uuid4(), uuid4()

    def at(moment: datetime, payload: Payload) -> Event:
        return Event(
            sequence=1, thread_id=thread_id, turn_id=turn_id, timestamp=moment, payload=payload
        )

    log = historical_event_log(
        instance.manifest.state_dir,
        [
            at(midnight - timedelta(minutes=2), TurnStarted(message="Go.", model="openai:gpt-5")),
            at(
                midnight - timedelta(minutes=1),
                ThreadCompacted(
                    summary="Summary one.",
                    boundary=2,
                    input_tokens=20_000,
                    output_tokens=1_000,
                    model="openai:gpt-5",
                ),
            ),
            at(midnight + timedelta(minutes=1), TurnCompleted(input_tokens=0, output_tokens=0)),
        ],
    )
    runner = LangGraphRunner(instance, event_log=log, today=lambda: today)

    budget = runner.prepare_for_turn().daily_budget

    assert budget is not None
    assert budget.cost.usd == pytest.approx(0.035)
