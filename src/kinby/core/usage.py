"""Calculate raw token totals from recorded events."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from kinby.contracts import (
    Event,
    ThreadUsage,
    TurnClosingKind,
    TurnUsage,
    UsageGetResult,
)
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
) -> UsageGetResult:
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
            recap_input_tokens=record.recap_input_tokens,
            recap_output_tokens=record.recap_output_tokens,
        )
        turns_by_thread.setdefault(record.thread_id, []).append(turn)

    return UsageGetResult(
        threads=[
            ThreadUsage(
                thread_id=thread_id,
                input_tokens=sum(turn.input_tokens for turn in turns),
                output_tokens=sum(turn.output_tokens for turn in turns),
                turns=turns,
            )
            for thread_id, turns in turns_by_thread.items()
        ]
    )
