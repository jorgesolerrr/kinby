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


class NoActiveTurn(CoreError):
    code = ErrorCode.NO_ACTIVE_TURN


class ModelNoResponse(CoreError):
    pass


class TurnInterruptedError(asyncio.CancelledError):
    """Stop work that tries to emit after its turn was interrupted."""
