"""Persist the event stream as the canonical transcript store, in the instance database."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from kinby.contracts import Event, Payload, Stream
from kinby.core.clock import utc_now
from kinby.instance.database import open_database

#: Each event row as the JSON of its event.
_EVENT_JSON = (
    "json_object('sequence', sequence, 'thread_id', thread_id, 'turn_id', turn_id,"
    " 'timestamp', timestamp, 'payload', json(payload))"
)


class EventLog:
    def __init__(
        self,
        state_dir: Path,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._state_dir = state_dir
        self._db = open_database(state_dir)
        # The event loop and the worker threads that read share the connection.
        self._using_db = threading.Lock()
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
            with self._using_db, self._db:
                self._db.execute("BEGIN IMMEDIATE")
                head = self._db.execute(
                    "SELECT sequence FROM heads WHERE thread_id = ?", (str(thread_id),)
                ).fetchone()
                event = Event(
                    sequence=(0 if head is None else head[0]) + 1,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    payload=payload,
                    timestamp=timestamp.astimezone(UTC),
                )
                at = event.timestamp.isoformat(timespec="microseconds")
                self._db.execute(
                    "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(thread_id),
                        event.sequence,
                        str(turn_id),
                        payload.type,
                        at,
                        payload.model_dump_json(),
                    ),
                )
                self._db.execute(
                    "INSERT INTO heads VALUES (?, ?, ?) ON CONFLICT (thread_id)"
                    " DO UPDATE SET sequence = excluded.sequence,"
                    " last_activity_at = excluded.last_activity_at",
                    (str(thread_id), event.sequence, at),
                )
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
        with self._using_db:
            records = self._db.execute(
                f"SELECT {_EVENT_JSON} FROM events WHERE thread_id = ? ORDER BY sequence",
                (str(thread_id),),
            ).fetchall()
        return [Event.model_validate_json(record) for (record,) in records]

    def all_events(self) -> Iterator[Event]:
        """Every event in the order appended, as stored when the read began. The read has a
        connection of its own, so it may be held open across appends."""
        with closing(open_database(self._state_dir)) as db:
            for (record,) in db.execute(f"SELECT {_EVENT_JSON} FROM events ORDER BY rowid"):
                yield Event.model_validate_json(record)
