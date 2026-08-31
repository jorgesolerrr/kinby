from kinby.contracts import PermissionMode
from kinby.core.turns import (
    ApprovalDecision,
    Emit,
    TurnOutcome,
    TurnPreparation,
    TurnRequest,
)


def fixed_turn_preparation() -> TurnPreparation:
    return TurnPreparation(
        model="openai:gpt-5",
        default_mode=PermissionMode.ASK,
        ceiling=PermissionMode.FULL_ACCESS,
    )


def fixed_permission_ceiling() -> PermissionMode:
    return PermissionMode.FULL_ACCESS


def cannot_resume(self: object, turn: TurnRequest) -> bool:
    return False


async def discard_turn(self: object, turn: TurnRequest) -> None:
    pass


async def does_not_park(
    self: object,
    turn: TurnRequest,
    decision: ApprovalDecision,
    emit: Emit,
) -> TurnOutcome:
    raise AssertionError("this runner does not park")
