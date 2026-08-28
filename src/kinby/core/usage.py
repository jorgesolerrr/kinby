"""Calculate raw token totals from recorded events."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from kinby.contracts import (
    Event,
    ThreadUsage,
    TurnCompleted,
    TurnUsage,
    UsageGetResult,
)


@dataclass(frozen=True)
class UsageRange:
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
    usage_range: UsageRange,
) -> UsageGetResult:
    turns_by_thread: dict[UUID, list[TurnUsage]] = {}
    for event in events:
        if not isinstance(event.payload, TurnCompleted):
            continue
        if not usage_range.includes(event.timestamp):
            continue
        turns_by_thread.setdefault(event.thread_id, []).append(
            TurnUsage(
                turn_id=event.turn_id,
                input_tokens=event.payload.input_tokens,
                output_tokens=event.payload.output_tokens,
            )
        )

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
