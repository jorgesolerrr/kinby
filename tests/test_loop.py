import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Self
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.tools import StructuredTool

from kinby.contracts import (
    AcceptedResult,
    ApprovalRequested,
    Event,
    MessageDelta,
    Payload,
    Scope,
    ThreadCreateResult,
    TurnStarted,
)
from kinby.core import LangGraphRunner, TurnConfig, build_dispatcher, turn_config
from kinby.core.turns import ParkedTurn, TurnOutcome, TurnRequest
from kinby.instance import Instance, load_instance

_MODEL = "openai:gpt-5"


def _load_test_instance(tmp_path: Path) -> Instance:
    instance_path = tmp_path / "instance"
    instance_path.mkdir()
    (instance_path / "kinby.toml").write_text(
        f'id = "test"\n\n[models]\nmain = "{_MODEL}"\n',
        encoding="utf-8",
    )
    return load_instance(instance_path)


class NoToolsModel:
    def bind_tools(self, tools: Sequence[StructuredTool]) -> Self:
        assert not tools
        return self


class StreamingChatModel(NoToolsModel):
    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        assert messages[-1].content == "Hello"
        yield AIMessageChunk(
            content="Hi",
            usage_metadata={"input_tokens": 4, "output_tokens": 0, "total_tokens": 4},
        )
        yield AIMessageChunk(
            content=" there",
            usage_metadata={"input_tokens": 0, "output_tokens": 2, "total_tokens": 2},
        )


class RememberingChatModel(NoToolsModel):
    def __init__(self) -> None:
        self.histories: list[list[object]] = []

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.histories.append([message.content for message in messages])
        reply = "First reply" if len(self.histories) == 1 else "Second reply"
        yield AIMessageChunk(content=reply)


class RecoveringChatModel(NoToolsModel):
    def __init__(self) -> None:
        self.histories: list[list[object]] = []

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.histories.append([message.content for message in messages])
        if len(self.histories) == 1:
            raise RuntimeError("provider unavailable")
        yield AIMessageChunk(content="Recovered")


class CompletingChatModel(NoToolsModel):
    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        yield AIMessageChunk(content="Done")


def test_runner_reloads_the_instance_model_between_turns(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance_path = tmp_path / "alice"
        instance_path.mkdir()
        manifest_path = instance_path / "kinby.toml"
        manifest_path.write_text(
            'id = "alice"\n\n[models]\nmain = "openai:gpt-5"\n',
            encoding="utf-8",
        )
        instance = load_instance(instance_path)
        requested_models: list[str] = []

        def init_model(model: str) -> CompletingChatModel:
            requested_models.append(model)
            return CompletingChatModel()

        runner = LangGraphRunner(instance, model_factory=init_model)
        dispatcher = build_dispatcher(
            instance.manifest.state_dir,
            turns=TurnConfig(runner.model_for_turn, runner),
        )
        created = await dispatcher.dispatch(
            "thread.create",
            {},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(created, ThreadCreateResult)

        started_payloads: list[TurnStarted] = []
        after_sequence = 0
        for message, model in (
            ("First", "openai:gpt-5"),
            ("Second", "anthropic:claude-sonnet-4-6"),
        ):
            manifest_path.write_text(
                f'id = "alice"\n\n[models]\nmain = "{model}"\n',
                encoding="utf-8",
            )
            accepted = await dispatcher.dispatch(
                "thread.turn.start",
                {"thread_id": created.id, "message": message},
                {Scope.THREAD_OPERATE},
            )
            assert isinstance(accepted, AcceptedResult)
            subscription = dispatcher.subscribe(
                "thread.subscribe",
                {"thread_id": created.id, "after_sequence": after_sequence},
                {Scope.THREAD_READ},
            )
            events = [await asyncio.wait_for(anext(subscription), timeout=1) for _ in range(3)]
            await subscription.aclose()
            started = events[0]
            assert isinstance(started, Event)
            assert isinstance(started.payload, TurnStarted)
            started_payloads.append(started.payload)
            last = events[-1]
            assert isinstance(last, Event)
            after_sequence = last.sequence

        assert [started.model for started in started_payloads] == [
            "openai:gpt-5",
            "anthropic:claude-sonnet-4-6",
        ]
        assert requested_models == [
            "openai:gpt-5",
            "anthropic:claude-sonnet-4-6",
        ]

    asyncio.run(scenario())


def test_turn_config_reapplies_the_session_model_override(tmp_path: Path) -> None:
    instance = _load_test_instance(tmp_path)
    configured = turn_config(
        instance,
        model_override="anthropic:claude-sonnet-4-6",
    )
    manifest_path = instance.path / "kinby.toml"

    manifest_path.write_text(
        'id = "test"\n\n[models]\nmain = "google:gemini-2.5-pro"\n',
        encoding="utf-8",
    )

    assert configured.model_for_turn() == "anthropic:claude-sonnet-4-6"


def test_langgraph_runner_streams_one_model_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        events: list[Payload] = []
        requested_models: list[str] = []

        def model_factory(model: str) -> StreamingChatModel:
            requested_models.append(model)
            return StreamingChatModel()

        async def emit(payload: Payload) -> Event:
            events.append(payload)
            return Event(
                sequence=len(events),
                thread_id=uuid4(),
                turn_id=uuid4(),
                payload=payload,
                timestamp=datetime.now(UTC),
            )

        runner = LangGraphRunner(_load_test_instance(tmp_path), model_factory=model_factory)
        outcome = await runner.run(
            TurnRequest(
                thread_id=uuid4(),
                turn_id=uuid4(),
                message="Hello",
                model=_MODEL,
            ),
            emit,
        )

        assert isinstance(outcome, TurnOutcome)
        assert requested_models == ["openai:gpt-5"]
        assert events == [MessageDelta(text="Hi"), MessageDelta(text=" there")]
        assert outcome.input_tokens == 4
        assert outcome.output_tokens == 2

    asyncio.run(scenario())


def test_failed_model_call_does_not_enter_checkpointed_history(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = RecoveringChatModel()
        runner = LangGraphRunner(_load_test_instance(tmp_path), model_factory=lambda _: model)
        thread_id = uuid4()

        async def emit(payload: Payload) -> Event:
            return Event(
                sequence=1,
                thread_id=thread_id,
                turn_id=uuid4(),
                payload=payload,
                timestamp=datetime.now(UTC),
            )

        with pytest.raises(RuntimeError, match="provider unavailable"):
            await runner.run(
                TurnRequest(
                    thread_id=thread_id,
                    turn_id=uuid4(),
                    message="Failed",
                    model=_MODEL,
                ),
                emit,
            )
        await runner.run(
            TurnRequest(
                thread_id=thread_id,
                turn_id=uuid4(),
                message="Retry",
                model=_MODEL,
            ),
            emit,
        )

        assert model.histories == [["Failed"], ["Retry"]]

    asyncio.run(scenario())


def test_langgraph_checkpointer_keeps_thread_messages_between_turns(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = RememberingChatModel()
        runner = LangGraphRunner(_load_test_instance(tmp_path), model_factory=lambda _: model)
        thread_id = uuid4()
        emitted = 0

        async def emit(payload: Payload) -> Event:
            nonlocal emitted
            emitted += 1
            return Event(
                sequence=emitted,
                thread_id=thread_id,
                turn_id=uuid4(),
                payload=payload,
                timestamp=datetime.now(UTC),
            )

        for message in ("First", "Second"):
            await runner.run(
                TurnRequest(
                    thread_id=thread_id,
                    turn_id=uuid4(),
                    message=message,
                    model=_MODEL,
                ),
                emit,
            )

        assert model.histories == [
            ["First"],
            ["First", "First reply", "Second"],
        ]

    asyncio.run(scenario())


def test_approval_hook_parks_until_resume(tmp_path: Path) -> None:
    async def scenario() -> None:
        events: list[Payload] = []
        model = StreamingChatModel()
        asked: list[str] = []

        async def asking_hook(turn: TurnRequest) -> str:
            asked.append(turn.message)
            return "May I continue?"

        async def emit(payload: Payload) -> Event:
            events.append(payload)
            return Event(
                sequence=len(events),
                thread_id=turn.thread_id,
                turn_id=turn.turn_id,
                payload=payload,
                timestamp=datetime.now(UTC),
            )

        turn = TurnRequest(
            thread_id=uuid4(),
            turn_id=uuid4(),
            message="Hello",
            model=_MODEL,
        )
        runner = LangGraphRunner(
            _load_test_instance(tmp_path),
            model_factory=lambda _: model,
            approval_hook=asking_hook,
        )
        parked = await runner.run(turn, emit)

        assert asked == ["Hello"]
        assert parked == ParkedTurn()
        assert len(events) == 1
        assert isinstance(events[0], ApprovalRequested)
        assert events[0].request == "May I continue?"
        assert isinstance(events[0].approval_id, UUID)

        resumed = await runner.resume(turn, "yes", emit)

        assert asked == ["Hello"]
        assert resumed.input_tokens == 4
        assert resumed.output_tokens == 2
        assert events[1:] == [MessageDelta(text="Hi"), MessageDelta(text=" there")]

    asyncio.run(scenario())
