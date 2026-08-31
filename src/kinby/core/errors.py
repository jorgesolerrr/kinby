"""Errors raised by the core package."""

import asyncio

from kinby.contracts import ErrorCode


class CoreError(Exception):
    code: ErrorCode = ErrorCode.INTERNAL
    retryable: bool = False


class ThreadNotFound(CoreError):
    code = ErrorCode.NOT_FOUND


class ThreadBusy(CoreError):
    code = ErrorCode.THREAD_BUSY
    retryable = True


class PermissionDenied(CoreError):
    code = ErrorCode.PERMISSION_DENIED


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
