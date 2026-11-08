"""Saved outputs: a tool result over the cap reaches the model cut, its whole output kept in a
folder for its turn until the turn closes (ADR 0121)."""

import asyncio
import re
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import Self
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import JsonValue

from kinby.contracts import (
    AcceptedResult,
    ApprovalRequested,
    Event,
    GateOutcome,
    ThreadCreateResult,
    ToolGated,
    ToolResult,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    is_turn_closing,
)
from kinby.core.dispatcher import ScheduledDispatcher, TurnConfig
from kinby.core.runtime import boot_instance
from kinby.core.saved_outputs import turn_outputs
from kinby.instance import Instance, load_instance
from tests.helpers import (
    fixed_permission_ceiling,
    fixed_turn_preparation,
    thread_events,
    turn_config_stub,
)
from tests.test_routines import signal_runtime
from tests.test_scheduler import ScriptedRunner, call

TOOLS = '''import asyncio

from kinby.plugins import tool


@tool(write=False)
def report(size: int) -> str:
    """A report of *size* characters."""
    return "x" * size


@tool(write=True)
def post(text: str) -> str:
    """Post one line."""
    return text


@tool(write=False)
async def wait() -> str:
    """Wait for a long time."""
    await asyncio.sleep(3600)
    return "waited"
'''

type Call = tuple[str, dict[str, JsonValue]]
#: A model step: the call it makes, one it builds from the last tool result it got, or None for
#: a model call that fails.
type Step = Call | Callable[[str], Call] | None

REPORT: Call = ("report", {"size": 40_000})
#: A call that asks in ask mode, so the turn parks on it.
POST: Call = ("post", {"text": "hi"})


class StepModel:
    """Makes one call per step, then finishes, and keeps each tool result it got."""

    def __init__(self, *steps: Step) -> None:
        self._steps = list(steps)
        self.results: list[str] = []

    def bind_tools(self, tools: Sequence[StructuredTool]) -> Self:
        return self

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        if isinstance(messages[-1], ToolMessage):
            self.results.append(messages[-1].text)
        if not self._steps:
            yield AIMessageChunk(content="Done.")
            return
        step = self._steps.pop(0)
        if step is None:
            raise RuntimeError("The model is down.")
        name, arguments = step if isinstance(step, tuple) else step(self.results[-1])
        yield AIMessageChunk(
            content="",
            tool_calls=[
                {
                    "id": f"call-{len(self._steps)}",
                    "name": name,
                    "args": arguments,
                    "type": "tool_call",
                }
            ],
        )


def instance_at(path: Path) -> Instance:
    (path / "workspace").mkdir(parents=True)
    (path / "tools").mkdir()
    (path / "tools" / "report.py").write_text(TOOLS, encoding="utf-8")
    (path / "kinby.toml").write_text(
        'id = "test"\n[models]\nmain = "openai:gpt-5"\nrecap = "custom:unpriced"\n',
        encoding="utf-8",
    )
    return load_instance(path)


def saved_path(result: str) -> Path:
    found = re.search(r"saved at (\S+) ", result)
    assert found is not None, result
    return Path(found.group(1))


def outputs_of(instance: Instance, turn_id: UUID) -> Path:
    return turn_outputs(instance.manifest.state_dir, turn_id)


async def start(dispatcher: ScheduledDispatcher) -> AcceptedResult:
    thread = await call(dispatcher, "thread.create", title="Report")
    assert isinstance(thread, ThreadCreateResult)
    accepted = await call(dispatcher, "thread.turn.start", thread_id=thread.id, message="Go.")
    assert isinstance(accepted, AcceptedResult)
    return accepted


async def until(
    dispatcher: ScheduledDispatcher, thread_id: UUID, stop: Callable[[Event], bool]
) -> Event:
    """The thread's first event *stop* holds for."""
    items = await thread_events(dispatcher, {"thread_id": thread_id})
    async with asyncio.timeout(10):
        async for item in items:
            assert isinstance(item, Event)
            if stop(item):
                await items.aclose()
                return item
    raise AssertionError("The thread stopped streaming.")


def parked_or_closed(event: Event) -> bool:
    return isinstance(event.payload, ApprovalRequested) or is_turn_closing(event.payload)


async def stopped(dispatcher: ScheduledDispatcher, thread_id: UUID) -> Event:
    """Where the thread's turn stopped, once its task is done: an approval or its close."""
    event = await until(dispatcher, thread_id, parked_or_closed)
    await dispatcher.scheduler.drain()
    return event


async def tool_result(dispatcher: ScheduledDispatcher, thread_id: UUID) -> ToolResult:
    event = await until(dispatcher, thread_id, lambda each: isinstance(each.payload, ToolResult))
    assert isinstance(event.payload, ToolResult)
    return event.payload


def test_a_result_over_the_cap_reaches_the_model_cut_and_its_whole_output_is_saved(
    tmp_path: Path,
) -> None:
    instance = instance_at(tmp_path)
    model = StepModel(REPORT, POST)
    dispatcher, _ = signal_runtime(instance, model)

    async def scenario() -> tuple[AcceptedResult, Event, ToolResult]:
        accepted = await start(dispatcher)
        parked = await stopped(dispatcher, accepted.thread_id)
        return accepted, parked, await tool_result(dispatcher, accepted.thread_id)

    accepted, parked, result = asyncio.run(scenario())

    assert isinstance(parked.payload, ApprovalRequested)
    [received] = model.results
    assert received.startswith("x" * 30_000 + "\n")
    assert not received.startswith("x" * 30_001)
    assert "40,000 characters" in received
    assert all(program in received for program in ("grep", "jq", "head", "tail"))
    saved = saved_path(received)
    assert saved.read_text(encoding="utf-8") == "x" * 40_000
    assert saved.parent == outputs_of(instance, accepted.turn_id)
    assert not saved.is_relative_to(instance.manifest.workspace.path)
    assert (result.output, result.size) == (received, 40_000)


def test_a_result_within_the_cap_reaches_the_model_whole_and_saves_nothing(
    tmp_path: Path,
) -> None:
    instance = instance_at(tmp_path)
    within: Call = ("report", {"size": 30_000})
    model = StepModel(within, POST)
    dispatcher, _ = signal_runtime(instance, model)

    async def scenario() -> tuple[AcceptedResult, ToolResult]:
        accepted = await start(dispatcher)
        await stopped(dispatcher, accepted.thread_id)
        return accepted, await tool_result(dispatcher, accepted.thread_id)

    accepted, result = asyncio.run(scenario())

    assert model.results == ["x" * 30_000]
    assert result.size is None
    assert not outputs_of(instance, accepted.turn_id).exists()


def test_reading_the_turns_saved_output_runs_unasked_in_ask_mode(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    model = StepModel(
        REPORT, lambda result: ("bash", {"command": f"head -c 5 {saved_path(result)}"})
    )
    dispatcher, _ = signal_runtime(instance, model)

    async def scenario() -> Event:
        accepted = await start(dispatcher)
        await stopped(dispatcher, accepted.thread_id)
        return await until(
            dispatcher,
            accepted.thread_id,
            lambda each: isinstance(each.payload, ToolGated) and each.payload.name == "bash",
        )

    read = asyncio.run(scenario())

    assert isinstance(read.payload, ToolGated)
    assert (read.payload.action, read.payload.rule) == (GateOutcome.ALLOW, "bash.read")
    assert model.results[-1] == "xxxxx"


def test_a_completed_turn_leaves_no_saved_outputs(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    dispatcher, _ = signal_runtime(instance, StepModel(REPORT))

    async def scenario() -> tuple[Event, ToolResult]:
        accepted = await start(dispatcher)
        closed = await stopped(dispatcher, accepted.thread_id)
        return closed, await tool_result(dispatcher, accepted.thread_id)

    closed, result = asyncio.run(scenario())

    assert isinstance(closed.payload, TurnCompleted)
    assert result.size == 40_000
    assert not outputs_of(instance, closed.turn_id).exists()


def test_a_failed_turn_leaves_no_saved_outputs(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    dispatcher, _ = signal_runtime(instance, StepModel(REPORT, None))

    async def scenario() -> tuple[Event, ToolResult]:
        accepted = await start(dispatcher)
        closed = await stopped(dispatcher, accepted.thread_id)
        return closed, await tool_result(dispatcher, accepted.thread_id)

    closed, result = asyncio.run(scenario())

    assert isinstance(closed.payload, TurnFailed)
    assert result.size == 40_000
    assert not outputs_of(instance, closed.turn_id).exists()


def test_an_interrupted_turn_leaves_no_saved_outputs(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    dispatcher, _ = signal_runtime(instance, StepModel(REPORT, ("wait", {})))

    async def scenario() -> tuple[bool, Event]:
        accepted = await start(dispatcher)
        await until(
            dispatcher,
            accepted.thread_id,
            lambda each: isinstance(each.payload, ToolGated) and each.payload.name == "wait",
        )
        while_waiting = outputs_of(instance, accepted.turn_id).exists()
        await call(dispatcher, "thread.turn.interrupt", thread_id=accepted.thread_id)
        return while_waiting, await stopped(dispatcher, accepted.thread_id)

    while_waiting, closed = asyncio.run(scenario())

    assert while_waiting
    assert isinstance(closed.payload, TurnInterrupted)
    assert not outputs_of(instance, closed.turn_id).exists()


def test_a_parked_turn_keeps_its_saved_outputs_until_it_closes(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    dispatcher, _ = signal_runtime(instance, StepModel(REPORT, POST))

    async def scenario() -> tuple[bool, Event]:
        accepted = await start(dispatcher)
        await stopped(dispatcher, accepted.thread_id)
        while_parked = outputs_of(instance, accepted.turn_id).exists()
        await call(dispatcher, "thread.turn.interrupt", thread_id=accepted.thread_id)
        closed = await until(
            dispatcher, accepted.thread_id, lambda each: is_turn_closing(each.payload)
        )
        return while_parked, closed

    while_parked, closed = asyncio.run(scenario())

    assert while_parked
    assert isinstance(closed.payload, TurnInterrupted)
    assert not outputs_of(instance, closed.turn_id).exists()


def test_an_instance_start_removes_leftover_saved_outputs_but_a_parked_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = instance_at(tmp_path)
    dispatcher, _ = signal_runtime(instance, StepModel(REPORT, POST))
    monkeypatch.setattr(
        "kinby.core.runtime.turn_config",
        turn_config_stub(
            lambda: TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, ScriptedRunner())
        ),
    )
    leftover = outputs_of(instance, uuid4())

    async def scenario() -> Path:
        accepted = await start(dispatcher)
        await stopped(dispatcher, accepted.thread_id)
        leftover.mkdir()
        (leftover / "output.txt").write_text("left behind by a crash", encoding="utf-8")
        runtime = await boot_instance(instance)
        await runtime.stop_after_running_routine()
        return outputs_of(instance, accepted.turn_id)

    parked = asyncio.run(scenario())

    assert parked.exists()
    assert not leftover.exists()
