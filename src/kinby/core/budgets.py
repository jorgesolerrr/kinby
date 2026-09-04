"""Derive and enforce instance spending budgets."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from kinby.contracts import Event
from kinby.core.errors import BudgetExceeded, ModelUnpriced
from kinby.core.turn_metrics import TurnKey, UnpricedModel, closing_day, turn_metrics
from kinby.instance import ModelPrice


@dataclass(frozen=True)
class DailyCost:
    """Priced spend and models that prevent a complete daily total."""

    usd: float = 0
    unpriced_models: tuple[UnpricedModel, ...] = ()


@dataclass(frozen=True)
class DailyBudget:
    """The state needed to enforce a configured daily spending limit."""

    limit: float
    cost: DailyCost
    unpriced_model: UnpricedModel | None = None


def daily_cost(
    events: Iterable[Event],
    prices: Mapping[str, ModelPrice],
    day: date,
) -> DailyCost:
    """Return spend closed on *day* and its unpriced models."""
    metrics = turn_metrics(events, prices)
    records = [record for record in metrics.records if closing_day(record) == day]
    unpriced_models = {
        unpriced
        for record in records
        for unpriced in metrics.unpriced_models_by_turn.get(
            TurnKey(record.thread_id, record.turn_id),
            (),
        )
    }
    return DailyCost(
        usd=sum(record.cost for record in records if record.cost is not None),
        unpriced_models=tuple(sorted(unpriced_models)),
    )


def check_daily_budget(budget: DailyBudget | None) -> None:
    """Refuse a turn start when the model is unpriced or daily cost reached its limit."""
    if budget is None:
        return
    if budget.unpriced_model is not None:
        raise ModelUnpriced(budget.unpriced_model)
    if budget.cost.unpriced_models:
        raise ModelUnpriced(budget.cost.unpriced_models[0])
    if budget.cost.usd >= budget.limit:
        raise BudgetExceeded("usd_per_day", budget.limit)
