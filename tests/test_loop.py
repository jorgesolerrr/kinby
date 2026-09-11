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
    ApprovalRequested,
    ErrorCode,
    Event,
    MessageDelta,
    ModelCompleted,
    Payload,
    PermissionMode,
    Scope,
    SystemPrompt,
    ThreadCreateResult,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnStarted,
    is_turn_closing,
)
from kinby.core import Dispatcher, LangGraphRunner, TurnConfig, build_dispatcher, turn_config
from kinby.core.events import EventLog
from kinby.core.snapshots import SNAPSHOTS_DIR
from kinby.core.turn_runner import ChatModel
from kinby.core.turns import PreparedTurnRequest, TurnContext, TurnOutcome, TurnRequest
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
            "routine_delete",
            "routine_list",
            "routine_read",
            "routine_set_enabled",
            "routine_write",
            "skill",
            "skill_delete",
            "skill_write",
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


class TwoCallChatModel(CoreSkillModel):
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
                usage_metadata={
                    "input_tokens": 5,
                    "output_tokens": 1,
                    "total_tokens": 6,
                    "input_token_details": {"cache_read": 3, "cache_creation": 1},
                },
            )
            return
        yield AIMessageChunk(
            content="Done",
            usage_metadata={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        )


class CapturingTwoCallChatModel(TwoCallChatModel):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[tuple[BaseMessage, ...]] = []

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.requests.append(tuple(messages))
        async for chunk in super().astream(messages):
            yield chunk


class FailingSecondCallChatModel(TwoCallChatModel):
    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        if self.calls == 1:
            self.calls += 1
            raise RuntimeError("provider unavailable")
        async for chunk in super().astream(messages):
            yield chunk


class InterruptibleSecondCallChatModel(TwoCallChatModel):
    def __init__(self) -> None:
        super().__init__()
        self.second_call_started = asyncio.Event()

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        if self.calls == 1:
            self.calls += 1
            self.second_call_started.set()
            await asyncio.Event().wait()
            return
        async for chunk in super().astream(messages):
            yield chunk


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


class ApprovalChatModel(CoreSkillModel):
    def __init__(self, delay: float = 0) -> None:
        self.calls = 0
        self.delay = delay

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.calls += 1
        await asyncio.sleep(self.delay)
        yield AIMessageChunk(
            content="",
            tool_calls=[
                {
                    "name": "remember",
                    "args": {
                        "description": "A budget test",
                        "subjects": ["budgets"],
                        "body": "The approval belongs to this turn.",
                    },
                    "id": "remember-1",
                    "type": "tool_call",
                }
            ],
            usage_metadata={
                "input_tokens": 5,
                "output_tokens": 1,
                "total_tokens": 6,
                "input_token_details": {"cache_read": 3, "cache_creation": 1},
            },
        )


class DelayedCompletingChatModel(CoreSkillModel):
    def __init__(self, delay: float = 0) -> None:
        self.calls = 0
        self.delay = delay

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.calls += 1
        await asyncio.sleep(self.delay)
        yield AIMessageChunk(content="Done")


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


async def _capture_two_call_turn(
    tmp_path: Path,
    model_name: str,
) -> CapturingTwoCallChatModel:
    model = CapturingTwoCallChatModel()
    runner = LangGraphRunner(_load_test_instance(tmp_path), model_factory=lambda _: model)

    async def emit(payload: Payload) -> Event:
        return Event(
            sequence=1,
            thread_id=uuid4(),
            turn_id=uuid4(),
            payload=payload,
            timestamp=datetime.now(UTC),
        )

    await runner.run(
        PreparedTurnRequest(
            thread_id=uuid4(),
            turn_id=uuid4(),
            message="Hello",
            model=model_name,
            permission_mode=PermissionMode.ASK,
            system_prompt=SystemPrompt("System prompt"),
        ),
        TurnContext(Budgets(), emit),
    )
    return model


def _cache_controls(message: BaseMessage) -> list[object]:
    if isinstance(message.content, str):
        return []
    return [
        block["cache_control"]
        for block in message.content
        if isinstance(block, dict) and "cache_control" in block
    ]


async def _park_budget_turn(dispatcher: Dispatcher, thread_id: UUID) -> Event:
    accepted = await dispatcher.dispatch(
        "thread.turn.start",
        {"thread_id": thread_id, "message": "Remember this"},
        {Scope.THREAD_OPERATE},
    )
    assert isinstance(accepted, AcceptedResult)
    subscription = dispatcher.subscribe(
        "thread.subscribe",
        {"thread_id": thread_id},
        {Scope.THREAD_READ},
    )
    while True:
        event = await asyncio.wait_for(anext(subscription), timeout=GRAPH_EVENT_TIMEOUT)
        assert isinstance(event, Event)
        if isinstance(event.payload, ApprovalRequested):
            await subscription.aclose()
            return event


async def _resume_budget_turn(
    dispatcher: Dispatcher,
    thread_id: UUID,
    requested: Event,
) -> list[Event]:
    assert isinstance(requested.payload, ApprovalRequested)
    accepted = await dispatcher.dispatch(
        "thread.approval.respond",
        {
            "thread_id": thread_id,
            "approval_id": requested.payload.approval_id,
            "answer": "yes",
        },
        {Scope.THREAD_OPERATE},
    )
    assert isinstance(accepted, AcceptedResult)
    subscription = dispatcher.subscribe(
        "thread.subscribe",
        {"thread_id": thread_id, "after_sequence": requested.sequence},
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
            input_tokens=8,
            output_tokens=3,
        )
        assert model.calls == 2

    asyncio.run(scenario())


def test_runner_records_each_model_call_and_closes_with_their_totals(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher, thread_id = await _budget_session(tmp_path, TwoCallChatModel(), "")

        events = await _budget_turn_events(dispatcher, thread_id, "Work")

        calls = [event.payload for event in events if isinstance(event.payload, ModelCompleted)]
        assert len(calls) == 2
        assert calls[0] == ModelCompleted(
            model=_MODEL,
            input_tokens=5,
            output_tokens=1,
            cache_read_tokens=3,
            cache_creation_tokens=1,
            duration_ms=calls[0].duration_ms,
        )
        assert calls[0].duration_ms >= 0
        assert calls[1] == ModelCompleted(
            model=_MODEL,
            input_tokens=2,
            output_tokens=3,
            duration_ms=calls[1].duration_ms,
        )
        assert calls[1].duration_ms >= 0
        assert events[-1].payload == TurnCompleted(
            input_tokens=7,
            output_tokens=4,
            cache_read_tokens=3,
            cache_creation_tokens=1,
        )

    asyncio.run(scenario())


def test_failed_second_model_call_closes_with_the_first_calls_tokens(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher, thread_id = await _budget_session(tmp_path, FailingSecondCallChatModel(), "")

        events = await _budget_turn_events(dispatcher, thread_id, "Work")

        calls = [event.payload for event in events if isinstance(event.payload, ModelCompleted)]
        assert len(calls) == 1
        assert events[-1].payload == TurnFailed(
            code=ErrorCode.INTERNAL,
            message="The model turn failed unexpectedly.",
            input_tokens=5,
            output_tokens=1,
            cache_read_tokens=3,
            cache_creation_tokens=1,
        )

    asyncio.run(scenario())


def test_interrupted_turn_closes_with_the_model_call_tally(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = InterruptibleSecondCallChatModel()
        dispatcher, thread_id = await _budget_session(tmp_path, model, "")
        accepted = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": thread_id, "message": "Work"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(accepted, AcceptedResult)
        await asyncio.wait_for(model.second_call_started.wait(), timeout=GRAPH_EVENT_TIMEOUT)

        interrupted = await dispatcher.dispatch(
            "thread.turn.interrupt",
            {"thread_id": thread_id},
            {Scope.THREAD_OPERATE},
        )

        assert isinstance(interrupted, AcceptedResult)
        events = [
            event
            for event in EventLog(tmp_path / "bounded" / ".state").stored(thread_id)
            if event.turn_id == accepted.turn_id
        ]
        assert events[-1].payload == TurnInterrupted(
            input_tokens=5,
            output_tokens=1,
            cache_read_tokens=3,
            cache_creation_tokens=1,
        )

    asyncio.run(scenario())


def test_parked_turn_interrupt_closes_with_the_recorded_model_call_tally(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        dispatcher, thread_id = await _budget_session(tmp_path, ApprovalChatModel(), "")
        requested = await _park_budget_turn(dispatcher, thread_id)

        interrupted = await dispatcher.dispatch(
            "thread.turn.interrupt",
            {"thread_id": thread_id},
            {Scope.THREAD_OPERATE},
        )

        assert isinstance(interrupted, AcceptedResult)
        events = EventLog(tmp_path / "bounded" / ".state").stored(thread_id)
        assert events[-1].turn_id == requested.turn_id
        assert events[-1].payload == TurnInterrupted(
            input_tokens=5,
            output_tokens=1,
            cache_read_tokens=3,
            cache_creation_tokens=1,
        )

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


def test_steps_budget_does_not_reset_after_approval(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = ApprovalChatModel()
        dispatcher, thread_id = await _budget_session(tmp_path, model, "steps = 2")
        requested = await _park_budget_turn(dispatcher, thread_id)
        manifest_path = tmp_path / "bounded" / "kinby.toml"
        manifest_path.write_text(
            manifest_path.read_text(encoding="utf-8").replace("steps = 2", "steps = 100"),
            encoding="utf-8",
        )
        resumed_model = DelayedCompletingChatModel()
        instance = load_instance(tmp_path / "bounded")
        runner = LangGraphRunner(instance, model_factory=lambda _: resumed_model)
        restarted = build_dispatcher(
            instance.manifest.state_dir,
            turns=TurnConfig(
                runner.prepare_for_turn,
                runner.permission_ceiling,
                runner,
            ),
        )

        events = await _resume_budget_turn(restarted, thread_id, requested)

        assert events[-1].payload == TurnFailed(
            code=ErrorCode.BUDGET_EXCEEDED,
            message="The turn exceeded the steps budget of 2.",
            input_tokens=5,
            output_tokens=1,
            cache_read_tokens=3,
            cache_creation_tokens=1,
        )
        assert model.calls == 1
        assert resumed_model.calls == 0

    asyncio.run(scenario())


def test_seconds_budget_does_not_reset_after_approval(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = ApprovalChatModel(delay=0.12)
        dispatcher, thread_id = await _budget_session(tmp_path, model, "seconds = 0.2")
        requested = await _park_budget_turn(dispatcher, thread_id)
        resumed_model = DelayedCompletingChatModel(delay=0.12)
        instance = load_instance(tmp_path / "bounded")
        runner = LangGraphRunner(instance, model_factory=lambda _: resumed_model)
        restarted = build_dispatcher(
            instance.manifest.state_dir,
            turns=TurnConfig(
                runner.prepare_for_turn,
                runner.permission_ceiling,
                runner,
            ),
        )

        events = await _resume_budget_turn(restarted, thread_id, requested)

        assert events[-1].payload == TurnFailed(
            code=ErrorCode.BUDGET_EXCEEDED,
            message="The turn exceeded the seconds budget of 0.2.",
            input_tokens=5,
            output_tokens=1,
            cache_read_tokens=3,
            cache_creation_tokens=1,
        )
        assert model.calls == 1
        assert resumed_model.calls == 1

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
                for _ in range(4)
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
    async def scenario() -> None:
        instance = _load_test_instance(tmp_path)
        configured = await turn_config(
            instance,
            event_log=EventLog(instance.manifest.state_dir),
            model_override="anthropic:claude-sonnet-4-6",
        )
        manifest_path = instance.path / "kinby.toml"

        manifest_path.write_text(
            'id = "test"\n\n[models]\nmain = "google:gemini-2.5-pro"\n',
            encoding="utf-8",
        )

        preparation = configured.prepare_for_turn()

        assert preparation.model == "anthropic:claude-sonnet-4-6"
        assert preparation.default_mode is PermissionMode.ASK
        assert preparation.ceiling is PermissionMode.FULL_ACCESS

    asyncio.run(scenario())


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
            PreparedTurnRequest(
                thread_id=uuid4(),
                turn_id=uuid4(),
                message="Hello",
                model=_MODEL,
                permission_mode=PermissionMode.ASK,
                system_prompt=SystemPrompt("System prompt"),
            ),
            TurnContext(Budgets(), emit),
        )

        assert isinstance(outcome, TurnOutcome)
        assert requested_models == ["openai:gpt-5"]
        assert events[:2] == [MessageDelta(text="Hi"), MessageDelta(text=" there")]
        assert isinstance(events[2], ModelCompleted)
        assert outcome.input_tokens == 4
        assert outcome.output_tokens == 2

    asyncio.run(scenario())


def test_anthropic_requests_mark_the_stable_prefix_for_caching(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = await _capture_two_call_turn(tmp_path, "anthropic:claude-opus-5")

        assert len(model.requests) == 2
        for request in model.requests:
            assert isinstance(request[0], SystemMessage)
            assert _cache_controls(request[0]) == [{"type": "ephemeral"}]
            assert _cache_controls(request[-1]) == [{"type": "ephemeral"}]

    asyncio.run(scenario())


def test_non_anthropic_requests_have_no_cache_control(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = await _capture_two_call_turn(tmp_path, "openai:gpt-5")

        assert len(model.requests) == 2
        assert all(
            not _cache_controls(message) for request in model.requests for message in request
        )

    asyncio.run(scenario())


def test_old_checkpoint_request_prepares_with_a_system_prompt() -> None:
    stored = TurnRequest(
        thread_id=uuid4(),
        turn_id=uuid4(),
        message="Hello",
        model=_MODEL,
        permission_mode=PermissionMode.ASK,
    )

    prepared = stored.prepare(SystemPrompt("System prompt"))

    assert isinstance(prepared, PreparedTurnRequest)
    assert prepared.system_prompt == "System prompt"


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
                PreparedTurnRequest(
                    thread_id=thread_id,
                    turn_id=uuid4(),
                    message="Failed",
                    model=_MODEL,
                    permission_mode=PermissionMode.ASK,
                    system_prompt=SystemPrompt("System prompt"),
                ),
                TurnContext(Budgets(), emit),
            )
        await runner.run(
            PreparedTurnRequest(
                thread_id=thread_id,
                turn_id=uuid4(),
                message="Retry",
                model=_MODEL,
                permission_mode=PermissionMode.ASK,
                system_prompt=SystemPrompt("System prompt"),
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
                PreparedTurnRequest(
                    thread_id=thread_id,
                    turn_id=uuid4(),
                    message=message,
                    model=_MODEL,
                    permission_mode=PermissionMode.ASK,
                    system_prompt=SystemPrompt("System prompt"),
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
                PreparedTurnRequest(
                    thread_id=thread_id,
                    turn_id=uuid4(),
                    message=message,
                    model=_MODEL,
                    permission_mode=PermissionMode.ASK,
                    system_prompt=SystemPrompt("System prompt"),
                ),
                TurnContext(Budgets(), emit),
            )

        assert model.histories == [
            ["First"],
            ["First", "First reply", "Second"],
        ]

    asyncio.run(scenario())


def test_turn_config_opens_a_snapshot_store_from_the_manifest(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _load_test_instance(tmp_path)
        (instance.path / "workspace").mkdir()

        configured = await turn_config(
            instance,
            event_log=EventLog(instance.manifest.state_dir),
        )

        assert configured.snapshots is not None
        assert (instance.manifest.state_dir / SNAPSHOTS_DIR).is_dir()

    asyncio.run(scenario())


def test_turn_config_opens_no_snapshot_store_when_the_manifest_turns_them_off(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance_path = tmp_path / "instance"
        instance_path.mkdir()
        (instance_path / "kinby.toml").write_text(
            f'id = "test"\n\n[models]\nmain = "{_MODEL}"\n\n[workspace]\nsnapshots = false\n',
            encoding="utf-8",
        )
        instance = load_instance(instance_path)

        configured = await turn_config(
            instance,
            event_log=EventLog(instance.manifest.state_dir),
        )

        assert configured.snapshots is None
        assert not (instance.manifest.state_dir / SNAPSHOTS_DIR).exists()

    asyncio.run(scenario())
