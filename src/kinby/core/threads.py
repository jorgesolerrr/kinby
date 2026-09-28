"""Keep thread identity available when no session process is running."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict  # noqa: TID251 - the store's file boundary

from kinby.contracts import (
    ApprovalRequested,
    Event,
    ThreadCreateResult,
    ThreadListResult,
    ThreadStatus,
    ThreadSummary,
    TurnFailed,
    TurnStarted,
    is_turn_closing,
)

THREADS_NAME = "threads.jsonl"


class ThreadRecord(BaseModel):
    """What the store keeps of a thread. Its status and last activity come from its events."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    title: str | None
    created_at: datetime


@dataclass(frozen=True)
class PendingApproval:
    event: Event
    request: ApprovalRequested


class ThreadStore:
    def __init__(self, state_dir: Path) -> None:
        self._path = state_dir / THREADS_NAME

    def create(self, title: str | None) -> ThreadCreateResult:
        thread = ThreadRecord(id=uuid4(), title=title, created_at=datetime.now(UTC))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as records:
            records.write(f"{thread.model_dump_json()}\n")
        return ThreadCreateResult(id=thread.id, created_at=thread.created_at)

    def threads(self) -> list[ThreadRecord]:
        if not self._path.exists():
            return []
        with self._path.open(encoding="utf-8") as records:
            return [ThreadRecord.model_validate_json(line) for line in records]

    def exists(self, thread_id: UUID) -> bool:
        return any(thread.id == thread_id for thread in self.threads())


def events_by_thread(
    threads: Iterable[ThreadRecord], events: Iterable[Event]
) -> dict[UUID, list[Event]]:
    """Each thread's events in order, leaving out the events of any thread not given."""
    grouped: dict[UUID, list[Event]] = {thread.id: [] for thread in threads}
    for event in events:
        if event.thread_id in grouped:
            grouped[event.thread_id].append(event)
    return grouped


def thread_list(threads: Sequence[ThreadRecord], events: Iterable[Event]) -> ThreadListResult:
    """Summarize each thread from its events, the most recently active first."""
    grouped = events_by_thread(threads, events)
    summaries = [_summary(thread, grouped[thread.id]) for thread in threads]
    summaries.sort(key=lambda summary: summary.last_activity_at, reverse=True)
    return ThreadListResult(threads=summaries)


def _summary(thread: ThreadRecord, events: Sequence[Event]) -> ThreadSummary:
    return ThreadSummary(
        id=thread.id,
        title=thread.title,
        created_at=thread.created_at,
        status=thread_status(events),
        last_activity_at=events[-1].timestamp if events else thread.created_at,
    )


def thread_status(events: Sequence[Event]) -> ThreadStatus:
    """What the thread's latest turn is doing, read from the thread's events in order."""
    started = next(
        (event for event in reversed(events) if isinstance(event.payload, TurnStarted)), None
    )
    if started is None:
        return ThreadStatus.IDLE
    if pending_approval(events) is not None:
        return ThreadStatus.AWAITING_APPROVAL
    closing = next(
        (
            event.payload
            for event in events
            if event.turn_id == started.turn_id and is_turn_closing(event.payload)
        ),
        None,
    )
    match closing:
        case None:
            return ThreadStatus.RUNNING
        case TurnFailed():
            return ThreadStatus.FAILED
        case _:
            return ThreadStatus.IDLE


def pending_approval(events: Sequence[Event]) -> PendingApproval | None:
    # Answering an approval appends nothing, so the resumed turn's own next event is
    # what ends the park. Events on any other turn leave it standing.
    for index, event in reversed(list(enumerate(events))):
        payload = event.payload
        if not isinstance(payload, ApprovalRequested):
            continue
        if any(later.turn_id == event.turn_id for later in events[index + 1 :]):
            return None
        return PendingApproval(event, payload)
    return None
