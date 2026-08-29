import asyncio
from pathlib import Path
from uuid import uuid4

from kinby.contracts import (
    MessageDelta,
    ToolCall,
    ToolResult,
    TurnCompleted,
    TurnStarted,
    Warning,
)
from kinby.core.events import EventLog

STARTED = TurnStarted(message="Hello", model="openai:gpt-5")


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
                TurnStarted(message="Book the trip", model="openai:gpt-5"),
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

        subscription = EventLog(tmp_path).subscribe(thread_id, after_sequence=0)
        replayed = [await anext(subscription) for _ in stored]
        await subscription.aclose()

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
        subscription = event_log.subscribe(thread_id, after_sequence=1)

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


def test_subscriber_does_not_receive_live_events_before_its_cursor(tmp_path: Path) -> None:
    async def scenario() -> None:
        thread_id = uuid4()
        turn_id = uuid4()
        event_log = EventLog(tmp_path)
        subscription = event_log.subscribe(thread_id, after_sequence=2)
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

        assert received == third

    asyncio.run(scenario())
