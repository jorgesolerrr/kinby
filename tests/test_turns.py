import asyncio
from contextlib import suppress
from pathlib import Path
from typing import cast
from uuid import UUID

from kinby.contracts import (
    AcceptedResult,
    ApprovalRequested,
    ErrorCode,
    ErrorEnvelope,
    Event,
    EventType,
    MessageDelta,
    Payload,
    Scope,
    ThreadCreateResult,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnStarted,
)
from kinby.core.dispatcher import Dispatcher, TurnConfig, build_dispatcher
from kinby.core.events import EventLog
from kinby.core.turns import Emit, ParkedTurn, TurnOutcome, TurnRequest
from tests.helpers import does_not_park

_APPROVAL_ID = UUID("11111111-1111-1111-1111-111111111111")


class ScriptedRunner:
    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
        assert turn.message == "Hello"
        await emit(MessageDelta(text="Hi"))
        await emit(MessageDelta(text=" there"))
        return TurnOutcome(input_tokens=4, output_tokens=2)

    resume = does_not_park


class WaitingRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
        self.started.set()
        await self.release.wait()
        return TurnOutcome()

    resume = does_not_park


class FailingRunner:
    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
        raise RuntimeError("provider unavailable")

    resume = does_not_park


class ParkingRunner:
    async def run(self, turn: TurnRequest, emit: Emit) -> ParkedTurn:
        await emit(ApprovalRequested(approval_id=_APPROVAL_ID, request="May I continue?"))
        return ParkedTurn()

    async def resume(self, turn: TurnRequest, answer: str, emit: Emit) -> TurnOutcome:
        assert answer == "yes"
        await emit(MessageDelta(text="Approved"))
        return TurnOutcome(input_tokens=3, output_tokens=1)


class CancellationSuppressingRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
        await emit(MessageDelta(text="Before interrupt"))
        self.started.set()
        with suppress(asyncio.CancelledError):
            await asyncio.Event().wait()
        await emit(MessageDelta(text="After interrupt"))
        return TurnOutcome()

    resume = does_not_park


class PausingEventLog(EventLog):
    def __init__(self, state_dir: Path) -> None:
        super().__init__(state_dir)
        self.turns_started = 0
        self.release = asyncio.Event()

    async def append(
        self,
        thread_id: UUID,
        turn_id: UUID,
        payload: Payload,
    ) -> Event:
        if isinstance(payload, TurnStarted):
            self.turns_started += 1
            await self.release.wait()
        return await super().append(thread_id, turn_id, payload)


def test_turn_streams_and_replays_through_the_dispatcher(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig("openai:gpt-5", ScriptedRunner()),
        )
        created = await dispatcher.dispatch(
            "thread.create",
            {"title": "Chat"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(created, ThreadCreateResult)

        accepted = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(accepted, AcceptedResult)
        assert accepted.thread_id == created.id
        assert accepted.sequence == 1

        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id},
            {Scope.THREAD_READ},
        )
        events = [await asyncio.wait_for(anext(subscription), timeout=1) for _ in range(4)]
        await subscription.aclose()

        typed_events = cast(list[Event], events)
        assert [event.type for event in typed_events] == [
            EventType.TURN_STARTED,
            EventType.MESSAGE_DELTA,
            EventType.MESSAGE_DELTA,
            EventType.TURN_COMPLETED,
        ]
        assert [event.sequence for event in typed_events] == [1, 2, 3, 4]
        assert typed_events[0].payload == TurnStarted(message="Hello", model="openai:gpt-5")
        assert typed_events[1].payload == MessageDelta(text="Hi")
        assert typed_events[2].payload == MessageDelta(text=" there")
        assert typed_events[3].payload == TurnCompleted(input_tokens=4, output_tokens=2)

        replay = build_dispatcher(tmp_path).subscribe(
            "thread.subscribe",
            {"thread_id": created.id, "after_sequence": 0},
            {Scope.THREAD_READ},
        )
        replayed = [await asyncio.wait_for(anext(replay), timeout=1) for _ in events]
        await replay.aclose()

        assert replayed == events

    asyncio.run(scenario())


def test_start_rejects_a_second_turn_while_the_first_is_running(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = WaitingRunner()
        dispatcher = build_dispatcher(tmp_path, turns=TurnConfig("openai:gpt-5", runner))
        created = await dispatcher.dispatch(
            "thread.create",
            {},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(created, ThreadCreateResult)

        first = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "First"},
            {Scope.THREAD_OPERATE},
        )
        await asyncio.wait_for(runner.started.wait(), timeout=1)
        second = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Second"},
            {Scope.THREAD_OPERATE},
        )

        assert isinstance(first, AcceptedResult)
        assert second == ErrorEnvelope(
            code=ErrorCode.THREAD_BUSY,
            message=f'Thread "{created.id}" already has a running turn.',
            retryable=True,
        )

        runner.release.set()
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id, "after_sequence": 1},
            {Scope.THREAD_READ},
        )
        completed = await asyncio.wait_for(anext(subscription), timeout=1)
        await subscription.aclose()
        assert isinstance(completed, Event)
        assert completed.type is EventType.TURN_COMPLETED

    asyncio.run(scenario())


def test_interrupt_ends_the_running_turn_and_allows_another(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = WaitingRunner()
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig("openai:gpt-5", runner),
        )
        created = await dispatcher.dispatch(
            "thread.create",
            {},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(created, ThreadCreateResult)

        first = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "First"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(first, AcceptedResult)
        await asyncio.wait_for(runner.started.wait(), timeout=1)

        interrupted = await dispatcher.dispatch(
            "thread.turn.interrupt",
            {"thread_id": created.id},
            {Scope.THREAD_OPERATE},
        )

        assert interrupted == AcceptedResult(
            thread_id=created.id,
            turn_id=first.turn_id,
            sequence=2,
        )
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id},
            {Scope.THREAD_READ},
        )
        events = [await asyncio.wait_for(anext(subscription), timeout=1) for _ in range(2)]
        await subscription.aclose()
        assert [event.type for event in cast(list[Event], events)] == [
            EventType.TURN_STARTED,
            EventType.TURN_INTERRUPTED,
        ]
        assert cast(list[Event], events)[1].payload == TurnInterrupted()

        second = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Second"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(second, AcceptedResult)
        runner.release.set()

    asyncio.run(scenario())


def test_interrupt_rejects_a_thread_with_no_active_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig("openai:gpt-5", ScriptedRunner()),
        )
        created = await dispatcher.dispatch(
            "thread.create",
            {},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(created, ThreadCreateResult)

        result = await dispatcher.dispatch(
            "thread.turn.interrupt",
            {"thread_id": created.id},
            {Scope.THREAD_OPERATE},
        )

        assert result == ErrorEnvelope(
            code=ErrorCode.NO_ACTIVE_TURN,
            message=f'Thread "{created.id}" has no active turn.',
            retryable=False,
        )

    asyncio.run(scenario())


def test_interrupt_ends_the_turn_when_the_runner_suppresses_cancellation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runner = CancellationSuppressingRunner()
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig("openai:gpt-5", runner),
        )
        created = await dispatcher.dispatch(
            "thread.create",
            {},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(created, ThreadCreateResult)
        await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        await asyncio.wait_for(runner.started.wait(), timeout=1)

        interrupted = await dispatcher.dispatch(
            "thread.turn.interrupt",
            {"thread_id": created.id},
            {Scope.THREAD_OPERATE},
        )

        assert isinstance(interrupted, AcceptedResult)
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id},
            {Scope.THREAD_READ},
        )
        events = [await asyncio.wait_for(anext(subscription), timeout=1) for _ in range(3)]
        await subscription.aclose()
        assert [event.type for event in cast(list[Event], events)] == [
            EventType.TURN_STARTED,
            EventType.MESSAGE_DELTA,
            EventType.TURN_INTERRUPTED,
        ]
        assert cast(list[Event], events)[-1].payload == TurnInterrupted()

    asyncio.run(scenario())


def test_start_rejects_a_concurrent_turn_before_recording_acceptance(tmp_path: Path) -> None:
    async def scenario() -> None:
        event_log = PausingEventLog(tmp_path)
        runner = WaitingRunner()
        dispatcher = build_dispatcher(
            tmp_path,
            event_log=event_log,
            turns=TurnConfig("openai:gpt-5", runner),
        )
        created = await dispatcher.dispatch(
            "thread.create",
            {},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(created, ThreadCreateResult)

        calls = [
            asyncio.create_task(
                dispatcher.dispatch(
                    "thread.turn.start",
                    {"thread_id": created.id, "message": message},
                    {Scope.THREAD_OPERATE},
                )
            )
            for message in ("First", "Second")
        ]
        for _ in range(5):
            await asyncio.sleep(0)
        event_log.release.set()
        results = await asyncio.gather(*calls)
        runner.release.set()

        assert sum(isinstance(result, AcceptedResult) for result in results) == 1
        busy = next(result for result in results if isinstance(result, ErrorEnvelope))
        assert busy.code is ErrorCode.THREAD_BUSY

    asyncio.run(scenario())


def test_failed_model_turn_ends_with_the_error_code(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig("openai:gpt-5", FailingRunner()),
        )
        created = await dispatcher.dispatch(
            "thread.create",
            {},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(created, ThreadCreateResult)

        await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id},
            {Scope.THREAD_READ},
        )
        started = await asyncio.wait_for(anext(subscription), timeout=1)
        failed = await asyncio.wait_for(anext(subscription), timeout=1)
        await subscription.aclose()

        assert isinstance(started, Event)
        assert started.type is EventType.TURN_STARTED
        assert isinstance(failed, Event)
        assert failed.type is EventType.TURN_FAILED
        assert failed.payload == TurnFailed(
            code=ErrorCode.INTERNAL,
            message="The model turn failed unexpectedly.",
        )

    asyncio.run(scenario())


async def _park_turn(
    tmp_path: Path,
) -> tuple[Dispatcher, ThreadCreateResult, AcceptedResult, Event]:
    dispatcher = build_dispatcher(
        tmp_path,
        turns=TurnConfig("openai:gpt-5", ParkingRunner()),
    )
    created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
    assert isinstance(created, ThreadCreateResult)
    accepted = await dispatcher.dispatch(
        "thread.turn.start",
        {"thread_id": created.id, "message": "Hello"},
        {Scope.THREAD_OPERATE},
    )
    assert isinstance(accepted, AcceptedResult)
    subscription = dispatcher.subscribe(
        "thread.subscribe",
        {"thread_id": created.id},
        {Scope.THREAD_READ},
    )
    await asyncio.wait_for(anext(subscription), timeout=1)
    requested = await asyncio.wait_for(anext(subscription), timeout=1)
    await subscription.aclose()
    assert isinstance(requested, Event)
    assert requested.type is EventType.APPROVAL_REQUESTED
    return dispatcher, created, accepted, requested


def test_parked_approval_resumes_after_dispatcher_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, created, accepted, requested = await _park_turn(tmp_path)
        assert requested.payload == ApprovalRequested(
            approval_id=_APPROVAL_ID,
            request="May I continue?",
        )

        restarted = build_dispatcher(
            tmp_path,
            turns=TurnConfig("openai:gpt-5", ParkingRunner()),
        )
        resumed = await restarted.dispatch(
            "thread.approval.respond",
            {
                "thread_id": created.id,
                "approval_id": _APPROVAL_ID,
                "answer": "yes",
            },
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(resumed, AcceptedResult)
        assert resumed.thread_id == created.id
        assert resumed.turn_id == accepted.turn_id

        live = restarted.subscribe(
            "thread.subscribe",
            {"thread_id": created.id, "after_sequence": requested.sequence},
            {Scope.THREAD_READ},
        )
        delta = await asyncio.wait_for(anext(live), timeout=1)
        completed = await asyncio.wait_for(anext(live), timeout=1)
        await live.aclose()

        assert isinstance(delta, Event)
        assert delta.type is EventType.MESSAGE_DELTA
        assert delta.payload == MessageDelta(text="Approved")
        assert isinstance(completed, Event)
        assert completed.type is EventType.TURN_COMPLETED
        assert completed.payload == TurnCompleted(input_tokens=3, output_tokens=1)

    asyncio.run(scenario())


def test_parked_approval_resumes_on_the_same_dispatcher(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher, created, accepted, requested = await _park_turn(tmp_path)
        resumed = await dispatcher.dispatch(
            "thread.approval.respond",
            {
                "thread_id": created.id,
                "approval_id": _APPROVAL_ID,
                "answer": "yes",
            },
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(resumed, AcceptedResult)
        assert resumed.turn_id == accepted.turn_id

        live = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id, "after_sequence": requested.sequence},
            {Scope.THREAD_READ},
        )
        delta = await asyncio.wait_for(anext(live), timeout=1)
        completed = await asyncio.wait_for(anext(live), timeout=1)
        await live.aclose()

        assert isinstance(delta, Event)
        assert delta.type is EventType.MESSAGE_DELTA
        assert isinstance(completed, Event)
        assert completed.type is EventType.TURN_COMPLETED

        stale = await dispatcher.dispatch(
            "thread.approval.respond",
            {
                "thread_id": created.id,
                "approval_id": _APPROVAL_ID,
                "answer": "yes",
            },
            {Scope.THREAD_OPERATE},
        )
        assert stale == ErrorEnvelope(
            code=ErrorCode.NO_ACTIVE_TURN,
            message=f'Thread "{created.id}" has no active turn.',
            retryable=False,
        )

    asyncio.run(scenario())


def test_unknown_approval_id_returns_not_found(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher, created, _, requested = await _park_turn(tmp_path)
        assert requested.type is EventType.APPROVAL_REQUESTED

        missing = await dispatcher.dispatch(
            "thread.approval.respond",
            {
                "thread_id": created.id,
                "approval_id": "22222222-2222-2222-2222-222222222222",
                "answer": "yes",
            },
            {Scope.THREAD_OPERATE},
        )
        assert missing == ErrorEnvelope(
            code=ErrorCode.NOT_FOUND,
            message='Approval "22222222-2222-2222-2222-222222222222" was not found.',
            retryable=False,
        )

    asyncio.run(scenario())


def test_start_rejects_a_new_turn_while_parked_after_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, created, _, requested = await _park_turn(tmp_path)
        assert requested.type is EventType.APPROVAL_REQUESTED

        restarted = build_dispatcher(
            tmp_path,
            turns=TurnConfig("openai:gpt-5", ParkingRunner()),
        )
        second = await restarted.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Second"},
            {Scope.THREAD_OPERATE},
        )
        assert second == ErrorEnvelope(
            code=ErrorCode.THREAD_BUSY,
            message=f'Thread "{created.id}" already has a running turn.',
            retryable=True,
        )

    asyncio.run(scenario())


def test_interrupt_ends_a_parked_turn_after_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, created, accepted, requested = await _park_turn(tmp_path)
        restarted = build_dispatcher(
            tmp_path,
            turns=TurnConfig("openai:gpt-5", ParkingRunner()),
        )

        interrupted = await restarted.dispatch(
            "thread.turn.interrupt",
            {"thread_id": created.id},
            {Scope.THREAD_OPERATE},
        )

        assert interrupted == AcceptedResult(
            thread_id=created.id,
            turn_id=accepted.turn_id,
            sequence=requested.sequence + 1,
        )
        subscription = restarted.subscribe(
            "thread.subscribe",
            {"thread_id": created.id, "after_sequence": requested.sequence},
            {Scope.THREAD_READ},
        )
        event = await asyncio.wait_for(anext(subscription), timeout=1)
        await subscription.aclose()
        assert isinstance(event, Event)
        assert event.payload == TurnInterrupted()

    asyncio.run(scenario())


def test_unknown_approval_id_with_no_active_turn_returns_not_found(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig("openai:gpt-5", ParkingRunner()),
        )
        created = await dispatcher.dispatch(
            "thread.create",
            {},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(created, ThreadCreateResult)

        missing = await dispatcher.dispatch(
            "thread.approval.respond",
            {
                "thread_id": created.id,
                "approval_id": "11111111-1111-1111-1111-111111111111",
                "answer": "yes",
            },
            {Scope.THREAD_OPERATE},
        )
        assert missing == ErrorEnvelope(
            code=ErrorCode.NOT_FOUND,
            message='Approval "11111111-1111-1111-1111-111111111111" was not found.',
            retryable=False,
        )

    asyncio.run(scenario())
