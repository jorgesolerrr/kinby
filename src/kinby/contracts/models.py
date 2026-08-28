"""Keep validation rules identical on both sides of the dispatcher boundary."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    """Reject unknown input before a handler can mistake it for valid data."""

    model_config = ConfigDict(extra="forbid")


class Scope(StrEnum):
    THREAD_READ = "thread:read"
    THREAD_OPERATE = "thread:operate"
    INSTANCE_READ = "instance:read"
    INSTANCE_ADMIN = "instance:admin"


class ErrorCode(StrEnum):
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


class EventType(StrEnum):
    TURN_STARTED = "turn.started"
    MESSAGE_DELTA = "message.delta"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    APPROVAL_REQUESTED = "approval.requested"
    TURN_COMPLETED = "turn.completed"
    TURN_FAILED = "turn.failed"
    TURN_INTERRUPTED = "turn.interrupted"


class TurnStarted(ContractModel):
    type: Literal[EventType.TURN_STARTED] = EventType.TURN_STARTED
    message: str
    model: str


class MessageDelta(ContractModel):
    type: Literal[EventType.MESSAGE_DELTA] = EventType.MESSAGE_DELTA
    text: str


class TurnCompleted(ContractModel):
    type: Literal[EventType.TURN_COMPLETED] = EventType.TURN_COMPLETED
    input_tokens: int = 0
    output_tokens: int = 0


class TurnFailed(ContractModel):
    type: Literal[EventType.TURN_FAILED] = EventType.TURN_FAILED
    code: ErrorCode
    message: str


class TurnInterrupted(ContractModel):
    type: Literal[EventType.TURN_INTERRUPTED] = EventType.TURN_INTERRUPTED


Payload = Annotated[
    TurnStarted | MessageDelta | TurnCompleted | TurnFailed | TurnInterrupted,
    Field(discriminator="type"),
]


class Event(ContractModel):
    sequence: int
    thread_id: UUID
    turn_id: UUID
    payload: Payload
    timestamp: datetime

    @property
    def type(self) -> EventType:
        return self.payload.type


class ThreadSubscribeCommand(ContractModel):
    thread_id: UUID
    after_sequence: Annotated[int, Field(ge=0)] = 0


class ThreadTurnStartCommand(ContractModel):
    thread_id: UUID
    message: str


class ThreadTurnInterruptCommand(ContractModel):
    thread_id: UUID


class AcceptedResult(ContractModel):
    thread_id: UUID
    turn_id: UUID
    sequence: int


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
