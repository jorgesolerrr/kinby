from uuid import UUID

from kinby.contracts import PermissionMode
from kinby.core.budgets import DailyBudget, DailyCost
from kinby.core.turn_metrics import UnpricedModel
from kinby.core.turns import (
    ApprovalDecision,
    Emit,
    TurnOutcome,
    TurnPreparation,
    TurnRequest,
)
from kinby.instance import Budgets

GRAPH_EVENT_TIMEOUT: float = 5
_DEFAULT_BUDGETS = Budgets()
_DEFAULT_DAILY_COST = DailyCost()


def fixed_turn_preparation(
    *,
    model: str = "openai:gpt-5",
    budgets: Budgets = _DEFAULT_BUDGETS,
    daily_cost: DailyCost = _DEFAULT_DAILY_COST,
    unpriced_model: UnpricedModel | None = None,
) -> TurnPreparation:
    daily_budget = (
        DailyBudget(budgets.usd_per_day, daily_cost, unpriced_model)
        if budgets.usd_per_day is not None
        else None
    )
    return TurnPreparation(
        model=model,
        default_mode=PermissionMode.ASK,
        ceiling=PermissionMode.FULL_ACCESS,
        daily_budget=daily_budget,
        budgets=budgets,
    )


def fixed_permission_ceiling() -> PermissionMode:
    return PermissionMode.FULL_ACCESS


async def cannot_restore(
    self: object,
    thread_id: UUID,
    turn_id: UUID,
) -> TurnRequest | None:
    return None


async def does_not_park(
    self: object,
    turn: TurnRequest,
    decision: ApprovalDecision,
    emit: Emit,
) -> TurnOutcome:
    raise AssertionError("this runner does not park")
