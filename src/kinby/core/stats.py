"""Aggregate per-turn metrics into UTC reporting buckets."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

from kinby.contracts import (
    MemoryCallCounts,
    StatsBucket,
    StatsBucketSize,
    StatsSummary,
    TurnClosingKind,
    TurnMetrics,
    TurnVerdict,
)
from kinby.core.turn_metrics import closing_day


@dataclass
class _BucketTotals:
    completed: int = 0
    failed: int = 0
    interrupted: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    recap_input_tokens: int = 0
    recap_output_tokens: int = 0
    cost: float | None = None
    tool_calls: Counter[str] = field(default_factory=Counter)
    memory_calls: Counter[str] = field(default_factory=Counter)
    turns_without_memory: int = 0
    approvals_requested: int = 0
    duration_seconds: float = 0
    durations: int = 0
    good_ratings: int = 0
    bad_ratings: int = 0

    def add(self, record: TurnMetrics) -> None:
        match record.closing_kind:
            case TurnClosingKind.COMPLETED:
                self.completed += 1
            case TurnClosingKind.FAILED:
                self.failed += 1
            case TurnClosingKind.INTERRUPTED:
                self.interrupted += 1
        self.input_tokens += record.input_tokens
        self.output_tokens += record.output_tokens
        self.recap_input_tokens += record.recap_input_tokens
        self.recap_output_tokens += record.recap_output_tokens
        if record.cost is not None:
            self.cost = record.cost if self.cost is None else self.cost + record.cost
        self.tool_calls.update(record.tool_calls)
        self.memory_calls.update(record.memory_calls.model_dump())
        self.turns_without_memory += not record.memory_consulted
        self.approvals_requested += record.approvals_requested
        if record.duration_seconds is not None:
            self.duration_seconds += record.duration_seconds
            self.durations += 1
        if record.rating is not None and record.rating.verdict is TurnVerdict.GOOD:
            self.good_ratings += 1
        elif record.rating is not None and record.rating.verdict is TurnVerdict.BAD:
            self.bad_ratings += 1


def stats_buckets(
    records: Iterable[TurnMetrics],
    by: StatsBucketSize,
) -> list[StatsBucket]:
    """Group turn records by their UTC closing date."""
    totals_by_start: dict[date, _BucketTotals] = {}
    for record in records:
        start = closing_day(record)
        if by is StatsBucketSize.WEEK:
            start -= timedelta(days=start.weekday())
        totals_by_start.setdefault(start, _BucketTotals()).add(record)

    return [_stats_bucket(start, totals_by_start[start]) for start in sorted(totals_by_start)]


def stats_summary(records: Iterable[TurnMetrics]) -> StatsSummary:
    """Aggregate records without a date boundary for a report total."""
    totals = _BucketTotals()
    for record in records:
        totals.add(record)
    return _stats_summary(totals)


def _stats_bucket(start: date, totals: _BucketTotals) -> StatsBucket:
    summary = _stats_summary(totals)
    return StatsBucket(start=start, **summary.model_dump())


def _stats_summary(totals: _BucketTotals) -> StatsSummary:
    return StatsSummary(
        completed=totals.completed,
        failed=totals.failed,
        interrupted=totals.interrupted,
        input_tokens=totals.input_tokens,
        output_tokens=totals.output_tokens,
        recap_input_tokens=totals.recap_input_tokens,
        recap_output_tokens=totals.recap_output_tokens,
        cost=totals.cost,
        tool_calls=dict(totals.tool_calls),
        memory_calls=MemoryCallCounts.model_validate(totals.memory_calls),
        turns_without_memory=totals.turns_without_memory,
        approvals_requested=totals.approvals_requested,
        mean_duration_seconds=(
            totals.duration_seconds / totals.durations if totals.durations else None
        ),
        good_ratings=totals.good_ratings,
        bad_ratings=totals.bad_ratings,
    )
