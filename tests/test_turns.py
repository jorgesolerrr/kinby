import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import cast
from uuid import UUID

from pydantic import JsonValue

from kinby.contracts import (
    AcceptedResult,
    ErrorCode,
    ErrorEnvelope,
    Event,
    EventType,
    Scope,
    ThreadCreateResult,
)
from kinby.core.dispatcher import TurnConfig, build_dispatcher
from kinby.core.events import EventLog
from kinby.core.turns import Emit, TurnOutcome, TurnRequest


class ScriptedRunner:
    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
        assert turn.message == "Hello"
        await emit(EventType.MESSAGE_DELTA, {"text": "Hi"})
        await emit(EventType.MESSAGE_DELTA, {"text": " there"})
        return TurnOutcome(input_tokens=4, output_tokens=2)


class WaitingRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
        self.started.set()
        await self.release.wait()
        return TurnOutcome()


class FailingRunner:
    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
        raise RuntimeError("provider unavailable")


class PausingEventLog(EventLog):
    def __init__(self, state_dir: Path) -> None:
        super().__init__(state_dir)
        self.turns_started = 0
        self.release = asyncio.Event()

    async def append(
        self,
        thread_id: UUID,
        turn_id: UUID,
        event_type: EventType,
        payload: Mapping[str, JsonValue],
    ) -> Event:
        if event_type is EventType.TURN_STARTED:
            self.turns_started += 1
            await self.release.wait()
        return await super().append(thread_id, turn_id, event_type, payload)


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
        assert typed_events[0].payload == {"message": "Hello", "model": "openai:gpt-5"}
        assert typed_events[1].payload == {"text": "Hi"}
        assert typed_events[2].payload == {"text": " there"}
        assert typed_events[3].payload == {"input_tokens": 4, "output_tokens": 2}

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
        assert failed.payload == {
            "code": ErrorCode.INTERNAL.value,
            "message": "The model turn failed unexpectedly.",
        }

    asyncio.run(scenario())
