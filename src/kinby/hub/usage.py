"""Add up the usage each running instance reports, without keeping any of it (ADR 0063)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from enum import StrEnum
from statistics import median
from uuid import UUID

from kinby.contracts import (
    ApiUse,
    FactoryRun,
    FactoryRunStatus,
    PlanLimit,
    PlanUse,
    RunTokens,
    RunTotals,
    RunUsage,
    RunWeek,
    StatsBucketSize,
    StatsGetResult,
    StatsRunsResult,
    StatsSummary,
    StatsSummaryResult,
    SubscriptionUse,
    UsageSource,
)
from kinby.core.stats import PLAN_WINDOWS, SUBSCRIPTION_SOURCES, bucket_start
from kinby.hub.runs import Rework

_ENDED = frozenset({FactoryRunStatus.DONE, FactoryRunStatus.CANCELLED})


class Uncounted(StrEnum):
    """Why an instance's usage is missing from a summary."""

    SKIPPED = "skipped"
    UNREACHABLE = "unreachable"
    OUTDATED = "outdated"


def summed_usage(answers: Mapping[UUID, StatsGetResult | Uncounted]) -> StatsSummaryResult:
    """Sum every counted instance per usage source. One account per source (ADR 0063)."""
    read = {instance_id: _with_runs(answer) for instance_id, answer in answers.items()}
    counted = {
        instance_id: answer
        for instance_id, answer in read.items()
        if isinstance(answer, StatsGetResult)
    }
    totals = [answer.total for answer in counted.values()]
    return StatsSummaryResult(
        buckets={instance_id: answer.buckets for instance_id, answer in counted.items()},
        api=_summed_api(totals),
        subscriptions=_summed_uses([use for total in totals for use in total.subscriptions]),
        plan_use=_summed_plan_use([use for answer in counted.values() for use in answer.plan_use]),
        limits=_latest_resets(limit for answer in counted.values() for limit in answer.limits),
        runs={
            instance_id: _run_totals(answer.runs or []) for instance_id, answer in counted.items()
        },
        run_total=_run_totals([run for answer in counted.values() for run in answer.runs or []]),
        skipped=_left_out(read, Uncounted.SKIPPED),
        unreachable=_left_out(read, Uncounted.UNREACHABLE),
        outdated=_left_out(read, Uncounted.OUTDATED),
    )


def _with_runs(answer: StatsGetResult | Uncounted) -> StatsGetResult | Uncounted:
    """A core older than run usage is outdated, as one older than plan use is, so its runs
    never read as none."""
    if isinstance(answer, StatsGetResult) and answer.runs is None:
        return Uncounted.OUTDATED
    return answer


def run_usage(
    reworked: Iterable[tuple[FactoryRun, Rework]],
    answers: Mapping[UUID, StatsGetResult | Uncounted],
) -> StatsRunsResult:
    """Join each run's rework with its tokens in every counted instance, since a step can run
    in an instance other than the one that handed the run in."""
    read = {instance_id: _run_tokens(answer) for instance_id, answer in answers.items()}
    tokens: dict[UUID, list[RunTokens]] = {}
    for runs in read.values():
        if not isinstance(runs, Uncounted):
            for run in runs:
                tokens.setdefault(run.run_id, []).append(run)
    usage = [_run_usage(run, rework, tokens.get(run.run_id, [])) for run, rework in reworked]
    return StatsRunsResult(
        runs=usage,
        weeks=_weeks(usage),
        skipped=_left_out(read, Uncounted.SKIPPED),
        unreachable=_left_out(read, Uncounted.UNREACHABLE),
        outdated=_left_out(read, Uncounted.OUTDATED),
    )


def _run_tokens(answer: StatsGetResult | Uncounted) -> list[RunTokens] | Uncounted:
    """The instance's run tokens. A core older than run usage answers without them."""
    if isinstance(answer, Uncounted):
        return answer
    return Uncounted.OUTDATED if answer.runs is None else answer.runs


def _run_usage(run: FactoryRun, rework: Rework, counted: list[RunTokens]) -> RunUsage:
    api = _summed_api([tokens.api for tokens in counted])
    subscriptions = _summed_uses([use for tokens in counted for use in tokens.subscriptions])
    return RunUsage(
        run=run,
        tokens=_tokens(api, subscriptions),
        api=api,
        subscriptions=subscriptions,
        attempts=rework.attempts,
        retries=rework.retries,
        send_backs=rework.send_backs,
        duration_seconds=(
            (run.updated_at - run.created_at).total_seconds() if run.status in _ENDED else None
        ),
        recalled_fact=any(tokens.recalled_fact for tokens in counted),
    )


def _run_totals(runs: list[RunTokens]) -> RunTotals:
    return RunTotals(
        runs=len({run.run_id for run in runs}),
        tokens=sum(_tokens(run.api, run.subscriptions) for run in runs),
        cost=_known_cost(run.api.cost for run in runs),
    )


def _tokens(api: ApiUse, subscriptions: list[SubscriptionUse]) -> int:
    """Input and output tokens across every usage source."""
    return api.total + sum(use.total for use in subscriptions)


def _weeks(usage: list[RunUsage]) -> list[RunWeek]:
    by_week: dict[date, list[RunUsage]] = {}
    for run in usage:
        by_week.setdefault(bucket_start(run.run.created_at, StatsBucketSize.WEEK), []).append(run)
    return [_week(start, runs) for start, runs in sorted(by_week.items())]


def _week(start: date, runs: list[RunUsage]) -> RunWeek:
    ended = [run.duration_seconds for run in runs if run.duration_seconds is not None]
    return RunWeek(
        start=start,
        runs=len(runs),
        tokens=median(run.tokens for run in runs),
        attempts=median(sum(step.attempts for step in run.attempts) for run in runs),
        duration_seconds=median(ended) if ended else None,
    )


def _left_out(answers: Mapping[UUID, object], why: Uncounted) -> list[UUID]:
    return [instance_id for instance_id, answer in answers.items() if answer is why]


def _summed_api(uses: Sequence[StatsSummary | ApiUse]) -> ApiUse:
    return ApiUse(
        input_tokens=sum(use.input_tokens for use in uses),
        output_tokens=sum(use.output_tokens for use in uses),
        cache_read_tokens=sum(use.cache_read_tokens for use in uses),
        cache_creation_tokens=sum(use.cache_creation_tokens for use in uses),
        cost=_known_cost(use.cost for use in uses),
    )


def _summed_uses(uses: list[SubscriptionUse]) -> list[SubscriptionUse]:
    """One use per subscription source, summed."""
    return [
        _summed_use(source, [use for use in uses if use.usage_source is source])
        for source in SUBSCRIPTION_SOURCES
    ]


def _known_cost(costs: Iterable[float | None]) -> float | None:
    """Sum the priced costs. None only when no instance priced anything."""
    known = [cost for cost in costs if cost is not None]
    return sum(known) if known else None


def _summed_use(source: UsageSource, uses: list[SubscriptionUse]) -> SubscriptionUse:
    return SubscriptionUse(
        usage_source=source,
        runs=sum(use.runs for use in uses),
        input_tokens=sum(use.input_tokens for use in uses),
        output_tokens=sum(use.output_tokens for use in uses),
        cache_read_tokens=sum(use.cache_read_tokens for use in uses),
        cache_creation_tokens=sum(use.cache_creation_tokens for use in uses),
        duration_ms=sum(use.duration_ms for use in uses),
    )


def _summed_plan_use(uses: list[PlanUse]) -> list[PlanUse]:
    """One entry per source and window, its runs summed across instances."""
    windows = [(source, int(length.total_seconds())) for source, length in PLAN_WINDOWS]
    return [
        PlanUse(
            usage_source=source,
            duration_seconds=seconds,
            runs=sum(
                use.runs
                for use in uses
                if (use.usage_source, use.duration_seconds) == (source, seconds)
            ),
        )
        for source, seconds in windows
    ]


def _latest_resets(limits: Iterable[PlanLimit]) -> list[PlanLimit]:
    """One limit per source: the one whose plan window resets last."""
    latest: dict[UsageSource, PlanLimit] = {}
    for limit in limits:
        held = latest.get(limit.usage_source)
        if held is None or limit.resets_at > held.resets_at:
            latest[limit.usage_source] = limit
    return [latest[source] for source in SUBSCRIPTION_SOURCES if source in latest]
