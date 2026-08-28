from kinby.core.turns import Emit, TurnOutcome, TurnRequest


async def does_not_park(self: object, turn: TurnRequest, answer: str, emit: Emit) -> TurnOutcome:
    raise AssertionError("this runner does not park")
