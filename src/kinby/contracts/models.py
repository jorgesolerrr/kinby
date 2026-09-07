"""Keep validation rules identical on both sides of the dispatcher boundary."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal, NewType, TypeIs
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

NodeId = NewType("NodeId", str)
GateRule = NewType("GateRule", str)


class ContractModel(BaseModel):
    """Reject unknown input before a handler can mistake it for valid data."""

    model_config = ConfigDict(extra="forbid")


class Scope(StrEnum):
    THREAD_READ = "thread:read"
    THREAD_OPERATE = "thread:operate"
    THREAD_ADMIN = "thread:admin"
    THREAD_RATE = "thread:rate"
    INSTANCE_READ = "instance:read"
    INSTANCE_ADMIN = "instance:admin"


class ErrorCode(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    THREAD_BUSY = "THREAD_BUSY"
    INSTANCE_BUSY = "INSTANCE_BUSY"
    TURN_OPEN = "TURN_OPEN"
    NO_ACTIVE_TURN = "NO_ACTIVE_TURN"
    PARKED_TURN_UNAVAILABLE = "PARKED_TURN_UNAVAILABLE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    MODEL_UNPRICED = "MODEL_UNPRICED"
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


class GateOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class GateDecider(StrEnum):
    POLICY = "policy"
    USER = "user"


class EventType(StrEnum):
    MODE_PINNED = "mode.pinned"
    TURN_STARTED = "turn.started"
    MESSAGE_DELTA = "message.delta"
    MODEL_COMPLETED = "model.completed"
    TOOL_CALL = "tool.call"
    TOOL_GATED = "tool.gated"
    TOOL_RESULT = "tool.result"
    WARNING = "warning"
    APPROVAL_REQUESTED = "approval.requested"
    TURN_COMPLETED = "turn.completed"
    TURN_FAILED = "turn.failed"
    TURN_INTERRUPTED = "turn.interrupted"
    TURN_RATED = "turn.rated"
    MEMORY_RECAPPED = "memory.recapped"
    ROUTINE_FAILURE_HANDLED = "routine.failure.handled"
    SIGNAL_RECEIVED = "signal.received"


class UserOrigin(ContractModel):
    kind: Literal["user"] = "user"


class RoutineTrigger(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"
    CATCH_UP = "catch-up"
    SIGNAL = "signal"


class SignalAuth(StrEnum):
    TOKEN = "token"
    HMAC_SHA256 = "hmac-sha256"


RoutineName = NewType("RoutineName", str)
CronSchedule = NewType("CronSchedule", str)
DeliveryId = NewType("DeliveryId", str)


class RoutineOrigin(ContractModel):
    kind: Literal["routine"] = "routine"
    name: RoutineName
    trigger: RoutineTrigger
    delivery_id: DeliveryId | None = None


Origin = Annotated[UserOrigin | RoutineOrigin, Field(discriminator="kind")]


class TurnStarted(ContractModel):
    type: Literal[EventType.TURN_STARTED] = EventType.TURN_STARTED
    message: str
    model: str
    permission_mode: PermissionMode | None = None
    origin: Origin = Field(default_factory=UserOrigin)


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
    write: bool | None = None


class ToolGated(ContractModel):
    type: Literal[EventType.TOOL_GATED] = EventType.TOOL_GATED
    call_id: str
    name: str
    action: GateOutcome
    rule: GateRule
    decided_by: GateDecider


def gate_denial_source(gate: ToolGated) -> str:
    return "the user" if gate.decided_by is GateDecider.USER else f'policy rule "{gate.rule}"'


class ToolResult(ContractModel):
    type: Literal[EventType.TOOL_RESULT] = EventType.TOOL_RESULT
    call_id: str
    name: str
    output: str
    error: bool
    duration_ms: int | None = None


class Warning(ContractModel):
    type: Literal[EventType.WARNING] = EventType.WARNING
    sources: tuple[str, ...]
    message: str


class ApprovalRequested(ContractModel):
    type: Literal[EventType.APPROVAL_REQUESTED] = EventType.APPROVAL_REQUESTED
    approval_id: UUID
    name: str
    arguments: dict[str, JsonValue]
    rule: GateRule


class TokenTotals(ContractModel):
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class CompletionOutcome(StrEnum):
    WORK = "work"
    NO_WORK = "no-work"


class TurnCompleted(TokenTotals):
    type: Literal[EventType.TURN_COMPLETED] = EventType.TURN_COMPLETED
    outcome: CompletionOutcome = CompletionOutcome.WORK


class ModelCompleted(TokenTotals):
    type: Literal[EventType.MODEL_COMPLETED] = EventType.MODEL_COMPLETED
    model: str
    duration_ms: int


class TurnFailed(TokenTotals):
    type: Literal[EventType.TURN_FAILED] = EventType.TURN_FAILED
    input_tokens: int = 0
    output_tokens: int = 0
    code: ErrorCode
    message: str


class TurnInterrupted(TokenTotals):
    type: Literal[EventType.TURN_INTERRUPTED] = EventType.TURN_INTERRUPTED
    input_tokens: int = 0
    output_tokens: int = 0


type TurnClosingPayload = TurnCompleted | TurnFailed | TurnInterrupted


class TurnVerdict(StrEnum):
    GOOD = "good"
    BAD = "bad"


class TurnRated(ContractModel):
    type: Literal[EventType.TURN_RATED] = EventType.TURN_RATED
    verdict: TurnVerdict
    reason: str | None = None


class MemoryRecapped(TokenTotals):
    type: Literal[EventType.MEMORY_RECAPPED] = EventType.MEMORY_RECAPPED
    node: NodeId | None
    model: str | None = None


class RoutineNoticeKind(StrEnum):
    FIRST_FAILURE = "first-failure"
    DISABLED = "disabled"


class RoutineNotice(ContractModel):
    kind: RoutineNoticeKind
    message: str
    thread_id: UUID
    turn_id: UUID


class RoutineFailureHandled(ContractModel):
    type: Literal[EventType.ROUTINE_FAILURE_HANDLED] = EventType.ROUTINE_FAILURE_HANDLED
    name: RoutineName
    notice: RoutineNotice | None = None


class Delivery(ContractModel):
    headers: dict[str, str]
    content_type: str
    body: str
    delivery_id: DeliveryId | None = None
    received_at: AwareDatetime


class SignalReceived(ContractModel):
    type: Literal[EventType.SIGNAL_RECEIVED] = EventType.SIGNAL_RECEIVED
    origin: RoutineOrigin
    delivery: Delivery


Payload = Annotated[
    ModePinned
    | TurnStarted
    | MessageDelta
    | ModelCompleted
    | ToolCall
    | ToolGated
    | ToolResult
    | Warning
    | ApprovalRequested
    | TurnCompleted
    | TurnFailed
    | TurnInterrupted
    | TurnRated
    | MemoryRecapped
    | RoutineFailureHandled
    | SignalReceived,
    Field(discriminator="type"),
]


def is_turn_closing(payload: Payload) -> TypeIs[TurnClosingPayload]:
    return isinstance(payload, TurnCompleted | TurnFailed | TurnInterrupted)


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


class ThreadTurnRateCommand(ContractModel):
    thread_id: UUID
    turn_id: UUID
    verdict: TurnVerdict
    reason: str | None = None


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
    recap_input_tokens: int
    recap_output_tokens: int


class ThreadUsage(TokenTotals):
    thread_id: UUID
    turns: list[TurnUsage]


class UsageGetResult(ContractModel):
    threads: list[ThreadUsage]


class StatsBucketSize(StrEnum):
    DAY = "day"
    WEEK = "week"


class TurnClosingKind(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class MemoryCallCounts(ContractModel):
    search: int = 0
    open: int = 0
    remember: int = 0
    forget: int = 0


class DenyCounts(ContractModel):
    policy: int = 0
    user: int = 0


class ToolTime(ContractModel):
    read_ms: int = 0
    write_ms: int = 0


class TurnMetrics(TokenTotals):
    thread_id: UUID
    turn_id: UUID
    model: str | None
    closing_kind: TurnClosingKind
    started_at: datetime | None
    closed_at: datetime
    duration_seconds: float | None
    recap_input_tokens: int
    recap_output_tokens: int
    cost: float | None = None
    tool_calls: dict[str, int]
    memory_calls: MemoryCallCounts
    memory_consulted: bool
    approvals_requested: int
    denies: DenyCounts = Field(default_factory=DenyCounts)
    tool_duration: ToolTime = Field(default_factory=ToolTime)
    memory_tokens: float
    rating: TurnRated | None


class StatsSummary(TokenTotals):
    completed: int
    failed: int
    interrupted: int
    recap_input_tokens: int
    recap_output_tokens: int
    cost: float | None = None
    tool_calls: dict[str, int]
    memory_calls: MemoryCallCounts
    turns_without_memory: int
    approvals_requested: int
    denies: DenyCounts = Field(default_factory=DenyCounts)
    tool_duration: ToolTime = Field(default_factory=ToolTime)
    mean_duration_seconds: float | None
    good_ratings: int
    bad_ratings: int


class StatsBucket(StatsSummary):
    start: date


class ModelCallMismatch(ContractModel):
    thread_id: UUID
    turn_id: UUID


class StatsGetCommand(ContractModel):
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    by: StatsBucketSize = StatsBucketSize.DAY


class StatsGetResult(ContractModel):
    records: list[TurnMetrics]
    buckets: list[StatsBucket]
    unpriced_models: list[str]
    warnings: list[ModelCallMismatch] = Field(default_factory=list)


class RoutineListCommand(ContractModel):
    pass


class RoutinePayload(ContractModel):
    body: str
    content_type: Literal["application/json", "text/plain"]


class RoutineRunCommand(ContractModel):
    name: RoutineName
    payload: RoutinePayload | None = None


class RoutineRunOutcome(StrEnum):
    RUNNING = "running"
    PARKED = "parked"
    WORK = "work"
    NO_WORK = "no-work"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class RoutineLastRun(ContractModel):
    thread_id: UUID
    turn_id: UUID
    started_at: datetime
    outcome: RoutineRunOutcome
    first_line: str = ""


class SignalSummary(ContractModel):
    path: str
    auth: SignalAuth


class RoutineSummary(ContractModel):
    name: RoutineName
    description: str
    schedule: CronSchedule | None
    enabled: bool
    mode: PermissionMode
    failure_count: int = 0
    last_failure: str | None = None
    notices: list[RoutineNotice] = Field(default_factory=list)
    last_run: RoutineLastRun | None
    next_run: datetime | None
    signal: SignalSummary | None = None
    pending: int


class RoutineListResult(ContractModel):
    routines: list[RoutineSummary]
    warnings: tuple[Warning, ...]
