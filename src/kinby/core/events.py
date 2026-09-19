"""Persist the event stream as the canonical transcript store."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from kinby.contracts import Event, Payload, Stream
from kinby.core.clock import utc_now

_EVENTS_NAME = "events.jsonl"


class EventLog:
    def __init__(
        self,
        state_dir: Path,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._path = state_dir / _EVENTS_NAME
        self._clock = clock
        self._lock = asyncio.Lock()
        self._subscribers: dict[UUID, set[asyncio.Queue[Event]]] = {}

    async def append(
        self,
        thread_id: UUID,
        turn_id: UUID,
        payload: Payload,
    ) -> Event:
        async with self._lock:
            timestamp = self._clock()
            if timestamp.utcoffset() is None:
                raise ValueError("Event clock must return a timezone-aware timestamp")
            event = Event(
                sequence=len(self.stored(thread_id)) + 1,
                thread_id=thread_id,
                turn_id=turn_id,
                payload=payload,
                timestamp=timestamp.astimezone(UTC),
            )
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as records:
                records.write(f"{event.model_dump_json()}\n")
            for subscriber in self._subscribers.get(thread_id, set()):
                subscriber.put_nowait(event)
        return event

    async def subscribe(
        self,
        thread_id: UUID,
        after_sequence: int = 0,
    ) -> Stream[Event]:
        """Take the head sequence and the replay under the lock, so neither misses an append."""
        subscriber: asyncio.Queue[Event] = asyncio.Queue()
        async with self._lock:
            stored = self.stored(thread_id)
            self._subscribers.setdefault(thread_id, set()).add(subscriber)
        replay = [event for event in stored if event.sequence > after_sequence]
        head_sequence = stored[-1].sequence if stored else 0
        return Stream(
            head_sequence,
            self._deliver(thread_id, subscriber, replay, after_sequence),
            _close=lambda: self._drop(thread_id, subscriber),
        )

    async def _deliver(
        self,
        thread_id: UUID,
        subscriber: asyncio.Queue[Event],
        replay: list[Event],
        after_sequence: int,
    ) -> AsyncGenerator[Event]:
        try:
            for event in replay:
                yield event
            while True:
                event = await subscriber.get()
                if event.sequence > after_sequence:
                    yield event
        finally:
            self._drop(thread_id, subscriber)

    def _drop(self, thread_id: UUID, subscriber: asyncio.Queue[Event]) -> None:
        subscribers = self._subscribers.get(thread_id)
        if subscribers is None:
            return
        subscribers.discard(subscriber)
        if not subscribers:
            del self._subscribers[thread_id]

    def stored(self, thread_id: UUID) -> list[Event]:
        return [event for event in self.all_events() if event.thread_id == thread_id]

    def all_events(self) -> Iterator[Event]:
        if not self._path.exists():
            return
        with self._path.open(encoding="utf-8") as records:
            for line in records:
                yield Event.model_validate_json(line)
