"""Errors raised by the core package."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from kinby.contracts import ErrorCode, ErrorEnvelope

if TYPE_CHECKING:
    from kinby.core.turn_metrics import UnpricedModel

BudgetName = Literal["steps", "tokens", "seconds", "usd_per_day"]


class CoreError(Exception):
    code: ErrorCode = ErrorCode.INTERNAL
    retryable: bool = False

    def envelope(self) -> ErrorEnvelope:
        """How a client hears about this error."""
        return ErrorEnvelope(code=self.code, message=str(self), retryable=self.retryable)


class CodeStepFailed(CoreError):
    """A routine code step could not finish."""


class CodeStepNotFound(CoreError):
    code = ErrorCode.NOT_FOUND


class RoutineNotFound(CoreError):
    code = ErrorCode.NOT_FOUND


class RoutinePending(CoreError):
    """Deliveries wait on the routine, so deleting it would drop them."""

    code = ErrorCode.ROUTINE_PENDING


class RoutineRefused(CoreError):
    """The routine loader refused the routine, so nothing was written."""

    code = ErrorCode.INVALID_ARGUMENT


class SkillNotFound(CoreError):
    code = ErrorCode.NOT_FOUND


class StaleWrite(CoreError):
    """The file changed since the client read it, so the write would overwrite a change unseen."""

    code = ErrorCode.STALE


class PackageConfigNotFound(CoreError):
    """The instance runs no package, or its package declares no config."""

    code = ErrorCode.NOT_FOUND


class ThreadNotFound(CoreError):
    code = ErrorCode.NOT_FOUND


class MemoryNodeNotFound(CoreError):
    """No live knowledge graph node has this id: none was written, or it was forgotten."""

    code = ErrorCode.NOT_FOUND


class InvalidMemoryNode(CoreError):
    """The id cannot name a knowledge graph node, such as one with a path in it."""

    code = ErrorCode.INVALID_ARGUMENT


class InstanceBusy(CoreError):
    code = ErrorCode.INSTANCE_BUSY
    retryable = True


class InstanceDraining(CoreError):
    """The instance is finishing its accepted work and takes no new work."""

    code = ErrorCode.INSTANCE_DRAINING


class ManagedInstanceNotFound(CoreError):
    code = ErrorCode.NOT_FOUND


class AdoptionBlocked(CoreError):
    """The adoption preflight found something the operator has to settle first."""

    code = ErrorCode.INVALID_ARGUMENT


class PackagePinRefused(CoreError):
    """A package pin names a package the instance does not run, or one not from git."""

    code = ErrorCode.INVALID_ARGUMENT


class SelectionNotPrepared(CoreError):
    """No image preparation stored a descriptor for this selection, and describing never builds."""

    code = ErrorCode.NOT_PREPARED


class InvalidValues(CoreError):
    """Some values the client sent are invalid. The envelope names what is wrong with each."""

    message = "Some values are invalid."

    def __init__(self, fields: dict[str, str]) -> None:
        super().__init__(self.message)
        self.fields = fields

    def envelope(self) -> ErrorEnvelope:
        return ErrorEnvelope(
            code=self.code, message=str(self), retryable=self.retryable, fields=self.fields
        )


class InvalidSetup(InvalidValues):
    """Some setup values are missing or invalid, so nothing was created."""

    code = ErrorCode.INVALID_SETUP
    message = "Some setup values are missing or invalid."


class InvalidConfig(InvalidValues):
    """Some config values are invalid, so the file was left alone."""

    code = ErrorCode.INVALID_ARGUMENT


class LifecycleOperationNotFound(CoreError):
    code = ErrorCode.NOT_FOUND


class LoginNotFound(CoreError):
    """The instance's package declares no subscription login by this id."""

    code = ErrorCode.NOT_FOUND


class LifecycleOperationInFlight(CoreError):
    """Another lifecycle operation owns this managed instance right now."""

    code = ErrorCode.INSTANCE_BUSY
    retryable = True


class ThreadBusy(CoreError):
    code = ErrorCode.THREAD_BUSY
    retryable = True


class TurnOpen(CoreError):
    code = ErrorCode.TURN_OPEN


class TurnNotFound(CoreError):
    code = ErrorCode.NOT_FOUND


class SnapshotUnavailable(CoreError):
    code = ErrorCode.SNAPSHOT_UNAVAILABLE


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
