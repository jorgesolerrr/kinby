"""Calculate raw token totals from recorded events."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from kinby.contracts import (
    Event,
    ModelCompleted,
    ThreadUsage,
    TurnClosingKind,
    TurnUsage,
    UsageGetResult,
)
from kinby.core.pricing import SHIPPED_WINDOWS
from kinby.core.turn_metrics import turn_metrics


@dataclass(frozen=True)
class TimeRange:
    since: datetime | None
    until: datetime | None

    def __post_init__(self) -> None:
        for name, boundary in (("since", self.since), ("until", self.until)):
            if boundary is not None and boundary.utcoffset() is None:
                raise ValueError(f"{name} must include a timezone")

    def includes(self, timestamp: datetime) -> bool:
        if self.since is not None and timestamp < self.since:
            return False
        return self.until is None or timestamp <= self.until


def usage_totals(
    events: Iterable[Event],
    time_range: TimeRange,
    windows: Mapping[str, int] = SHIPPED_WINDOWS,
) -> UsageGetResult:
    events = list(events)
    latest_calls = {
        event.thread_id: event.payload
        for event in events
        if isinstance(event.payload, ModelCompleted)
    }
    turns_by_thread: dict[UUID, list[TurnUsage]] = {}
    for record in turn_metrics(events).records:
        if record.closing_kind is not TurnClosingKind.COMPLETED:
            continue
        if not time_range.includes(record.closed_at):
            continue
        turn = TurnUsage(
            turn_id=record.turn_id,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cache_read_tokens=record.cache_read_tokens,
            cache_creation_tokens=record.cache_creation_tokens,
            recap_input_tokens=record.recap_input_tokens,
            recap_output_tokens=record.recap_output_tokens,
            delegated_runs=[
                reported
                for reported in record.delegated_runs
                if time_range.includes(reported.timestamp)
            ],
        )
        turns_by_thread.setdefault(record.thread_id, []).append(turn)

    threads: list[ThreadUsage] = []
    for thread_id, turns in turns_by_thread.items():
        latest = latest_calls.get(thread_id)
        threads.append(
            ThreadUsage(
                thread_id=thread_id,
                input_tokens=sum(turn.input_tokens for turn in turns),
                output_tokens=sum(turn.output_tokens for turn in turns),
                cache_read_tokens=sum(turn.cache_read_tokens for turn in turns),
                cache_creation_tokens=sum(turn.cache_creation_tokens for turn in turns),
                turns=turns,
                context_size=latest.input_tokens if latest is not None else None,
                window=windows.get(latest.model) if latest is not None else None,
            )
        )
    return UsageGetResult(threads=threads)
