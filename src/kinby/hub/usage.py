"""Add up the usage each running instance reports, without keeping any of it (ADR 0063)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from uuid import UUID

from kinby.contracts import (
    ApiUse,
    PlanLimit,
    StatsGetResult,
    StatsSummaryResult,
    SubscriptionUse,
    UsageSource,
)
from kinby.core.stats import SUBSCRIPTION_SOURCES


class Uncounted(StrEnum):
    """Why an instance's usage is missing from a summary."""

    SKIPPED = "skipped"
    UNREACHABLE = "unreachable"


def summed_usage(answers: Mapping[UUID, StatsGetResult | Uncounted]) -> StatsSummaryResult:
    """Sum every counted instance per usage source. One account per source (ADR 0063)."""
    counted = {
        instance_id: answer
        for instance_id, answer in answers.items()
        if isinstance(answer, StatsGetResult)
    }
    totals = [answer.total for answer in counted.values()]
    uses = [use for total in totals for use in total.subscriptions]
    return StatsSummaryResult(
        buckets={instance_id: answer.buckets for instance_id, answer in counted.items()},
        api=ApiUse(
            input_tokens=sum(total.input_tokens for total in totals),
            output_tokens=sum(total.output_tokens for total in totals),
            cache_read_tokens=sum(total.cache_read_tokens for total in totals),
            cache_creation_tokens=sum(total.cache_creation_tokens for total in totals),
            cost=_known_cost(total.cost for total in totals),
        ),
        subscriptions=[
            _summed_use(source, [use for use in uses if use.usage_source is source])
            for source in SUBSCRIPTION_SOURCES
        ],
        limits=_latest_resets(limit for answer in counted.values() for limit in answer.limits),
        skipped=[
            instance_id for instance_id, answer in answers.items() if answer is Uncounted.SKIPPED
        ],
        unreachable=[
            instance_id
            for instance_id, answer in answers.items()
            if answer is Uncounted.UNREACHABLE
        ],
    )


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


def _latest_resets(limits: Iterable[PlanLimit]) -> list[PlanLimit]:
    """One limit per source: the one whose plan window resets last."""
    latest: dict[UsageSource, PlanLimit] = {}
    for limit in limits:
        held = latest.get(limit.usage_source)
        if held is None or limit.resets_at > held.resets_at:
            latest[limit.usage_source] = limit
    return [latest[source] for source in SUBSCRIPTION_SOURCES if source in latest]
