"""What the transcript store's reads cost on a large store. Timing makes a parallel gate flaky, so
these run only when asked for: pytest -m slow -s."""

import asyncio
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from uuid import UUID, uuid4

import pytest

from kinby.contracts import Event, MessageDelta, PermissionMode, TurnCompleted, TurnStarted
from kinby.core.events import EventLog
from tests.helpers import write_events
from tests.test_routines import instance_at, routine_file
from tests.test_scheduler import FakeClock, runtime

pytestmark = pytest.mark.slow

THREADS = 1000
TURNS_PER_THREAD = 10
START = datetime(2026, 9, 6, tzinfo=UTC)


def turn() -> Iterator[TurnStarted | MessageDelta | TurnCompleted]:
    yield TurnStarted(message="Hello", model="openai:gpt-5", permission_mode=PermissionMode.ASK)
    for _ in range(8):
        yield MessageDelta(text="Some words of an answer. ")
    yield TurnCompleted(input_tokens=100, output_tokens=20)


def history(thread_ids: list[UUID]) -> Iterator[dict[str, object]]:
    """Ten turns of ten events in each thread: 100 events a thread."""
    at = START
    for thread_id in thread_ids:
        sequence = 0
        for _ in range(TURNS_PER_THREAD):
            turn_id = uuid4()
            for payload in turn():
                sequence += 1
                at += timedelta(seconds=1)
                event = Event(
                    sequence=sequence,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    payload=payload,
                    timestamp=at,
                )
                record = event.model_dump(mode="json")
                record["timestamp"] = at.isoformat(timespec="microseconds")
                yield record


def test_reading_one_thread_of_a_large_store_costs_that_thread(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News\nschedule: 0 7 * * *")
        state_dir = instance.manifest.state_dir
        thread_ids = [uuid4() for _ in range(THREADS)]
        write_events(state_dir, history(thread_ids))
        event_log = EventLog(state_dir)

        def read_thread() -> float:
            started = time.perf_counter()
            assert len(event_log.thread_events(thread_ids[THREADS // 2])) == 100
            return (time.perf_counter() - started) * 1000

        thread_ms = median(read_thread() for _ in range(5))
        scheduler = runtime(instance, FakeClock(START + timedelta(days=30))).scheduler
        started = time.perf_counter()
        await scheduler.tick()
        tick_ms = (time.perf_counter() - started) * 1000
        print(f"\none thread of 100 events: {thread_ms:.1f} ms, one tick: {tick_ms:.1f} ms")

        assert thread_ms < 10

    asyncio.run(scenario())
