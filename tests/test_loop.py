import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Self
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessageChunk, BaseMessage, SystemMessage
from langchain_core.tools import StructuredTool

from kinby.contracts import (
    AcceptedResult,
    ErrorCode,
    Event,
    MessageDelta,
    Payload,
    PermissionMode,
    Scope,
    ThreadCreateResult,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    is_turn_closing,
)
from kinby.core import Dispatcher, LangGraphRunner, TurnConfig, build_dispatcher, turn_config
from kinby.core.events import EventLog
from kinby.core.turn_runner import ChatModel
from kinby.core.turns import TurnContext, TurnOutcome, TurnPreparation, TurnRequest
from kinby.instance import Budgets, Instance, load_instance
from tests.helpers import GRAPH_EVENT_TIMEOUT

_MODEL = "openai:gpt-5"


def _load_test_instance(tmp_path: Path) -> Instance:
    instance_path = tmp_path / "instance"
    instance_path.mkdir()
    (instance_path / "kinby.toml").write_text(
        f'id = "test"\n\n[models]\nmain = "{_MODEL}"\n\n[tools]\ndefaults = false\n',
        encoding="utf-8",
    )
    return load_instance(instance_path)


class CoreSkillModel:
    def bind_tools(self, tools: Sequence[StructuredTool]) -> Self:
        assert [tool.name for tool in tools] == [
            "forget",
            "memory_open",
            "memory_search",
            "remember",
            "skill",
        ]
        return self


class StreamingChatModel(CoreSkillModel):
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


class RememberingChatModel(CoreSkillModel):
    def __init__(self) -> None:
        self.histories: list[list[object]] = []

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.histories.append(
            [message.content for message in messages if not isinstance(message, SystemMessage)]
        )
        reply = "First reply" if len(self.histories) == 1 else "Second reply"
        yield AIMessageChunk(content=reply)


class RecoveringChatModel(CoreSkillModel):
    def __init__(self) -> None:
        self.histories: list[list[object]] = []

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.histories.append(
            [message.content for message in messages if not isinstance(message, SystemMessage)]
        )
        if len(self.histories) == 1:
            raise RuntimeError("provider unavailable")
        yield AIMessageChunk(content="Recovered")


class CompletingChatModel(CoreSkillModel):
    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        yield AIMessageChunk(content="Done")


class LoopingChatModel(CoreSkillModel):
    def __init__(self) -> None:
        self.calls = 0
        self.histories: list[list[object]] = []

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.calls += 1
        self.histories.append(
            [message.content for message in messages if not isinstance(message, SystemMessage)]
        )
        if self.calls > 2:
            yield AIMessageChunk(content="Done")
            return
        yield AIMessageChunk(
            content="",
            tool_calls=[
                {
                    "name": "missing",
                    "args": {},
                    "id": f"missing-{self.calls}",
                    "type": "tool_call",
                }
            ],
        )


class TokenBudgetChatModel(CoreSkillModel):
    def __init__(self) -> None:
        self.calls = 0

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.calls += 1
        if self.calls == 1:
            yield AIMessageChunk(
                content="",
                tool_calls=[
                    {
                        "name": "missing",
                        "args": {},
                        "id": "missing-1",
                        "type": "tool_call",
                    }
                ],
                usage_metadata={"input_tokens": 5, "output_tokens": 1, "total_tokens": 6},
            )
            return
        yield AIMessageChunk(
            content="Over budget",
            usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        )


class StallingChatModel(CoreSkillModel):
    def __init__(self) -> None:
        self.calls = 0

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.calls += 1
        await asyncio.sleep(0.2)
        yield AIMessageChunk(content="Too late")


class ProviderTimeoutChatModel(CoreSkillModel):
    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        yield AIMessageChunk(content="")
        raise TimeoutError("provider timed out")


async def _budget_session(
    tmp_path: Path,
    model: ChatModel,
    budget: str,
) -> tuple[Dispatcher, UUID]:
    instance_path = tmp_path / "bounded"
    instance_path.mkdir()
    (instance_path / "kinby.toml").write_text(
        (
            f'id = "bounded"\n\n[models]\nmain = "{_MODEL}"\n\n'
            f"[tools]\ndefaults = false\n\n[budgets]\n{budget}\n"
        ),
        encoding="utf-8",
    )
    instance = load_instance(instance_path)
    runner = LangGraphRunner(instance, model_factory=lambda _: model)
    dispatcher = build_dispatcher(
        instance.manifest.state_dir,
        turns=TurnConfig(
            runner.prepare_for_turn,
            runner.permission_ceiling,
            runner,
        ),
    )
    created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
    assert isinstance(created, ThreadCreateResult)
    return dispatcher, created.id


async def _budget_turn_events(
    dispatcher: Dispatcher,
    thread_id: UUID,
    message: str,
    after_sequence: int = 0,
) -> list[Event]:
    accepted = await dispatcher.dispatch(
        "thread.turn.start",
        {"thread_id": thread_id, "message": message},
        {Scope.THREAD_OPERATE},
    )
    assert isinstance(accepted, AcceptedResult)
    subscription = dispatcher.subscribe(
        "thread.subscribe",
        {"thread_id": thread_id, "after_sequence": after_sequence},
        {Scope.THREAD_READ},
    )
    events: list[Event] = []
    while True:
        event = await asyncio.wait_for(anext(subscription), timeout=GRAPH_EVENT_TIMEOUT)
        assert isinstance(event, Event)
        events.append(event)
        if is_turn_closing(event.payload):
            await subscription.aclose()
            return events


def test_steps_budget_fails_a_tool_loop_without_another_model_call(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = LoopingChatModel()
        dispatcher, thread_id = await _budget_session(tmp_path, model, "steps = 3")

        events = await _budget_turn_events(dispatcher, thread_id, "Loop")

        failed = events[-1].payload
        assert failed == TurnFailed(
            code=ErrorCode.BUDGET_EXCEEDED,
            message="The turn exceeded the steps budget of 3.",
        )
        assert model.calls == 2

    asyncio.run(scenario())


def test_next_turn_after_a_tripped_budget_starts_from_the_clean_checkpoint(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        model = LoopingChatModel()
        dispatcher, thread_id = await _budget_session(tmp_path, model, "steps = 3")
        failed_events = await _budget_turn_events(dispatcher, thread_id, "Loop")

        next_events = await _budget_turn_events(
            dispatcher,
            thread_id,
            "Recover",
            failed_events[-1].sequence,
        )

        assert isinstance(next_events[-1].payload, TurnCompleted)
        assert model.histories[-1] == ["Recover"]

    asyncio.run(scenario())


def test_tokens_budget_fails_after_streamed_usage_passes_the_limit(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = TokenBudgetChatModel()
        dispatcher, thread_id = await _budget_session(tmp_path, model, "tokens = 10")

        events = await _budget_turn_events(dispatcher, thread_id, "Spend")

        assert events[-1].payload == TurnFailed(
            code=ErrorCode.BUDGET_EXCEEDED,
            message="The turn exceeded the tokens budget of 10.",
        )
        assert model.calls == 2

    asyncio.run(scenario())


def test_seconds_budget_fails_a_stalled_model_call(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = StallingChatModel()
        dispatcher, thread_id = await _budget_session(tmp_path, model, "seconds = 0.1")

        events = await _budget_turn_events(dispatcher, thread_id, "Wait")

        assert events[-1].payload == TurnFailed(
            code=ErrorCode.BUDGET_EXCEEDED,
            message="The turn exceeded the seconds budget of 0.1.",
        )
        assert model.calls == 1

    asyncio.run(scenario())


def test_seconds_budget_does_not_relabel_a_provider_timeout(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = ProviderTimeoutChatModel()
        dispatcher, thread_id = await _budget_session(tmp_path, model, "seconds = 1")

        events = await _budget_turn_events(dispatcher, thread_id, "Wait")

        assert events[-1].payload == TurnFailed(
            code=ErrorCode.INTERNAL,
            message="The model turn failed unexpectedly.",
        )

    asyncio.run(scenario())


def test_runner_reloads_the_instance_model_between_turns(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance_path = tmp_path / "alice"
        instance_path.mkdir()
        manifest_path = instance_path / "kinby.toml"
        manifest_path.write_text(
            'id = "alice"\n\n[models]\nmain = "openai:gpt-5"\n\n[tools]\ndefaults = false\n',
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
            turns=TurnConfig(
                runner.prepare_for_turn,
                runner.permission_ceiling,
                runner,
            ),
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
            events = [
                await asyncio.wait_for(anext(subscription), timeout=GRAPH_EVENT_TIMEOUT)
                for _ in range(3)
            ]
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
        event_log=EventLog(instance.manifest.state_dir),
        model_override="anthropic:claude-sonnet-4-6",
    )
    manifest_path = instance.path / "kinby.toml"

    manifest_path.write_text(
        'id = "test"\n\n[models]\nmain = "google:gemini-2.5-pro"\n',
        encoding="utf-8",
    )

    assert configured.prepare_for_turn() == TurnPreparation(
        model="anthropic:claude-sonnet-4-6",
        default_mode=PermissionMode.ASK,
        ceiling=PermissionMode.FULL_ACCESS,
    )


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
                permission_mode=PermissionMode.ASK,
            ),
            TurnContext(Budgets(), emit),
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
                    permission_mode=PermissionMode.ASK,
                ),
                TurnContext(Budgets(), emit),
            )
        await runner.run(
            TurnRequest(
                thread_id=thread_id,
                turn_id=uuid4(),
                message="Retry",
                model=_MODEL,
                permission_mode=PermissionMode.ASK,
            ),
            TurnContext(Budgets(), emit),
        )

        assert model.histories == [["Failed"], ["Retry"]]

    asyncio.run(scenario())


def test_runner_keeps_thread_messages_between_turns(tmp_path: Path) -> None:
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
                    permission_mode=PermissionMode.ASK,
                ),
                TurnContext(Budgets(), emit),
            )

        assert model.histories == [
            ["First"],
            ["First", "First reply", "Second"],
        ]

    asyncio.run(scenario())


def test_runner_keeps_thread_messages_after_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _load_test_instance(tmp_path)
        model = RememberingChatModel()
        thread_id = uuid4()

        async def emit(payload: Payload) -> Event:
            return Event(
                sequence=1,
                thread_id=thread_id,
                turn_id=uuid4(),
                payload=payload,
                timestamp=datetime.now(UTC),
            )

        for message in ("First", "Second"):
            runner = LangGraphRunner(instance, model_factory=lambda _: model)
            await runner.run(
                TurnRequest(
                    thread_id=thread_id,
                    turn_id=uuid4(),
                    message=message,
                    model=_MODEL,
                    permission_mode=PermissionMode.ASK,
                ),
                TurnContext(Budgets(), emit),
            )

        assert model.histories == [
            ["First"],
            ["First", "First reply", "Second"],
        ]

    asyncio.run(scenario())
