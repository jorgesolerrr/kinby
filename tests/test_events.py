import asyncio
import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from kinby.contracts import (
    Delivery,
    DeliveryId,
    Event,
    EventType,
    MessageDelta,
    PermissionMode,
    RoutineName,
    RoutineOrigin,
    RoutineRunCommand,
    RoutineTrigger,
    SignalReceived,
    TakeoverRequested,
    ToolCall,
    ToolResult,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    Warning,
)
from kinby.core.events import EventLog
from tests.helpers import write_events

STARTED = TurnStarted(
    message="Hello",
    model="openai:gpt-5",
    permission_mode=PermissionMode.ASK,
)


def test_event_clock_rejects_naive_timestamps_before_persisting(tmp_path: Path) -> None:
    async def scenario() -> None:
        thread_id = uuid4()
        event_log = EventLog(tmp_path, clock=lambda: datetime(2026, 9, 11))

        with pytest.raises(ValueError, match="timezone-aware"):
            await event_log.append(thread_id, uuid4(), STARTED)

        assert event_log.stored(thread_id) == []
        assert list(event_log.all_events()) == []

    asyncio.run(scenario())


def test_event_clock_normalizes_aware_timestamps_to_utc(tmp_path: Path) -> None:
    async def scenario() -> None:
        thread_id = uuid4()
        local_time = datetime(2026, 9, 10, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
        event_log = EventLog(tmp_path, clock=lambda: local_time)

        event = await event_log.append(thread_id, uuid4(), STARTED)

        expected = datetime(2026, 9, 11, 4, 30, tzinfo=UTC)
        assert event.timestamp == expected
        assert event.timestamp.tzinfo is UTC
        stored = EventLog(tmp_path).stored(thread_id)
        assert stored == [event]
        assert stored[0].timestamp.isoformat() == "2026-09-11T04:30:00+00:00"

    asyncio.run(scenario())


def test_tool_and_warning_events_round_trip_through_the_event_log(tmp_path: Path) -> None:
    async def scenario() -> None:
        thread_id = uuid4()
        turn_id = uuid4()
        event_log = EventLog(tmp_path)
        payloads = [
            ToolCall(
                call_id="call-1",
                name="weather",
                arguments={"city": "Quito", "days": 3},
            ),
            ToolResult(
                call_id="call-1",
                name="weather",
                output="18 C",
                error=False,
            ),
            Warning(sources=("tools/weather.py",), message="Using cached tool set."),
        ]

        stored = [await event_log.append(thread_id, turn_id, payload) for payload in payloads]

        assert EventLog(tmp_path).stored(thread_id) == stored
        assert [event.payload for event in stored] == payloads

    asyncio.run(scenario())


def test_finished_thread_replays_every_stored_event_in_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        thread_id = uuid4()
        turn_id = uuid4()
        event_log = EventLog(tmp_path)
        stored = [
            await event_log.append(
                thread_id,
                turn_id,
                TurnStarted(
                    message="Book the trip",
                    model="openai:gpt-5",
                    permission_mode=PermissionMode.ASK,
                ),
            ),
            await event_log.append(
                thread_id,
                turn_id,
                MessageDelta(text="Done"),
            ),
            await event_log.append(
                thread_id,
                turn_id,
                TurnCompleted(input_tokens=0, output_tokens=0),
            ),
        ]

        stream = await EventLog(tmp_path).subscribe(thread_id, after_sequence=0)
        replayed = [await anext(stream.items) for _ in stored]
        await stream.items.aclose()

        assert stream.head_sequence == 3
        assert replayed == stored
        assert [event.sequence for event in replayed] == [1, 2, 3]

    asyncio.run(scenario())


def test_sequence_stays_gap_free_after_event_log_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        thread_id = uuid4()
        other_thread_id = uuid4()
        turn_id = uuid4()
        first = await EventLog(tmp_path).append(
            thread_id,
            turn_id,
            STARTED,
        )
        other_thread = await EventLog(tmp_path).append(
            other_thread_id,
            turn_id,
            STARTED,
        )

        second = await EventLog(tmp_path).append(
            thread_id,
            turn_id,
            TurnCompleted(input_tokens=0, output_tokens=0),
        )

        assert (first.sequence, second.sequence) == (1, 2)
        assert other_thread.sequence == 1

    asyncio.run(scenario())


def test_interleaved_threads_each_keep_a_gap_free_sequence(tmp_path: Path) -> None:
    async def scenario() -> None:
        first_thread = uuid4()
        second_thread = uuid4()
        turn_id = uuid4()
        event_log = EventLog(tmp_path)

        for _ in range(3):
            await event_log.append(first_thread, turn_id, MessageDelta(text="first"))
            await event_log.append(second_thread, turn_id, MessageDelta(text="second"))
        await event_log.append(first_thread, turn_id, MessageDelta(text="first"))

        assert [event.sequence for event in event_log.stored(first_thread)] == [1, 2, 3, 4]
        assert [event.sequence for event in event_log.stored(second_thread)] == [1, 2, 3]

    asyncio.run(scenario())


def test_all_events_reads_every_thread_in_the_order_appended(tmp_path: Path) -> None:
    async def scenario() -> None:
        first_thread = uuid4()
        second_thread = uuid4()
        turn_id = uuid4()
        event_log = EventLog(tmp_path)

        appended = [
            await event_log.append(second_thread, turn_id, STARTED),
            await event_log.append(first_thread, turn_id, STARTED),
            await event_log.append(second_thread, turn_id, MessageDelta(text="second")),
        ]

        assert list(EventLog(tmp_path).all_events()) == appended

    asyncio.run(scenario())


def test_a_read_under_way_sees_no_event_appended_after_it_began(tmp_path: Path) -> None:
    async def scenario() -> None:
        thread_id = uuid4()
        turn_id = uuid4()
        event_log = EventLog(tmp_path)
        first = await event_log.append(thread_id, turn_id, STARTED)
        second = await event_log.append(thread_id, turn_id, MessageDelta(text="kept"))
        reading = event_log.all_events()
        read = [next(reading)]

        await event_log.append(thread_id, turn_id, MessageDelta(text="later"))
        read.extend(reading)

        assert read == [first, second]

    asyncio.run(scenario())


def test_a_thread_read_returns_that_thread_after_its_cursor(tmp_path: Path) -> None:
    async def scenario() -> None:
        thread_id = uuid4()
        turn_id = uuid4()
        event_log = EventLog(tmp_path)
        first = await event_log.append(thread_id, turn_id, STARTED)
        await event_log.append(uuid4(), turn_id, STARTED)
        second = await event_log.append(thread_id, turn_id, MessageDelta(text="kept"))

        assert event_log.thread_events(thread_id) == [first, second]
        assert event_log.thread_events(thread_id, after_sequence=1) == [second]
        assert event_log.thread_events(thread_id, after_sequence=2) == []

    asyncio.run(scenario())


def test_a_turn_read_returns_only_that_turns_events(tmp_path: Path) -> None:
    async def scenario() -> None:
        thread_id = uuid4()
        turn_id = uuid4()
        event_log = EventLog(tmp_path)
        started = await event_log.append(thread_id, turn_id, STARTED)
        await event_log.append(thread_id, uuid4(), STARTED)
        completed = await event_log.append(
            thread_id, turn_id, TurnCompleted(input_tokens=0, output_tokens=0)
        )

        assert event_log.turn_events(turn_id) == [started, completed]

    asyncio.run(scenario())


def test_a_type_read_returns_those_types_in_the_order_appended(tmp_path: Path) -> None:
    async def scenario() -> None:
        times = iter(datetime(2026, 9, 6, hour, tzinfo=UTC) for hour in range(9, 14))
        event_log = EventLog(tmp_path, clock=lambda: next(times))
        first_thread = uuid4()
        second_thread = uuid4()
        turn_id = uuid4()
        nine = await event_log.append(first_thread, turn_id, STARTED)
        await event_log.append(first_thread, turn_id, MessageDelta(text="skipped"))
        eleven = await event_log.append(second_thread, turn_id, STARTED)
        twelve = await event_log.append(
            first_thread, turn_id, TurnCompleted(input_tokens=0, output_tokens=0)
        )
        thirteen = await event_log.append(second_thread, turn_id, STARTED)
        types = {EventType.TURN_STARTED, EventType.TURN_COMPLETED}

        assert event_log.events_of(types) == [nine, eleven, twelve, thirteen]
        assert event_log.events_of(
            types,
            since=datetime(2026, 9, 6, 11, tzinfo=UTC),
            until=datetime(2026, 9, 6, 13, tzinfo=UTC),
        ) == [eleven, twelve]

    asyncio.run(scenario())


def test_an_open_turn_read_returns_each_threads_latest_of_those_types_while_its_turn_is_open(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        event_log = EventLog(tmp_path)
        closed = TurnCompleted(input_tokens=0, output_tokens=0)

        def requested() -> TakeoverRequested:
            return TakeoverRequested(takeover_id=uuid4(), reason="A sign-in")

        answered, superseded, latest_closed = uuid4(), uuid4(), uuid4()
        answered_turn = uuid4()
        await event_log.append(answered, answered_turn, requested())
        await event_log.append(answered, answered_turn, closed)
        await event_log.append(superseded, uuid4(), requested())
        waiting = await event_log.append(superseded, uuid4(), requested())
        await event_log.append(superseded, uuid4(), MessageDelta(text="another turn"))
        await event_log.append(latest_closed, uuid4(), requested())
        latest_turn = uuid4()
        await event_log.append(latest_closed, latest_turn, requested())
        await event_log.append(latest_closed, latest_turn, closed)
        parked = await event_log.append(uuid4(), uuid4(), requested())

        assert event_log.latest_in_open_turns({EventType.TAKEOVER_REQUESTED}) == [waiting, parked]

    asyncio.run(scenario())


def test_the_last_activity_read_gives_when_each_threads_latest_event_was_stored(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        times = iter(datetime(2026, 9, 6, hour, tzinfo=UTC) for hour in range(9, 12))
        event_log = EventLog(tmp_path, clock=lambda: next(times))
        first_thread, second_thread = uuid4(), uuid4()
        await event_log.append(first_thread, uuid4(), STARTED)
        await event_log.append(second_thread, uuid4(), STARTED)
        await event_log.append(first_thread, uuid4(), MessageDelta(text="latest"))

        with event_log.reader() as reader:
            assert reader.last_activity() == {
                first_thread: datetime(2026, 9, 6, 11, tzinfo=UTC),
                second_thread: datetime(2026, 9, 6, 10, tzinfo=UTC),
            }

    asyncio.run(scenario())


def test_a_delivery_is_found_by_its_routine_and_provider_id(tmp_path: Path) -> None:
    async def scenario() -> None:
        event_log = EventLog(tmp_path)

        def received(routine: str, delivery_id: str) -> SignalReceived:
            return SignalReceived(
                origin=RoutineOrigin(
                    name=RoutineName(routine),
                    trigger=RoutineTrigger.SIGNAL,
                    delivery_id=DeliveryId(delivery_id),
                ),
                delivery=Delivery(
                    headers={},
                    content_type="application/json",
                    body="{}",
                    delivery_id=DeliveryId(delivery_id),
                    received_at=datetime(2026, 9, 6, 9, tzinfo=UTC),
                ),
            )

        await event_log.append(uuid4(), uuid4(), received("triage", "delivery-1"))
        issues = await event_log.append(uuid4(), uuid4(), received("issues", "delivery-1"))

        assert event_log.delivery(RoutineName("issues"), DeliveryId("delivery-1")) == issues
        assert event_log.delivery(RoutineName("issues"), DeliveryId("delivery-2")) is None
        assert event_log.delivery(RoutineName("news"), DeliveryId("delivery-1")) is None

    asyncio.run(scenario())


def test_a_reader_in_a_worker_thread_iterates_what_was_stored_when_it_began(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        thread_id = uuid4()
        turn_id = uuid4()
        event_log = EventLog(tmp_path)
        first = await event_log.append(thread_id, turn_id, STARTED)
        second = await event_log.append(thread_id, turn_id, MessageDelta(text="kept"))
        began = threading.Event()
        appended = threading.Event()

        def read() -> list[Event]:
            with event_log.reader() as reader:
                reading = reader.thread_events(thread_id)
                read = [next(reading)]
                began.set()
                appended.wait()
                return [*read, *reading]

        reading = asyncio.create_task(asyncio.to_thread(read))
        await asyncio.to_thread(began.wait)
        await event_log.append(thread_id, turn_id, MessageDelta(text="later"))
        appended.set()

        assert await reading == [first, second]

    asyncio.run(scenario())


def test_every_read_of_one_reader_sees_what_was_stored_when_its_first_began(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        thread_id = uuid4()
        turn_id = uuid4()
        event_log = EventLog(tmp_path)
        first = await event_log.append(thread_id, turn_id, STARTED)
        began = threading.Event()
        appended = threading.Event()

        def read() -> tuple[list[Event], list[Event]]:
            with event_log.reader() as reader:
                before = [*reader.thread_events(thread_id)]
                began.set()
                appended.wait()
                return before, [*reader.turn_events(turn_id)]

        reading = asyncio.create_task(asyncio.to_thread(read))
        await asyncio.to_thread(began.wait)
        await event_log.append(thread_id, turn_id, MessageDelta(text="later"))
        appended.set()

        assert await reading == ([first], [first])

    asyncio.run(scenario())


def test_subscribing_while_threads_append_replays_its_thread_then_goes_live_once(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        thread_id = uuid4()
        other_thread = uuid4()
        turn_id = uuid4()
        event_log = EventLog(tmp_path)
        await event_log.append(thread_id, turn_id, STARTED)
        await event_log.append(other_thread, turn_id, STARTED)

        subscribing, *appended = await asyncio.gather(
            event_log.subscribe(thread_id, after_sequence=1),
            event_log.append(other_thread, turn_id, MessageDelta(text="other")),
            event_log.append(thread_id, turn_id, MessageDelta(text="raced")),
            event_log.append(thread_id, turn_id, MessageDelta(text="after")),
        )
        received = [await asyncio.wait_for(anext(subscribing.items), timeout=1) for _ in range(2)]
        await subscribing.aclose()

        assert received == [event for event in appended if event.thread_id == thread_id]
        assert [event.sequence for event in received] == [2, 3]
        assert subscribing.head_sequence == 1

    asyncio.run(scenario())


def test_subscriber_receives_replay_gap_then_live_events_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        thread_id = uuid4()
        turn_id = uuid4()
        event_log = EventLog(tmp_path)
        await event_log.append(thread_id, turn_id, STARTED)
        replay_gap = await event_log.append(
            thread_id,
            turn_id,
            MessageDelta(text="halfway"),
        )
        stream = await event_log.subscribe(thread_id, after_sequence=1)
        subscription = stream.items

        received_gap = await anext(subscription)
        during_handoff = await event_log.append(
            thread_id,
            turn_id,
            MessageDelta(text="done"),
        )
        received_during_handoff = await asyncio.wait_for(anext(subscription), timeout=1)
        waiting_for_live = asyncio.ensure_future(anext(subscription))
        await asyncio.sleep(0)
        live = await event_log.append(
            thread_id,
            turn_id,
            TurnCompleted(input_tokens=0, output_tokens=0),
        )
        received_live = await asyncio.wait_for(waiting_for_live, timeout=1)
        await subscription.aclose()

        assert stream.head_sequence == 2
        assert [received_gap, received_during_handoff, received_live] == [
            replay_gap,
            during_handoff,
            live,
        ]
        assert [
            received_gap.sequence,
            received_during_handoff.sequence,
            received_live.sequence,
        ] == [2, 3, 4]

    asyncio.run(scenario())


def test_closing_a_subscription_before_its_first_item_drops_the_subscriber(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        thread_id = uuid4()
        event_log = EventLog(tmp_path)
        stream = await event_log.subscribe(thread_id)
        await stream.aclose()
        await event_log.append(thread_id, uuid4(), STARTED)

        assert event_log._subscribers == {}

    asyncio.run(scenario())


def test_subscriber_does_not_receive_live_events_before_its_cursor(tmp_path: Path) -> None:
    async def scenario() -> None:
        thread_id = uuid4()
        turn_id = uuid4()
        event_log = EventLog(tmp_path)
        stream = await event_log.subscribe(thread_id, after_sequence=2)
        subscription = stream.items
        waiting = asyncio.ensure_future(anext(subscription))
        await asyncio.sleep(0)

        await event_log.append(thread_id, turn_id, STARTED)
        await event_log.append(thread_id, turn_id, MessageDelta(text=""))

        assert waiting.done() is False

        third = await event_log.append(
            thread_id,
            turn_id,
            TurnCompleted(input_tokens=0, output_tokens=0),
        )
        received = await asyncio.wait_for(waiting, timeout=1)
        await subscription.aclose()

        assert stream.head_sequence == 0
        assert received == third

    asyncio.run(scenario())


def test_old_events_without_delivery_fields_still_read(tmp_path: Path) -> None:
    thread_id = uuid4()
    turn_id = uuid4()
    write_events(
        tmp_path,
        [
            {
                "sequence": 1,
                "thread_id": str(thread_id),
                "turn_id": str(turn_id),
                "timestamp": "2026-09-06T09:00:00+00:00",
                "payload": {
                    "type": "turn.started",
                    "message": "Hello",
                    "model": "openai:gpt-5",
                    "origin": {
                        "kind": "routine",
                        "name": "news",
                        "trigger": "scheduled",
                    },
                },
            }
        ],
    )
    event = next(EventLog(tmp_path).all_events())
    assert isinstance(event.payload, TurnStarted)
    assert isinstance(event.payload.origin, RoutineOrigin)
    assert event.payload.origin.delivery_id is None
    assert event.payload.origin.trigger is RoutineTrigger.SCHEDULED
    assert event.payload.prompt_version is None


def test_old_tool_events_read_with_unknown_write_and_no_duration(tmp_path: Path) -> None:
    thread_id = uuid4()
    turn_id = uuid4()
    records = [
        {
            "sequence": 1,
            "thread_id": str(thread_id),
            "turn_id": str(turn_id),
            "timestamp": "2026-09-06T09:00:00+00:00",
            "payload": {
                "type": "tool.call",
                "call_id": "weather-1",
                "name": "weather",
                "arguments": {"city": "Quito"},
            },
        },
        {
            "sequence": 2,
            "thread_id": str(thread_id),
            "turn_id": str(turn_id),
            "timestamp": "2026-09-06T09:00:01+00:00",
            "payload": {
                "type": "tool.result",
                "call_id": "weather-1",
                "name": "weather",
                "output": "18 C",
                "error": False,
            },
        },
    ]
    write_events(tmp_path, records)

    call, result = [event.payload for event in EventLog(tmp_path).all_events()]

    assert isinstance(call, ToolCall)
    assert call.write is None
    assert isinstance(result, ToolResult)
    assert result.duration_ms is None


def test_old_closing_events_read_with_zero_new_token_fields(tmp_path: Path) -> None:
    thread_id = uuid4()
    completed_id = uuid4()
    failed_id = uuid4()
    records = [
        {
            "sequence": 1,
            "thread_id": str(thread_id),
            "turn_id": str(completed_id),
            "timestamp": "2026-09-06T09:00:00+00:00",
            "payload": {
                "type": "turn.completed",
                "input_tokens": 4,
                "output_tokens": 2,
            },
        },
        {
            "sequence": 2,
            "thread_id": str(thread_id),
            "turn_id": str(failed_id),
            "timestamp": "2026-09-06T09:01:00+00:00",
            "payload": {
                "type": "turn.failed",
                "code": "INTERNAL",
                "message": "failed",
            },
        },
    ]
    write_events(tmp_path, records)

    completed, failed = [event.payload for event in EventLog(tmp_path).all_events()]

    assert completed == TurnCompleted(input_tokens=4, output_tokens=2)
    assert failed == TurnFailed(code="INTERNAL", message="failed")


def test_old_boundary_events_read_with_no_workspace_snapshot(tmp_path: Path) -> None:
    thread_id = uuid4()
    turn_id = uuid4()
    records = [
        {
            "sequence": 1,
            "thread_id": str(thread_id),
            "turn_id": str(turn_id),
            "timestamp": "2026-09-06T09:00:00+00:00",
            "payload": {"type": "turn.started", "message": "Hello", "model": "openai:gpt-5"},
        },
        {
            "sequence": 2,
            "thread_id": str(thread_id),
            "turn_id": str(turn_id),
            "timestamp": "2026-09-06T09:00:01+00:00",
            "payload": {"type": "turn.completed", "input_tokens": 4, "output_tokens": 2},
        },
    ]
    write_events(tmp_path, records)

    started, completed = [event.payload for event in EventLog(tmp_path).all_events()]

    assert isinstance(started, TurnStarted)
    assert started.snapshot is None
    assert isinstance(completed, TurnCompleted)
    assert completed.snapshot is None


def test_signal_received_and_manual_payload_shapes() -> None:
    received = datetime(2026, 9, 6, 9, tzinfo=UTC)
    delivery = Delivery(
        headers={"content-type": "application/json"},
        content_type="application/json",
        body='{"action":"opened"}',
        delivery_id=DeliveryId("abc"),
        received_at=received,
    )
    payload = SignalReceived(
        origin=RoutineOrigin(
            name=RoutineName("news"),
            trigger=RoutineTrigger.SIGNAL,
            delivery_id=DeliveryId("abc"),
        ),
        delivery=delivery,
    )
    event = Event.model_validate(
        {
            "sequence": 1,
            "thread_id": str(uuid4()),
            "turn_id": str(uuid4()),
            "timestamp": received.isoformat(),
            "payload": payload.model_dump(mode="json"),
        }
    )
    assert isinstance(event.payload, SignalReceived)
    assert event.payload.delivery.delivery_id == "abc"
    origin = RoutineOrigin(
        name="news",
        trigger=RoutineTrigger.SIGNAL,
        delivery_id=DeliveryId("abc"),
    )
    assert origin.delivery_id == "abc"
    command = RoutineRunCommand.model_validate(
        {
            "name": "news",
            "payload": {"body": delivery.body, "content_type": "application/json"},
        }
    )
    assert command.payload is not None
    assert command.payload.body == delivery.body
    assert command.payload.content_type == "application/json"
    assert RoutineRunCommand(name="news").payload is None
