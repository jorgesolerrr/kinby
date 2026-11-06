"""Persist the event stream as the canonical transcript store, in the instance database."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import AsyncGenerator, Callable, Collection, Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from kinby.contracts import (
    DeliveryId,
    Event,
    EventType,
    Payload,
    RoutineName,
    SignalReceived,
    Stream,
)
from kinby.core.clock import utc_now
from kinby.instance.database import DELIVERY_ID, open_database

#: Each event row as the JSON of its event.
_EVENT_JSON = (
    "json_object('sequence', sequence, 'thread_id', thread_id, 'turn_id', turn_id,"
    " 'timestamp', timestamp, 'payload', json(payload))"
)
_CLOSING_TYPES = (
    f"('{EventType.TURN_COMPLETED}', '{EventType.TURN_FAILED}', '{EventType.TURN_INTERRUPTED}')"
)


def _stored_at(time: datetime) -> str:
    return time.astimezone(UTC).isoformat(timespec="microseconds")


class EventReader:
    """The bounded reads of the transcript store, each iterating its events in the order
    appended."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def thread_events(self, thread_id: UUID, after_sequence: int = 0) -> Iterator[Event]:
        return self._events("thread_id = ? AND sequence > ?", (str(thread_id), after_sequence))

    def turn_events(self, turn_id: UUID) -> Iterator[Event]:
        return self._events("turn_id = ?", (str(turn_id),))

    def events_of(
        self,
        types: Collection[EventType],
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Iterator[Event]:
        """The events of *types*, stored from *since* and before *until*."""
        where = f"type IN ({', '.join('?' * len(types))})"
        parameters: list[str] = [*types]
        if since is not None:
            where += " AND timestamp >= ?"
            parameters.append(_stored_at(since))
        if until is not None:
            where += " AND timestamp < ?"
            parameters.append(_stored_at(until))
        return self._events(where, parameters)

    def latest_in_open_turns(self, types: Collection[EventType]) -> Iterator[Event]:
        """Each thread's latest event of *types*, when its turn has not closed."""
        latest = (
            f"SELECT max(rowid) FROM events WHERE type IN ({', '.join('?' * len(types))})"
            " GROUP BY thread_id"
        )
        return self._events(
            f"rowid IN ({latest}) AND NOT EXISTS (SELECT 1 FROM events AS closing"
            f" WHERE closing.turn_id = events.turn_id AND closing.type IN {_CLOSING_TYPES})",
            [*types],
        )

    def last_activity(self) -> dict[UUID, datetime]:
        """When each thread's latest event was stored, from its head."""
        return {
            UUID(thread_id): datetime.fromisoformat(at)
            for thread_id, at in self._db.execute("SELECT thread_id, last_activity_at FROM heads")
        }

    def delivery(self, name: RoutineName, delivery_id: DeliveryId) -> Event | None:
        """The arrival of the delivery a provider sent *name* under *delivery_id*."""
        return next(
            self._events(
                f"type = '{EventType.SIGNAL_RECEIVED}' AND {DELIVERY_ID} = ?"
                " AND json_extract(payload, '$.origin.name') = ?",
                (delivery_id, name),
            ),
            None,
        )

    def _events(self, where: str, parameters: Sequence[object]) -> Iterator[Event]:
        query = f"SELECT {_EVENT_JSON} FROM events WHERE {where} ORDER BY rowid"
        for (record,) in self._db.execute(query, parameters):
            yield Event.model_validate_json(record)


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
        return await self._append(thread_id, turn_id, payload)

    async def append_delivery(
        self, thread_id: UUID, turn_id: UUID, arrival: SignalReceived, body: str
    ) -> Event:
        """Record a delivery's arrival, and its body to wait for *turn_id* to start (ADR 0117)."""
        return await self._append(thread_id, turn_id, arrival, body)

    def waiting_body(self, turn_id: UUID) -> str | None:
        """The body of the delivery that waits for *turn_id* to start."""
        with self._using_db:
            row = self._db.execute(
                "SELECT body FROM waiting_deliveries WHERE turn_id = ?", (str(turn_id),)
            ).fetchone()
        return None if row is None else row[0]

    def drop_waiting_body(self, turn_id: UUID) -> None:
        with self._using_db:
            self._db.execute("DELETE FROM waiting_deliveries WHERE turn_id = ?", (str(turn_id),))

    async def _append(
        self,
        thread_id: UUID,
        turn_id: UUID,
        payload: Payload,
        waiting_body: str | None = None,
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
                if waiting_body is not None:
                    self._db.execute(
                        "INSERT INTO waiting_deliveries VALUES (?, ?)",
                        (str(turn_id), waiting_body),
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
            replay = self.thread_events(thread_id, after_sequence)
            with self._using_db:
                head = self._db.execute(
                    "SELECT sequence FROM heads WHERE thread_id = ?", (str(thread_id),)
                ).fetchone()
            self._subscribers.setdefault(thread_id, set()).add(subscriber)
        head_sequence = 0 if head is None else head[0]
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

    def thread_events(self, thread_id: UUID, after_sequence: int = 0) -> list[Event]:
        with self._using_db:
            return [*EventReader(self._db).thread_events(thread_id, after_sequence)]

    def turn_events(self, turn_id: UUID) -> list[Event]:
        with self._using_db:
            return [*EventReader(self._db).turn_events(turn_id)]

    def events_of(
        self,
        types: Collection[EventType],
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Event]:
        with self._using_db:
            return [*EventReader(self._db).events_of(types, since=since, until=until)]

    def latest_in_open_turns(self, types: Collection[EventType]) -> list[Event]:
        with self._using_db:
            return [*EventReader(self._db).latest_in_open_turns(types)]

    def delivery(self, name: RoutineName, delivery_id: DeliveryId) -> Event | None:
        with self._using_db:
            return EventReader(self._db).delivery(name, delivery_id)

    @contextmanager
    def reader(self) -> Iterator[EventReader]:
        """Reads on a connection of their own, for a worker thread to iterate a long range. Every
        read sees the events stored when the first began, while appends go on."""
        with closing(open_database(self._state_dir)) as db:
            # One read transaction holds one snapshot, so a fold's reads agree with each other.
            db.execute("BEGIN")
            yield EventReader(db)
