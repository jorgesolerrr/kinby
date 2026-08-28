from kinby.core.turns import Emit, TurnOutcome, TurnRequest


def fixed_model_name() -> str:
    return "openai:gpt-5"


async def does_not_park(self: object, turn: TurnRequest, answer: str, emit: Emit) -> TurnOutcome:
    raise AssertionError("this runner does not park")
