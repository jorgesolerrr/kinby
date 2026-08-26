"""Keep validation rules identical on both sides of the dispatcher boundary."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Reject unknown input before a handler can mistake it for valid data."""

    model_config = ConfigDict(extra="forbid")


class Scope(str, Enum):
    THREAD_READ = "thread:read"
    THREAD_OPERATE = "thread:operate"
    INSTANCE_READ = "instance:read"
    INSTANCE_ADMIN = "instance:admin"


class ErrorCode(str, Enum):
    NOT_FOUND = "NOT_FOUND"
    THREAD_BUSY = "THREAD_BUSY"
    NO_ACTIVE_TURN = "NO_ACTIVE_TURN"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    INTERNAL = "INTERNAL"


class ErrorEnvelope(ContractModel):
    code: ErrorCode
    message: str
    retryable: bool


class ThreadCreateCommand(ContractModel):
    title: str | None = None


class ThreadCreateResult(ContractModel):
    id: UUID
    created_at: datetime


class ThreadListCommand(ContractModel):
    pass


class ThreadSummary(ContractModel):
    id: UUID
    title: str | None
    created_at: datetime


class ThreadListResult(ContractModel):
    threads: list[ThreadSummary]
