import asyncio
from pathlib import Path
from uuid import uuid4

from kinby.contracts import EventType
from kinby.core.events import EventLog


def test_finished_thread_replays_every_stored_event_in_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        thread_id = uuid4()
        turn_id = uuid4()
        event_log = EventLog(tmp_path)
        stored = [
            await event_log.append(
                thread_id,
                turn_id,
                EventType.TURN_STARTED,
                {"message": "Book the trip"},
            ),
            await event_log.append(
                thread_id,
                turn_id,
                EventType.MESSAGE_DELTA,
                {"text": "Done"},
            ),
            await event_log.append(
                thread_id,
                turn_id,
                EventType.TURN_COMPLETED,
                {},
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
            EventType.TURN_STARTED,
            {},
        )
        other_thread = await EventLog(tmp_path).append(
            other_thread_id,
            turn_id,
            EventType.TURN_STARTED,
            {},
        )

        second = await EventLog(tmp_path).append(
            thread_id,
            turn_id,
            EventType.TURN_COMPLETED,
            {},
        )

        assert (first.sequence, second.sequence) == (1, 2)
        assert other_thread.sequence == 1

    asyncio.run(scenario())


def test_subscriber_receives_replay_gap_then_live_events_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        thread_id = uuid4()
        turn_id = uuid4()
        event_log = EventLog(tmp_path)
        await event_log.append(thread_id, turn_id, EventType.TURN_STARTED, {})
        replay_gap = await event_log.append(
            thread_id,
            turn_id,
            EventType.MESSAGE_DELTA,
            {"text": "halfway"},
        )
        subscription = event_log.subscribe(thread_id, after_sequence=1)

        received_gap = await anext(subscription)
        during_handoff = await event_log.append(
            thread_id,
            turn_id,
            EventType.MESSAGE_DELTA,
            {"text": "done"},
        )
        received_during_handoff = await asyncio.wait_for(anext(subscription), timeout=1)
        waiting_for_live = asyncio.ensure_future(anext(subscription))
        await asyncio.sleep(0)
        live = await event_log.append(
            thread_id,
            turn_id,
            EventType.TURN_COMPLETED,
            {},
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

        await event_log.append(thread_id, turn_id, EventType.TURN_STARTED, {})
        await event_log.append(thread_id, turn_id, EventType.MESSAGE_DELTA, {})

        assert waiting.done() is False

        third = await event_log.append(thread_id, turn_id, EventType.TURN_COMPLETED, {})
        received = await asyncio.wait_for(waiting, timeout=1)
        await subscription.aclose()

        assert received == third

    asyncio.run(scenario())
