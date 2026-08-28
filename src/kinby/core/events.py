"""Persist the event stream as the canonical transcript store."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from kinby.contracts import Event, Payload

_EVENTS_NAME = "events.jsonl"


class EventLog:
    def __init__(self, state_dir: Path) -> None:
        self._path = state_dir / _EVENTS_NAME
        self._lock = asyncio.Lock()
        self._subscribers: dict[UUID, set[asyncio.Queue[Event]]] = {}

    async def append(
        self,
        thread_id: UUID,
        turn_id: UUID,
        payload: Payload,
    ) -> Event:
        async with self._lock:
            event = Event(
                sequence=len(self._stored_events(thread_id)) + 1,
                thread_id=thread_id,
                turn_id=turn_id,
                payload=payload,
                timestamp=datetime.now(UTC),
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
    ) -> AsyncGenerator[Event]:
        subscriber: asyncio.Queue[Event] = asyncio.Queue()
        async with self._lock:
            replay = [
                event for event in self._stored_events(thread_id) if event.sequence > after_sequence
            ]
            self._subscribers.setdefault(thread_id, set()).add(subscriber)
        try:
            for event in replay:
                yield event
            while True:
                event = await subscriber.get()
                if event.sequence > after_sequence:
                    yield event
        finally:
            subscribers = self._subscribers[thread_id]
            subscribers.remove(subscriber)
            if not subscribers:
                del self._subscribers[thread_id]

    def _stored_events(self, thread_id: UUID) -> list[Event]:
        if not self._path.exists():
            return []
        with self._path.open(encoding="utf-8") as records:
            return [
                event
                for line in records
                if (event := Event.model_validate_json(line)).thread_id == thread_id
            ]
