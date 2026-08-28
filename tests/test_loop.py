import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk, BaseMessage
from pydantic import JsonValue

from kinby.contracts import Event, EventType
from kinby.core import LangGraphRunner
from kinby.core.turns import TurnRequest


class StreamingChatModel:
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


class RememberingChatModel:
    def __init__(self) -> None:
        self.histories: list[list[object]] = []

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.histories.append([message.content for message in messages])
        reply = "First reply" if len(self.histories) == 1 else "Second reply"
        yield AIMessageChunk(content=reply)


class RecoveringChatModel:
    def __init__(self) -> None:
        self.histories: list[list[object]] = []

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.histories.append([message.content for message in messages])
        if len(self.histories) == 1:
            raise RuntimeError("provider unavailable")
        yield AIMessageChunk(content="Recovered")


def test_langgraph_runner_streams_one_model_turn() -> None:
    async def scenario() -> None:
        events: list[tuple[EventType, dict[str, object]]] = []
        requested_models: list[str] = []

        def model_factory(model: str) -> StreamingChatModel:
            requested_models.append(model)
            return StreamingChatModel()

        async def emit(event_type: EventType, payload: Mapping[str, JsonValue]) -> Event:
            events.append((event_type, dict(payload)))
            return Event(
                sequence=len(events),
                thread_id=uuid4(),
                turn_id=uuid4(),
                type=event_type,
                payload=dict(payload),
                timestamp=datetime.now(UTC),
            )

        runner = LangGraphRunner("openai:gpt-5", model_factory=model_factory)
        outcome = await runner.run(
            TurnRequest(
                thread_id=uuid4(),
                turn_id=uuid4(),
                message="Hello",
            ),
            emit,
        )

        assert requested_models == ["openai:gpt-5"]
        assert events == [
            (EventType.MESSAGE_DELTA, {"text": "Hi"}),
            (EventType.MESSAGE_DELTA, {"text": " there"}),
        ]
        assert outcome.input_tokens == 4
        assert outcome.output_tokens == 2

    asyncio.run(scenario())


def test_failed_model_call_does_not_enter_checkpointed_history() -> None:
    async def scenario() -> None:
        model = RecoveringChatModel()
        runner = LangGraphRunner("openai:gpt-5", model_factory=lambda _: model)
        thread_id = uuid4()

        async def emit(event_type: EventType, payload: Mapping[str, JsonValue]) -> Event:
            return Event(
                sequence=1,
                thread_id=thread_id,
                turn_id=uuid4(),
                type=event_type,
                payload=dict(payload),
                timestamp=datetime.now(UTC),
            )

        with pytest.raises(RuntimeError, match="provider unavailable"):
            await runner.run(
                TurnRequest(thread_id=thread_id, turn_id=uuid4(), message="Failed"),
                emit,
            )
        await runner.run(
            TurnRequest(thread_id=thread_id, turn_id=uuid4(), message="Retry"),
            emit,
        )

        assert model.histories == [["Failed"], ["Retry"]]

    asyncio.run(scenario())


def test_langgraph_checkpointer_keeps_thread_messages_between_turns() -> None:
    async def scenario() -> None:
        model = RememberingChatModel()
        runner = LangGraphRunner("openai:gpt-5", model_factory=lambda _: model)
        thread_id = uuid4()
        emitted = 0

        async def emit(event_type: EventType, payload: Mapping[str, JsonValue]) -> Event:
            nonlocal emitted
            emitted += 1
            return Event(
                sequence=emitted,
                thread_id=thread_id,
                turn_id=uuid4(),
                type=event_type,
                payload=dict(payload),
                timestamp=datetime.now(UTC),
            )

        for message in ("First", "Second"):
            await runner.run(
                TurnRequest(
                    thread_id=thread_id,
                    turn_id=uuid4(),
                    message=message,
                ),
                emit,
            )

        assert model.histories == [
            ["First"],
            ["First", "First reply", "Second"],
        ]

    asyncio.run(scenario())
