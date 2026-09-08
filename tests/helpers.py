from collections.abc import Awaitable, Callable
from uuid import UUID

from kinby.contracts import PermissionMode, PromptVersion, SystemPrompt, TreeId
from kinby.core.budgets import DailyBudget, DailyCost
from kinby.core.dispatcher import TurnConfig
from kinby.core.snapshots import SnapshotError, SnapshotRef
from kinby.core.turn_metrics import UnpricedModel
from kinby.core.turns import (
    ApprovalDecision,
    Emit,
    PreparedTurnRequest,
    TurnOutcome,
    TurnPreparation,
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
        prompt_version=PromptVersion("123456789abc"),
        system_prompt=SystemPrompt("System prompt"),
        daily_budget=daily_budget,
        budgets=budgets,
    )


def fixed_permission_ceiling() -> PermissionMode:
    return PermissionMode.FULL_ACCESS


async def cannot_restore(
    self: object,
    thread_id: UUID,
    turn_id: UUID,
) -> PreparedTurnRequest | None:
    return None


async def does_not_park(
    self: object,
    turn: PreparedTurnRequest,
    decision: ApprovalDecision,
    emit: Emit,
) -> TurnOutcome:
    raise AssertionError("this runner does not park")


class FakeSnapshotStore:
    """Record the refs a turn asks to capture and hand back one tree id per call."""

    def __init__(self, *, failing: bool = False) -> None:
        self.refs: list[SnapshotRef] = []
        self._failing = failing

    async def capture(self, ref: SnapshotRef) -> TreeId:
        self.refs.append(ref)
        if self._failing:
            raise SnapshotError("git add --all failed")
        return TreeId(f"{len(self.refs):040d}")


def turn_config_stub(build: Callable[[], TurnConfig]) -> Callable[..., Awaitable[TurnConfig]]:
    """Stand in for the boot step that opens a runner, a recap and a snapshot store."""

    async def configured(*args: object, **kwargs: object) -> TurnConfig:
        return build()

    return configured
