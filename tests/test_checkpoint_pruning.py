"""Each thread keeps only its newest completed checkpoint, and anything after it: a turn parked
on an approval or a takeover still resumes after a restart."""

import asyncio
import sqlite3
from contextlib import closing
from uuid import UUID

from langchain_core.messages import AIMessageChunk, HumanMessage

from kinby.contracts import (
    ApprovalRequested,
    Event,
    TakeoverRequested,
    ToolResult,
    TurnCompleted,
    is_turn_closing,
)
from kinby.core.dispatcher import Dispatcher, TurnConfig, build_dispatcher
from kinby.core.turn_runner import LangGraphRunner
from kinby.instance import Instance, load_instance
from tests.helpers import thread_events
from tests.test_browser_tools import FakeBrowser
from tests.test_plugins import ScriptedModel
from tests.test_scheduler import call

_DONE = AIMessageChunk(content="Done.")


def _instance(tmp_path) -> Instance:
    path = tmp_path / "instance"
    path.mkdir()
    (path / "kinby.toml").write_text(
        'id = "test"\nbrowser = true\n\n[models]\nmain = "openai:gpt-5"\n', encoding="utf-8"
    )
    return load_instance(path)


def _runtime(instance: Instance, model: ScriptedModel) -> Dispatcher:
    browser = FakeBrowser()
    runner = LangGraphRunner(instance, model_factory=lambda _: model, browser=browser)
    return build_dispatcher(
        instance.manifest.state_dir,
        turns=TurnConfig(
            runner.prepare_for_turn, runner.permission_ceiling, runner, browser=browser
        ),
    )


def _calling(name: str, args: dict[str, object]) -> AIMessageChunk:
    return AIMessageChunk(
        content="", tool_calls=[{"name": name, "args": args, "id": "call-1", "type": "tool_call"}]
    )


async def _stop(dispatcher: Dispatcher, thread_id: UUID, after: int) -> list[Event]:
    """The thread's events after *after*, up to where its turn closes or parks."""
    items = await thread_events(dispatcher, {"thread_id": thread_id, "after_sequence": after})
    events: list[Event] = []
    async with asyncio.timeout(5):
        async for item in items:
            assert isinstance(item, Event)
            events.append(item)
            if is_turn_closing(item.payload) or isinstance(
                item.payload, ApprovalRequested | TakeoverRequested
            ):
                break
    await items.aclose()
    return events


async def _turn(dispatcher: Dispatcher, thread_id: UUID, message: str, after: int) -> Event:
    """Start a turn with *message*, and return where it closes or parks."""
    await call(dispatcher, "thread.turn.start", thread_id=thread_id, message=message)
    return (await _stop(dispatcher, thread_id, after))[-1]


def _checkpoints(instance: Instance, thread_id: UUID) -> int:
    path = instance.manifest.state_dir / "checkpoints.sqlite"
    with closing(sqlite3.connect(path)) as database:
        [(count,)] = database.execute(
            "SELECT count(*) FROM checkpoints WHERE thread_id = ?", (str(thread_id),)
        )
    return count


async def _parked_after_a_turn(
    instance: Instance, parking: AIMessageChunk
) -> tuple[UUID, Event, int]:
    """A thread with one finished turn and a second parked on the call *parking*, and how many
    checkpoints it holds while parked."""
    dispatcher = _runtime(instance, ScriptedModel([_DONE, parking]))
    created = await call(dispatcher, "thread.create")
    finished = await _turn(dispatcher, created.id, "First", after=0)
    parked = await _turn(dispatcher, created.id, "Second", after=finished.sequence)
    return created.id, parked, _checkpoints(instance, created.id)


def test_each_thread_keeps_one_checkpoint_over_several_turns(tmp_path) -> None:
    instance = _instance(tmp_path)
    model = ScriptedModel([_DONE] * 5)
    dispatcher = _runtime(instance, model)

    async def scenario() -> tuple[UUID, UUID]:
        first = await call(dispatcher, "thread.create")
        second = await call(dispatcher, "thread.create")
        after = {first.id: 0, second.id: 0}
        for thread_id, message in (
            (first.id, "One"),
            (second.id, "Two"),
            (first.id, "Three"),
            (second.id, "Four"),
            (first.id, "Five"),
        ):
            closed = await _turn(dispatcher, thread_id, message, after[thread_id])
            assert isinstance(closed.payload, TurnCompleted)
            after[thread_id] = closed.sequence
        return first.id, second.id

    first, second = asyncio.run(scenario())

    assert (_checkpoints(instance, first), _checkpoints(instance, second)) == (1, 1)
    asked = [message.text for message in model.messages[-1] if isinstance(message, HumanMessage)]
    assert asked == ["One", "Three", "Five"]


def test_a_parked_approval_survives_pruning_and_a_restart(tmp_path) -> None:
    instance = _instance(tmp_path)
    remember = _calling(
        "remember", {"description": "A fact", "subjects": ["facts"], "body": "Kept."}
    )

    async def scenario() -> tuple[int, list[Event], UUID]:
        thread_id, parked, while_parked = await _parked_after_a_turn(instance, remember)
        assert isinstance(parked.payload, ApprovalRequested)
        restarted = _runtime(instance, ScriptedModel([_DONE]))
        await call(
            restarted,
            "thread.approval.respond",
            thread_id=thread_id,
            approval_id=parked.payload.approval_id,
            decision="approve",
        )
        return while_parked, await _stop(restarted, thread_id, parked.sequence), thread_id

    while_parked, events, thread_id = asyncio.run(scenario())

    assert while_parked == 2
    [result] = [event.payload for event in events if isinstance(event.payload, ToolResult)]
    assert (result.name, result.error) == ("remember", False)
    assert isinstance(events[-1].payload, TurnCompleted)
    assert _checkpoints(instance, thread_id) == 1


def test_a_pending_takeover_survives_pruning_and_a_restart(tmp_path) -> None:
    instance = _instance(tmp_path)
    takeover = _calling("browser_takeover", {"reason": "Sign in with a code from your phone."})

    async def scenario() -> tuple[int, list[Event], UUID]:
        thread_id, parked, while_parked = await _parked_after_a_turn(instance, takeover)
        assert isinstance(parked.payload, TakeoverRequested)
        restarted = _runtime(instance, ScriptedModel([_DONE]))
        await call(
            restarted,
            "thread.takeover.resume",
            thread_id=thread_id,
            takeover_id=parked.payload.takeover_id,
        )
        return while_parked, await _stop(restarted, thread_id, parked.sequence), thread_id

    while_parked, events, thread_id = asyncio.run(scenario())

    assert while_parked == 2
    [result] = [event.payload for event in events if isinstance(event.payload, ToolResult)]
    assert (result.name, result.error) == ("browser_takeover", False)
    assert isinstance(events[-1].payload, TurnCompleted)
    assert _checkpoints(instance, thread_id) == 1
