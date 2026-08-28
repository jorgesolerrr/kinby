"""Errors whose codes are part of the contract."""

from kinby.contracts import ErrorCode


class CoreError(Exception):
    code: ErrorCode = ErrorCode.INTERNAL
    retryable: bool = False


class ThreadNotFound(CoreError):
    code = ErrorCode.NOT_FOUND


class ThreadBusy(CoreError):
    code = ErrorCode.THREAD_BUSY
    retryable = True


class ModelNoResponse(CoreError):
    pass
