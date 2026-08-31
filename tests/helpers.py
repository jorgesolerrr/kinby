from uuid import UUID

from kinby.contracts import PermissionMode
from kinby.core.turns import (
    ApprovalDecision,
    Emit,
    TurnOutcome,
    TurnPreparation,
    TurnRequest,
)

GRAPH_EVENT_TIMEOUT: float = 5


def fixed_turn_preparation() -> TurnPreparation:
    return TurnPreparation(
        model="openai:gpt-5",
        default_mode=PermissionMode.ASK,
        ceiling=PermissionMode.FULL_ACCESS,
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
