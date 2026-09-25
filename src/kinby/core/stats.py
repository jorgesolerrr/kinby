"""Aggregate per-turn metrics into UTC reporting buckets."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from kinby.contracts import (
    DelegatedRun,
    DenyCounts,
    MemoryCallCounts,
    NavigationMeans,
    ReportedRun,
    StatsBucket,
    StatsBucketSize,
    StatsSummary,
    SubscriptionUse,
    ToolTime,
    TurnClosingKind,
    TurnMetrics,
    TurnVerdict,
    UsageSource,
)

_SUBSCRIPTION_SOURCES = tuple(source for source in UsageSource if source is not UsageSource.API)


@dataclass
class _BucketTotals:
    completed: int = 0
    failed: int = 0
    interrupted: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    recap_input_tokens: int = 0
    recap_output_tokens: int = 0
    cost: float | None = None
    tool_calls: Counter[str] = field(default_factory=Counter)
    memory_calls: Counter[str] = field(default_factory=Counter)
    turns_without_memory: int = 0
    approvals_requested: int = 0
    denies: Counter[str] = field(default_factory=Counter)
    tool_duration: Counter[str] = field(default_factory=Counter)
    duration_seconds: float = 0
    durations: int = 0
    good_ratings: int = 0
    bad_ratings: int = 0
    navigation_turns: int = 0
    read_calls: int = 0
    navigation_duration_ms: int = 0
    tokens_before_first_write: int = 0
    navigation_repeat_opens: int = 0
    delegated_runs: list[DelegatedRun] = field(default_factory=list)

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
        self.cache_read_tokens += record.cache_read_tokens
        self.cache_creation_tokens += record.cache_creation_tokens
        self.recap_input_tokens += record.recap_input_tokens
        self.recap_output_tokens += record.recap_output_tokens
        if record.cost is not None:
            self.cost = record.cost if self.cost is None else self.cost + record.cost
        self.tool_calls.update(record.tool_calls)
        self.memory_calls.update(record.memory_calls.model_dump())
        self.turns_without_memory += not record.memory_consulted
        self.approvals_requested += record.approvals_requested
        self.denies.update(record.denies.model_dump())
        self.tool_duration.update(record.tool_duration.model_dump())
        if record.duration_seconds is not None:
            self.duration_seconds += record.duration_seconds
            self.durations += 1
        if record.rating is not None and record.rating.verdict is TurnVerdict.GOOD:
            self.good_ratings += 1
        elif record.rating is not None and record.rating.verdict is TurnVerdict.BAD:
            self.bad_ratings += 1
        if record.navigation.write_calls > 0:
            self.navigation_turns += 1
            self.read_calls += record.navigation.read_calls
            self.navigation_duration_ms += record.navigation.duration_ms
            self.tokens_before_first_write += record.navigation.tokens_before_first_write
            self.navigation_repeat_opens += record.navigation.repeat_opens


def stats_buckets(
    records: Iterable[TurnMetrics],
    runs: Iterable[ReportedRun],
    by: StatsBucketSize,
) -> list[StatsBucket]:
    """Group turn records by their UTC closing date and delegated runs by their own."""
    totals_by_start: dict[date, _BucketTotals] = {}
    for record in records:
        start = _bucket_start(record.closed_at, by)
        totals_by_start.setdefault(start, _BucketTotals()).add(record)
    for reported in runs:
        start = _bucket_start(reported.timestamp, by)
        totals_by_start.setdefault(start, _BucketTotals()).delegated_runs.append(reported.run)

    return [_stats_bucket(start, totals_by_start[start]) for start in sorted(totals_by_start)]


def stats_summary(records: Iterable[TurnMetrics], runs: Iterable[ReportedRun]) -> StatsSummary:
    """Aggregate records and runs without a date boundary for a report total."""
    totals = _BucketTotals()
    for record in records:
        totals.add(record)
    totals.delegated_runs.extend(reported.run for reported in runs)
    return _stats_summary(totals)


def _bucket_start(timestamp: datetime, by: StatsBucketSize) -> date:
    start = timestamp.astimezone(UTC).date()
    if by is StatsBucketSize.WEEK:
        start -= timedelta(days=start.weekday())
    return start


def _stats_bucket(start: date, totals: _BucketTotals) -> StatsBucket:
    summary = _stats_summary(totals)
    return StatsBucket(start=start, **summary.model_dump())


def _navigation_means(totals: _BucketTotals) -> NavigationMeans:
    if totals.navigation_turns == 0:
        return NavigationMeans()
    counted = totals.navigation_turns
    return NavigationMeans(
        turns=counted,
        read_calls=totals.read_calls / counted,
        duration_ms=totals.navigation_duration_ms / counted,
        tokens_before_first_write=totals.tokens_before_first_write / counted,
        repeat_opens=totals.navigation_repeat_opens / counted,
    )


def _subscription_use(source: UsageSource, runs: Iterable[DelegatedRun]) -> SubscriptionUse:
    paid = [run for run in runs if run.usage_source is source]
    return SubscriptionUse(
        usage_source=source,
        runs=len(paid),
        input_tokens=sum(run.input_tokens for run in paid),
        output_tokens=sum(run.output_tokens for run in paid),
        cache_read_tokens=sum(run.cache_read_tokens for run in paid),
        cache_creation_tokens=sum(run.cache_creation_tokens for run in paid),
        duration_ms=sum(run.duration_ms for run in paid),
    )


def _stats_summary(totals: _BucketTotals) -> StatsSummary:
    return StatsSummary(
        completed=totals.completed,
        failed=totals.failed,
        interrupted=totals.interrupted,
        input_tokens=totals.input_tokens,
        output_tokens=totals.output_tokens,
        cache_read_tokens=totals.cache_read_tokens,
        cache_creation_tokens=totals.cache_creation_tokens,
        recap_input_tokens=totals.recap_input_tokens,
        recap_output_tokens=totals.recap_output_tokens,
        cost=totals.cost,
        tool_calls=dict(totals.tool_calls),
        memory_calls=MemoryCallCounts.model_validate(totals.memory_calls),
        turns_without_memory=totals.turns_without_memory,
        approvals_requested=totals.approvals_requested,
        denies=DenyCounts.model_validate(totals.denies),
        tool_duration=ToolTime.model_validate(totals.tool_duration),
        mean_duration_seconds=(
            totals.duration_seconds / totals.durations if totals.durations else None
        ),
        good_ratings=totals.good_ratings,
        bad_ratings=totals.bad_ratings,
        navigation=_navigation_means(totals),
        subscriptions=[
            _subscription_use(source, totals.delegated_runs) for source in _SUBSCRIPTION_SOURCES
        ],
    )
