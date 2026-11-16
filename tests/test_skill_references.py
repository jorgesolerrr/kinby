import asyncio
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

from langchain_core.messages import AIMessageChunk, HumanMessage

from kinby.contracts import (
    AcceptedResult,
    ErrorCode,
    ErrorEnvelope,
    Event,
    Scope,
    ThreadCreateResult,
    ThreadListResult,
    ThreadTurnListResult,
    ToolCall,
    TurnCompleted,
    TurnStarted,
)
from kinby.core.dispatcher import Dispatcher, TurnConfig, build_dispatcher
from kinby.core.turn_runner import LangGraphRunner
from kinby.instance import Instance
from tests.helpers import GRAPH_EVENT_TIMEOUT, thread_events
from tests.test_plugins import ScriptedModel, _instance, _write_skill


def _skilled_instance(tmp_path: Path) -> Instance:
    instance = _instance(tmp_path, defaults=False)
    _write_skill(
        instance.path / "skills",
        directory="planning",
        name="planning",
        description="Plan work before changing files.",
        body="Write the plan first.",
    )
    _write_skill(
        instance.path / "skills",
        directory="review",
        name="review",
        description="Review a change.",
        body="Read the diff twice.",
    )
    return instance


def _dispatcher(instance: Instance, model: ScriptedModel) -> Dispatcher:
    runner = LangGraphRunner(instance, model_factory=lambda _: model)
    return build_dispatcher(
        instance.manifest.state_dir,
        turns=TurnConfig(runner.prepare_for_turn, runner.permission_ceiling, runner),
    )


async def _thread(dispatcher: Dispatcher) -> UUID:
    created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
    assert isinstance(created, ThreadCreateResult)
    return created.id


async def _events(dispatcher: Dispatcher, thread_id: UUID) -> list[Event]:
    """The thread's events up to its turn's close."""
    subscription = await thread_events(dispatcher, {"thread_id": thread_id, "after_sequence": 0})
    events: list[Event] = []
    while True:
        event = await asyncio.wait_for(anext(subscription), timeout=GRAPH_EVENT_TIMEOUT)
        assert isinstance(event, Event)
        events.append(event)
        if isinstance(event.payload, TurnCompleted):
            await subscription.aclose()
            return events


async def _start(
    dispatcher: Dispatcher, thread_id: UUID, message: str, skills: list[str]
) -> list[Event]:
    accepted = await dispatcher.dispatch(
        "thread.turn.start",
        {"thread_id": thread_id, "message": message, "skills": skills},
        {Scope.THREAD_OPERATE},
    )
    assert isinstance(accepted, AcceptedResult)
    return await _events(dispatcher, thread_id)


def _sent(model: ScriptedModel) -> str:
    """The message the model was sent for the turn."""
    *_, human = model.messages[0]
    assert isinstance(human, HumanMessage)
    return human.text


def _started(events: list[Event]) -> TurnStarted:
    return next(event.payload for event in events if isinstance(event.payload, TurnStarted))


def test_a_referenced_skills_body_comes_ahead_of_the_typed_text_which_is_all_the_thread_keeps(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        model = ScriptedModel([AIMessageChunk(content="Done")])
        dispatcher = _dispatcher(_skilled_instance(tmp_path), model)
        thread_id = await _thread(dispatcher)

        events = await _start(dispatcher, thread_id, "/planning the release", ["planning"])

        assert _sent(model) == (
            '<skill name="planning">\nWrite the plan first.\n</skill>\n\n/planning the release'
        )
        assert not any(isinstance(event.payload, ToolCall) for event in events)
        started = _started(events)
        assert started.message == "/planning the release"
        assert started.skills == ["planning"]
        listed = await dispatcher.dispatch("thread.list", {}, {Scope.THREAD_READ})
        assert isinstance(listed, ThreadListResult)
        assert [thread.title for thread in listed.threads] == ["/planning the release"]

    asyncio.run(scenario())


def test_a_message_referencing_two_skills_sends_both_bodies_in_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = ScriptedModel([AIMessageChunk(content="Done")])
        dispatcher = _dispatcher(_skilled_instance(tmp_path), model)
        thread_id = await _thread(dispatcher)

        events = await _start(dispatcher, thread_id, "Ship it", ["review", "planning"])

        assert _sent(model) == (
            '<skill name="review">\nRead the diff twice.\n</skill>\n\n'
            '<skill name="planning">\nWrite the plan first.\n</skill>\n\n'
            "Ship it"
        )
        assert _started(events).skills == ["review", "planning"]

    asyncio.run(scenario())


def test_a_slash_name_without_a_reference_is_plain_text(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = ScriptedModel([AIMessageChunk(content="Done")])
        dispatcher = _dispatcher(_skilled_instance(tmp_path), model)
        thread_id = await _thread(dispatcher)

        events = await _start(dispatcher, thread_id, "/planning the release", [])

        assert _sent(model) == "/planning the release"
        assert _started(events).skills == []

    asyncio.run(scenario())


def test_a_reference_to_a_skill_the_turn_cannot_load_refuses_the_command(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        model = ScriptedModel([])
        dispatcher = _dispatcher(_skilled_instance(tmp_path), model)
        thread_id = await _thread(dispatcher)
        refused = ErrorEnvelope(
            code=ErrorCode.NOT_FOUND,
            message='Skill "missing" is not available in this turn.',
            retryable=False,
        )

        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": thread_id, "message": "Go", "skills": ["planning", "missing"]},
            {Scope.THREAD_OPERATE},
        )
        asked = await dispatcher.dispatch(
            "thread.ask", {"message": "Go", "skills": ["missing"]}, {Scope.INSTANCE_LIFECYCLE}
        )

        assert started == refused
        assert asked == refused
        listed = await dispatcher.dispatch("thread.list", {}, {Scope.THREAD_READ})
        assert isinstance(listed, ThreadListResult)
        assert [thread.id for thread in listed.threads] == [thread_id]
        assert listed.threads[0].title is None
        turns = await dispatcher.dispatch(
            "thread.turn.list", {"thread_id": thread_id}, {Scope.THREAD_READ}
        )
        assert turns == ThreadTurnListResult(turn_ids=[])
        assert model.messages == []

    asyncio.run(scenario())


def test_an_ask_carries_its_references_to_the_model(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = ScriptedModel([AIMessageChunk(content="Done")])
        dispatcher = _dispatcher(_skilled_instance(tmp_path), model)
        payload: Mapping[str, object] = {"message": "Check my plan.", "skills": ["planning"]}

        accepted = await dispatcher.dispatch("thread.ask", payload, {Scope.INSTANCE_LIFECYCLE})

        assert isinstance(accepted, AcceptedResult)
        events = await _events(dispatcher, accepted.thread_id)
        assert _sent(model).startswith('<skill name="planning">\nWrite the plan first.\n</skill>')
        assert _sent(model).endswith("Check my plan.")
        assert _started(events).skills == ["planning"]

    asyncio.run(scenario())
