"""Keep validation rules identical on both sides of the dispatcher boundary."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, NewType
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

NodeId = NewType("NodeId", str)


class ContractModel(BaseModel):
    """Reject unknown input before a handler can mistake it for valid data."""

    model_config = ConfigDict(extra="forbid")


class Scope(StrEnum):
    THREAD_READ = "thread:read"
    THREAD_OPERATE = "thread:operate"
    THREAD_ADMIN = "thread:admin"
    INSTANCE_READ = "instance:read"
    INSTANCE_ADMIN = "instance:admin"


class ErrorCode(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    THREAD_BUSY = "THREAD_BUSY"
    NO_ACTIVE_TURN = "NO_ACTIVE_TURN"
    PARKED_TURN_UNAVAILABLE = "PARKED_TURN_UNAVAILABLE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    INTERNAL = "INTERNAL"


class ErrorEnvelope(ContractModel):
    code: ErrorCode
    message: str
    retryable: bool


class PermissionMode(StrEnum):
    READ_ONLY = "read-only"
    ASK = "ask"
    AUTO = "auto"
    FULL_ACCESS = "full-access"


class EventType(StrEnum):
    MODE_PINNED = "mode.pinned"
    TURN_STARTED = "turn.started"
    MESSAGE_DELTA = "message.delta"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    WARNING = "warning"
    APPROVAL_REQUESTED = "approval.requested"
    TURN_COMPLETED = "turn.completed"
    TURN_FAILED = "turn.failed"
    TURN_INTERRUPTED = "turn.interrupted"
    MEMORY_RECAPPED = "memory.recapped"


class TurnStarted(ContractModel):
    type: Literal[EventType.TURN_STARTED] = EventType.TURN_STARTED
    message: str
    model: str
    permission_mode: PermissionMode | None = None


class ModePinned(ContractModel):
    type: Literal[EventType.MODE_PINNED] = EventType.MODE_PINNED
    mode: PermissionMode


class MessageDelta(ContractModel):
    type: Literal[EventType.MESSAGE_DELTA] = EventType.MESSAGE_DELTA
    text: str


class ToolCall(ContractModel):
    type: Literal[EventType.TOOL_CALL] = EventType.TOOL_CALL
    call_id: str
    name: str
    arguments: dict[str, JsonValue]


class ToolResult(ContractModel):
    type: Literal[EventType.TOOL_RESULT] = EventType.TOOL_RESULT
    call_id: str
    name: str
    output: str
    error: bool


class Warning(ContractModel):
    type: Literal[EventType.WARNING] = EventType.WARNING
    sources: tuple[str, ...]
    message: str


class ApprovalRequested(ContractModel):
    type: Literal[EventType.APPROVAL_REQUESTED] = EventType.APPROVAL_REQUESTED
    approval_id: UUID
    name: str
    arguments: dict[str, JsonValue]
    rule: str


class TokenTotals(ContractModel):
    input_tokens: int
    output_tokens: int

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class TurnCompleted(TokenTotals):
    type: Literal[EventType.TURN_COMPLETED] = EventType.TURN_COMPLETED


class TurnFailed(ContractModel):
    type: Literal[EventType.TURN_FAILED] = EventType.TURN_FAILED
    code: ErrorCode
    message: str


class TurnInterrupted(ContractModel):
    type: Literal[EventType.TURN_INTERRUPTED] = EventType.TURN_INTERRUPTED


class MemoryRecapped(TokenTotals):
    type: Literal[EventType.MEMORY_RECAPPED] = EventType.MEMORY_RECAPPED
    node: NodeId | None


Payload = Annotated[
    ModePinned
    | TurnStarted
    | MessageDelta
    | ToolCall
    | ToolResult
    | Warning
    | ApprovalRequested
    | TurnCompleted
    | TurnFailed
    | TurnInterrupted
    | MemoryRecapped,
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


class ThreadModeSetCommand(ContractModel):
    thread_id: UUID
    mode: PermissionMode


class ThreadTurnInterruptCommand(ContractModel):
    thread_id: UUID


class ThreadApprovalRespondCommand(ContractModel):
    thread_id: UUID
    approval_id: UUID
    answer: str


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


class UsageGetCommand(ContractModel):
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None


class TurnUsage(TokenTotals):
    turn_id: UUID


class ThreadUsage(TokenTotals):
    thread_id: UUID
    turns: list[TurnUsage]


class UsageGetResult(ContractModel):
    threads: list[ThreadUsage]
