"""Errors raised by the core package."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from kinby.contracts import ErrorCode

if TYPE_CHECKING:
    from kinby.core.turn_metrics import UnpricedModel

BudgetName = Literal["steps", "tokens", "seconds", "usd_per_day"]


class CoreError(Exception):
    code: ErrorCode = ErrorCode.INTERNAL
    retryable: bool = False


class ThreadNotFound(CoreError):
    code = ErrorCode.NOT_FOUND


class ThreadBusy(CoreError):
    code = ErrorCode.THREAD_BUSY
    retryable = True


class TurnOpen(CoreError):
    code = ErrorCode.TURN_OPEN


class TurnNotFound(CoreError):
    code = ErrorCode.NOT_FOUND


class PermissionDenied(CoreError):
    code = ErrorCode.PERMISSION_DENIED


class BudgetExceeded(CoreError):
    code = ErrorCode.BUDGET_EXCEEDED

    def __init__(self, budget: BudgetName, value: int | float) -> None:
        if budget == "usd_per_day":
            amount = format(Decimal(str(value)), "f")
            super().__init__(f"The daily cost reached the usd_per_day budget of {amount}.")
            return
        super().__init__(f"The turn exceeded the {budget} budget of {value}.")


class ModelUnpriced(CoreError):
    code = ErrorCode.MODEL_UNPRICED

    def __init__(self, model: UnpricedModel) -> None:
        super().__init__(f'Model "{model}" has no price. Add [prices."{model}"] to kinby.toml.')


class NoActiveTurn(CoreError):
    code = ErrorCode.NO_ACTIVE_TURN


class ModelNoResponse(CoreError):
    pass


class InvalidApprovalRequest(CoreError):
    """The turn runner produced an approval request with the wrong type."""


class TurnInterruptedError(asyncio.CancelledError):
    """Stop work that tries to emit after its turn was interrupted."""


class ApprovalNotFound(CoreError):
    code = ErrorCode.NOT_FOUND


class InvalidParkedTurn(CoreError):
    """The live runner state for a parked turn is unavailable."""

    code = ErrorCode.PARKED_TURN_UNAVAILABLE
