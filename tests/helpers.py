from kinby.core.turns import ApprovalDecision, Emit, TurnOutcome, TurnRequest


def fixed_model_name() -> str:
    return "openai:gpt-5"


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
