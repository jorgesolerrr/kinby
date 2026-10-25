"""Keep validation rules identical on both sides of the dispatcher boundary."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, NewType, Self, TypeIs
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    StringConstraints,
    field_serializer,
    model_validator,
)

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
    INSTANCE_LIFECYCLE = "instance:lifecycle"
    HUB_READ = "hub:read"
    HUB_ADMIN = "hub:admin"
    HUB_UPDATE = "hub:update"
    #: Hand a factory a work item. Only an instance holds it, on its own hub route.
    FACTORY_INTAKE = "factory:intake"
    #: Read another instance through the hub, and run the hub writes the user approved. Only the
    #: home instance holds it, on its hub route.
    HUB_HOME = "hub:home"
    #: Raise a notification for the hub to send through its channels. Every instance holds it, on
    #: its hub route.
    NOTIFICATION_RAISE = "notification:raise"


#: What a client driving one instance holds. Lifecycle is granted by the control route alone.
INSTANCE_SCOPES = frozenset(
    {
        Scope.THREAD_READ,
        Scope.THREAD_OPERATE,
        Scope.THREAD_ADMIN,
        Scope.THREAD_RATE,
        Scope.INSTANCE_READ,
        Scope.INSTANCE_ADMIN,
    }
)
CONTROL_SCOPES = INSTANCE_SCOPES | {Scope.INSTANCE_LIFECYCLE}

#: What a factory's instance holds on its hub route, with the control token the hub gave it.
INTAKE_SCOPES = frozenset({Scope.FACTORY_INTAKE})
#: What the home instance holds on its hub route: the hub's reads, and the reads of an instance
#: the hub relays to it.
HOME_SCOPES = frozenset({Scope.HUB_READ, Scope.HUB_HOME})
#: What every instance holds on its hub route (ADR 0103).
NOTIFICATION_SCOPES = frozenset({Scope.NOTIFICATION_RAISE})
#: What a client authenticated against the hub holds: a hub has one user.
HUB_SCOPES = frozenset(Scope) - INTAKE_SCOPES - NOTIFICATION_SCOPES - {Scope.HUB_HOME}
#: What the update token holds: run an instance update and follow its operation.
UPDATE_SCOPES = frozenset({Scope.HUB_UPDATE})

#: The secret a hub presents to one instance's contract server.
ControlToken = NewType("ControlToken", str)

#: The single secret the user presents to the hub's contract server.
AccessToken = NewType("AccessToken", str)

#: The secret CI presents to the hub's contract server. It holds the update scopes only.
UpdateToken = NewType("UpdateToken", str)


class ErrorCode(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    THREAD_BUSY = "THREAD_BUSY"
    INSTANCE_BUSY = "INSTANCE_BUSY"
    INSTANCE_DRAINING = "INSTANCE_DRAINING"
    TURN_OPEN = "TURN_OPEN"
    NO_ACTIVE_TURN = "NO_ACTIVE_TURN"
    PARKED_TURN_UNAVAILABLE = "PARKED_TURN_UNAVAILABLE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    MODEL_UNPRICED = "MODEL_UNPRICED"
    SNAPSHOT_UNAVAILABLE = "SNAPSHOT_UNAVAILABLE"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    #: No image preparation stored a descriptor for this selection yet.
    NOT_PREPARED = "NOT_PREPARED"
    #: Some setup values are missing or invalid. The envelope names each field.
    INVALID_SETUP = "INVALID_SETUP"
    #: The file changed since the client read it, so the write was refused. Read it again.
    STALE = "STALE"
    #: The routine has deliveries waiting to fire, so it cannot be deleted yet.
    ROUTINE_PENDING = "ROUTINE_PENDING"
    #: Raised by a client, never sent by a server: its connection dropped under a call,
    #: or never came up.
    CONNECTION_LOST = "CONNECTION_LOST"
    INTERNAL = "INTERNAL"


class ErrorEnvelope(ContractModel):
    code: ErrorCode
    message: str
    retryable: bool
    #: What is wrong with each value the client sent, by field name. INVALID_SETUP fills it, and
    #: so does INVALID_ARGUMENT for a config write.
    fields: dict[str, str] = Field(default_factory=dict)


class PermissionMode(StrEnum):
    READ_ONLY = "read-only"
    ASK = "ask"
    AUTO = "auto"
    FULL_ACCESS = "full-access"


class GateOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class GateAction(StrEnum):
    """What the gate answers for a tool call, and the rule a tool can be given in its place."""

    ALLOW = "allow"
    ASK = "ask"
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
    RUN_DELEGATED = "run.delegated"
    RUN_STOPPED = "run.stopped"
    MEMORY_RECAPPED = "memory.recapped"
    MEMORY_RECALLED = "memory.recalled"
    MEMORY_CONSOLIDATED = "memory.consolidated"
    PROFILE_COUNTED = "profile.counted"
    ROUTINE_FAILURE_HANDLED = "routine.failure.handled"
    ROUTINE_FAILURES_RESET = "routine.failures.reset"
    SIGNAL_RECEIVED = "signal.received"
    WORKSPACE_REVERTED = "workspace.reverted"
    NOTIFICATION_RAISED = "notification.raised"
    NOTIFICATION_ACCEPTED = "notification.accepted"
    NOTIFICATION_REFUSED = "notification.refused"


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
PromptVersion = NewType("PromptVersion", str)
ProfileVersion = NewType("ProfileVersion", str)
SystemPrompt = NewType("SystemPrompt", str)
# The forty-character hex id of a git tree: one workspace snapshot.
TreeId = NewType("TreeId", str)
#: A coding client's session, which a later run of the client can continue.
CodingSessionId = NewType("CodingSessionId", str)


class ChangeStatus(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


class FileChange(ContractModel):
    path: str
    status: ChangeStatus
    additions: Annotated[int, Field(ge=0)]
    deletions: Annotated[int, Field(ge=0)]


class RoutineOrigin(ContractModel):
    """A routine's run. ``dreaming_stage`` says whether the routine was a dreaming stage when
    its turn started, so a later reader never reloads a routine file that may have changed."""

    kind: Literal["routine"] = "routine"
    name: RoutineName
    trigger: RoutineTrigger
    delivery_id: DeliveryId | None = None
    dreaming_stage: bool = False


class FactoryRunOrigin(ContractModel):
    """A step of a factory run, which started the turn as its agent step."""

    kind: Literal["factory-run"] = "factory-run"
    factory: FactoryName
    run_id: UUID
    step: StepId


class HomeInstanceOrigin(ContractModel):
    """The home instance, which asked the hub to start the turn after the user approved it."""

    kind: Literal["home-instance"] = "home-instance"


Origin = Annotated[
    UserOrigin | RoutineOrigin | FactoryRunOrigin | HomeInstanceOrigin,
    Field(discriminator="kind"),
]


class TurnStarted(ContractModel):
    type: Literal[EventType.TURN_STARTED] = EventType.TURN_STARTED
    message: str
    model: str
    permission_mode: PermissionMode | None = None
    origin: Origin = Field(default_factory=UserOrigin)
    prompt_version: PromptVersion | None = None
    #: None when the instance has no profile.
    profile_version: ProfileVersion | None = None
    snapshot: TreeId | None = None


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
    #: The user's reason for a denial, when they gave one.
    reason: str | None = None


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
    snapshot: TreeId | None = None


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
    snapshot: TreeId | None = None


class TurnInterrupted(TokenTotals):
    type: Literal[EventType.TURN_INTERRUPTED] = EventType.TURN_INTERRUPTED
    input_tokens: int = 0
    output_tokens: int = 0
    snapshot: TreeId | None = None


type TurnClosingPayload = TurnCompleted | TurnFailed | TurnInterrupted


class UsageSource(StrEnum):
    API = "api"
    CLAUDE_SUBSCRIPTION = "claude-subscription"
    CHATGPT_SUBSCRIPTION = "chatgpt-subscription"


class DelegatedRunOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    LIMITED = "limited"


class DelegatedRun(TokenTotals):
    """One run of an outside agent that a tool or a client step started, with its own tokens.

    A client that reports a running total across resumes, like a Codex thread, needs its
    previous reading subtracted. Only a limited run has ``resets_at``: when its plan window resets.
    """

    usage_source: UsageSource
    client: str
    models: list[str]
    duration_ms: Annotated[int, Field(ge=0)]
    client_turns: Annotated[int, Field(ge=0)]
    outcome: DelegatedRunOutcome
    resets_at: AwareDatetime | None = None
    #: The client session whose running total the run's tokens continue, when it has one.
    session: CodingSessionId | None = None

    @model_validator(mode="after")
    def _resets_at_only_when_limited(self) -> Self:
        limited = self.outcome is DelegatedRunOutcome.LIMITED
        if limited and self.resets_at is None:
            raise ValueError("A limited run needs resets_at, the time its plan window resets.")
        if not limited and self.resets_at is not None:
            raise ValueError(f"A {self.outcome} run has no resets_at; only a limited run has one.")
        return self


class RunDelegated(ContractModel):
    """A delegated run. A client step's names its step and attempt; a tool's names neither."""

    type: Literal[EventType.RUN_DELEGATED] = EventType.RUN_DELEGATED
    run: DelegatedRun
    step: StepId | None = None
    attempt: int | None = None


class RunStopped(ContractModel):
    """A factory run this instance handed in stopped, as the hub told it."""

    type: Literal[EventType.RUN_STOPPED] = EventType.RUN_STOPPED
    stop: RunStop


class ReportedRun(ContractModel):
    """A delegated run and the time it was recorded, which places it in a time range."""

    timestamp: datetime
    run: DelegatedRun


class WorkspaceReverted(ContractModel):
    """The workspace went from ``previous`` back to ``restored``, the target's before tree."""

    type: Literal[EventType.WORKSPACE_REVERTED] = EventType.WORKSPACE_REVERTED
    target_turn_id: UUID
    previous: TreeId
    restored: TreeId


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


class MemoryRecalled(ContractModel):
    """A memory search or open returned these facts and episodes to the turn.

    A ``kinby memory search`` in a factory run's coding session is recorded on the run's thread
    instead, naming the step, the attempt and the session.
    """

    type: Literal[EventType.MEMORY_RECALLED] = EventType.MEMORY_RECALLED
    nodes: tuple[NodeId, ...]
    step: StepId | None = None
    attempt: int | None = None
    session: CodingSessionId | None = None


class MemoryConsolidated(ContractModel):
    """A consolidation finished its writes. Every episode it lists is covered."""

    type: Literal[EventType.MEMORY_CONSOLIDATED] = EventType.MEMORY_CONSOLIDATED
    episodes: tuple[NodeId, ...]
    facts: tuple[NodeId, ...]


class ProfileCounted(ContractModel):
    """The provider's tokenizer counted a profile version, once for every turn that ran with it."""

    type: Literal[EventType.PROFILE_COUNTED] = EventType.PROFILE_COUNTED
    version: ProfileVersion
    tokens: int


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


class RoutineFailuresReset(ContractModel):
    type: Literal[EventType.ROUTINE_FAILURES_RESET] = EventType.ROUTINE_FAILURES_RESET
    name: RoutineName


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


class WaitingApproval(ContractModel):
    """An approval a turn parked on, which a notification asks the user to answer."""

    turn_id: UUID
    request: ApprovalRequested


class NotificationContent(ContractModel):
    """What an instance's notification says. The hub adds its header and link."""

    #: Where it came from: the routine, the step, or the thread's title.
    source: Annotated[str, Field(min_length=1)]
    title: Annotated[str, Field(min_length=1)]
    text: str
    #: What the user can reply with.
    options: list[Annotated[str, Field(min_length=1)]] = Field(default_factory=list, max_length=4)
    #: The approval its options answer, the ones APPROVAL_OPTIONS names.
    approval: WaitingApproval | None = None


class NotificationRaised(NotificationContent):
    """A notification the instance raises on its hub route, again on each scheduler pass, until
    the hub answers it."""

    type: Literal[EventType.NOTIFICATION_RAISED] = EventType.NOTIFICATION_RAISED


class NotificationAccepted(ContractModel):
    """The hub took the notification, and sends it through its channels."""

    type: Literal[EventType.NOTIFICATION_ACCEPTED] = EventType.NOTIFICATION_ACCEPTED
    #: The sequence of the thread's notification.raised event.
    raised: int
    notification_id: UUID


class NotificationRefused(ContractModel):
    """The hub refused the notification, so it is never sent."""

    type: Literal[EventType.NOTIFICATION_REFUSED] = EventType.NOTIFICATION_REFUSED
    #: The sequence of the thread's notification.raised event.
    raised: int
    reason: str


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
    | RunDelegated
    | RunStopped
    | MemoryRecapped
    | MemoryRecalled
    | MemoryConsolidated
    | ProfileCounted
    | RoutineFailureHandled
    | RoutineFailuresReset
    | SignalReceived
    | WorkspaceReverted
    | NotificationRaised
    | NotificationAccepted
    | NotificationRefused,
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


class ThreadAskCommand(ContractModel):
    message: str


class ThreadTurnDiffCommand(ContractModel):
    thread_id: UUID
    turn_id: UUID


class ThreadTurnRevertCommand(ContractModel):
    thread_id: UUID
    turn_id: UUID


class ThreadTurnRevertPreviewCommand(ContractModel):
    thread_id: UUID
    turn_id: UUID


class ThreadTurnRevertPreviewResult(ContractModel):
    turn_id: UUID
    files: list[FileChange]


class ThreadTurnListCommand(ContractModel):
    thread_id: UUID


class ThreadTurnListResult(ContractModel):
    turn_ids: list[UUID]


class ThreadTurnTargetListCommand(ContractModel):
    thread_id: UUID


class TurnTarget(ContractModel):
    """One turn or workspace revert that a workspace operation can target."""

    turn_id: UUID
    closed: bool


class ThreadTurnTargetListResult(ContractModel):
    targets: list[TurnTarget]


class ThreadTurnDiffResult(ContractModel):
    turn_id: UUID
    before: TreeId
    after: TreeId
    files: list[FileChange]
    patch: str


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


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    DENY = "deny"


#: The options of a notification that asks for an approval, and the decision each one gives.
APPROVAL_OPTIONS = {"Approve": ApprovalDecision.APPROVE, "Deny": ApprovalDecision.DENY}


class ThreadApprovalRespondCommand(ContractModel):
    thread_id: UUID
    approval_id: UUID
    decision: ApprovalDecision
    #: What the model should do instead. Only a denial carries one.
    reason: str | None = None

    @model_validator(mode="after")
    def _reason_only_on_deny(self) -> Self:
        if self.decision is ApprovalDecision.APPROVE and self.reason is not None:
            raise ValueError("An approval takes no reason; only a denial does.")
        return self


class AcceptedResult(ContractModel):
    thread_id: UUID
    turn_id: UUID
    sequence: int


def accepted(event: Event) -> AcceptedResult:
    """The result a command returns for the event it appended."""
    return AcceptedResult(
        thread_id=event.thread_id,
        turn_id=event.turn_id,
        sequence=event.sequence,
    )


class ThreadCreateCommand(ContractModel):
    title: str | None = None


class ThreadCreateResult(ContractModel):
    id: UUID
    created_at: datetime


class ThreadFilter(StrEnum):
    #: The threads the sidebar shows: those not archived that the user started, the user
    #: started a turn in, or that failed, and any awaiting approval.
    SIDEBAR = "sidebar"
    ALL = "all"
    ARCHIVED = "archived"


class ThreadCursor(ContractModel):
    """Where a page of threads ends: its last thread's activity and id, which the list sorts by."""

    last_activity_at: datetime
    id: UUID


class ThreadListCommand(ContractModel):
    filter: ThreadFilter = ThreadFilter.SIDEBAR
    #: Only this routine's runs, within the filter.
    routine: RoutineName | None = None
    #: Where the previous page ended. The next page holds the threads that sort below it.
    cursor: ThreadCursor | None = None
    #: The most threads on a page. Without one, a single page holds every thread.
    limit: int | None = Field(default=None, ge=1)


class ThreadRenameCommand(ContractModel):
    thread_id: UUID
    #: Trimmed before it is checked, so a title of spaces is empty.
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ThreadArchiveCommand(ContractModel):
    thread_id: UUID


class ThreadUnarchiveCommand(ContractModel):
    thread_id: UUID


class ThreadStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_REPLY = "awaiting_reply"
    FAILED = "failed"


class ThreadSummary(ContractModel):
    id: UUID
    title: str | None
    created_at: datetime
    status: ThreadStatus
    #: The time of the thread's last event, or its creation time when it has none.
    last_activity_at: datetime
    #: The pinned mode, or the instance's default when none is pinned, capped at the ceiling.
    mode: PermissionMode
    mode_pinned: bool
    archived: bool
    #: Who started the thread's first turn: the user, or the routine whose run it is.
    origin: Origin


class ThreadListResult(ContractModel):
    #: The most recently active first, and by id between threads active at the same moment.
    threads: list[ThreadSummary]
    #: The highest mode the instance lets a thread pin.
    ceiling: PermissionMode
    #: What the next page starts below, or null on the last page.
    cursor: ThreadCursor | None


class IntendedState(StrEnum):
    STOPPED = "stopped"
    RUNNING = "running"
    #: The container is gone on purpose. The data and the record stay for a restoration.
    REMOVED = "removed"
    #: The owned storage is gone too. The record stays so its operations remain readable.
    DELETED = "deleted"


class ProcessState(StrEnum):
    MISSING = "missing"
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class Readiness(StrEnum):
    NOT_RUNNING = "not-running"
    STARTING = "starting"
    READY = "ready"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class StorageKind(StrEnum):
    BIND = "bind"
    VOLUME = "volume"


class StorageItem(ContractModel):
    kind: StorageKind
    source: str
    destination: str
    writable: bool


#: A factory's name, which is also its folder's name on the hub.
type FactoryName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]*$")]


#: A hub secret's name: free text, unique on the hub.
type HubSecretName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SetupFieldKind(StrEnum):
    """Where a setup field's value lands: the instance's configuration, or its secrets."""

    CONFIG = "config"
    SECRET = "secret"


class SetupFieldType(StrEnum):
    """How a client asks for a setup field's value, and so what the value is."""

    TEXT = "text"
    MULTILINE = "multiline"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    CHOICE = "choice"
    #: One address, like someone@example.com.
    EMAIL = "email"
    #: An absolute URL with a scheme and a host, like https://github.com/owner/name.git.
    URL = "url"


#: A setup value: text for text, multiline, choice, email and URL fields, a bool, or a whole number.
type SetupValue = bool | int | str


class TargetFile(StrEnum):
    """The instance file a template's configuration field lands in."""

    KINBY_TOML = "kinby.toml"


class SetupTarget(ContractModel):
    """Where a configuration value lands: a file, and a dotted key inside it."""

    file: TargetFile
    key: str


class SetupField(ContractModel):
    """One value a prepared image or an instance template asks for before an instance exists."""

    name: str
    label: str
    description: str
    kind: SetupFieldKind
    type: SetupFieldType
    required: bool
    #: What the field holds when the user sends nothing. A secret never has one.
    default: SetupValue | None = None
    #: The values a choice field offers. Only a choice field has them.
    choices: list[str] | None = None
    #: Where a template's own configuration field lands. kinby writes the built-in fields itself.
    target: SetupTarget | None = None


class SubscriptionLogin(ContractModel):
    """One sign-in a template declares, run in a setup container (ADR 0065)."""

    id: str
    label: str
    description: str
    #: What the setup container runs instead of kinby.
    command: list[str]
    #: Where the login's own named volume mounts, in the instance and in the setup container.
    volume: str
    #: Finds the URL and the one-time code in the command's output, as named groups.
    prompt_pattern: str


class DeclaredSetup(ContractModel):
    """The setup fields an instance asks for, the built-in fields first, and its logins."""

    setup_fields: list[SetupField]
    #: Descriptions stored before logins existed have none.
    logins: list[SubscriptionLogin] = Field(default_factory=list)


class ImagePrepareCommand(ContractModel):
    """Prepare kinby's base image, the one a vanilla instance runs on."""


class ImagePrepareResult(ContractModel):
    operation_id: UUID


class ImageDescribeCommand(ContractModel):
    """Read what the prepared image declares. It never builds."""


class AvatarShape(StrEnum):
    CIRCLE = "circle"
    SQUIRCLE = "squircle"
    SQUARE = "square"


class AvatarColor(StrEnum):
    """A name from a fixed palette. A client maps each name to colors of its own theme."""

    BLUE = "blue"
    VIOLET = "violet"
    GREEN = "green"
    AMBER = "amber"
    RED = "red"
    GRAY = "gray"


class Avatar(ContractModel):
    """How a client draws an instance. The hub keeps it, not the instance directory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    shape: AvatarShape
    color: AvatarColor


#: What an instance draws when nobody chose: a circle in the palette's first color.
DEFAULT_AVATAR = Avatar(shape=AvatarShape.CIRCLE, color=AvatarColor.BLUE)


class FactoryRole(ContractModel):
    """The part an instance plays in a factory, such as ``coder`` in ``software``."""

    factory: FactoryName
    #: The role's name in the factory file, which is also its instance template's.
    name: Annotated[str, Field(min_length=1)]


class InstanceCreateCommand(ContractModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    #: The instance's name. One that picks a factory role is named ``{factory}-{role}`` without it.
    manifest_id: Annotated[str, Field(min_length=1)] | None = None
    persona_name: Annotated[str, Field(min_length=1)] | None = None
    #: The role the instance plays for life, whose instance template it is written from. None
    #: creates a vanilla instance.
    factory_role: FactoryRole | None = None
    #: The value of the built-in model field. The hub checks it with the other setup values.
    model: str
    revision: Annotated[str, Field(min_length=1)] = "HEAD"
    #: Values for the configuration fields the prepared image, or the role's template, declares,
    #: by field name.
    config: dict[str, SetupValue] = Field(default_factory=dict)
    #: Values for its secret fields, and any other variables the instance should hold.
    secrets: dict[str, SecretStr] = Field(default_factory=dict)
    #: The hub secret each secret field takes and stays linked to, by field name. A field takes
    #: a typed value or a hub secret, not both.
    hub_secrets: dict[str, HubSecretName] = Field(default_factory=dict)
    #: The name each typed secret is saved under as a hub secret, by field name. The field then
    #: links it.
    save_to_hub: dict[str, HubSecretName] = Field(default_factory=dict)
    avatar: Avatar = DEFAULT_AVATAR
    #: The library skills the instance picks, copied in after its template or kinby's defaults.
    picks: list[SkillName] = Field(default_factory=list)

    @field_serializer("secrets", when_used="json")
    def serialize_secrets(self, secrets: dict[str, SecretStr]) -> dict[str, str]:
        return {name: value.get_secret_value() for name, value in secrets.items()}


class InstanceListCommand(ContractModel):
    #: List the removed instances whose records and storage the hub retains, not the active ones.
    removed: bool = False


class InstanceStartCommand(ContractModel):
    instance_id: UUID


class InstanceStatusCommand(ContractModel):
    instance_id: UUID


class InstanceStopCommand(ContractModel):
    instance_id: UUID
    force: bool = False


class InstanceRecreateCommand(ContractModel):
    instance_id: UUID


class InstanceRemoveCommand(ContractModel):
    """Drain and remove one instance's container. Its data and its record stay behind."""

    instance_id: UUID


class InstanceRestoreCommand(ContractModel):
    """Bring a removed instance back from its retained record, stopped."""

    instance_id: UUID


class InstanceDeletePreviewCommand(ContractModel):
    """Preview a removed instance's permanent deletion. It reads the retained inventory only."""

    instance_id: UUID


class InstanceDeletePreviewResult(ContractModel):
    """Exactly what a deletion removes: the removed instance's owned directories and volumes."""

    instance_id: UUID
    #: As the hub sees them, with every symlink resolved.
    directories: list[Path]
    volumes: list[str]
    #: Writable binds the hub cannot see, as Docker host paths. They stay on the host.
    retained: list[str]


class InstanceDeleteCommand(ContractModel):
    """Delete the previewed targets. A target that changed since the preview stops it."""

    instance_id: UUID
    directories: list[Path]
    volumes: list[str]


class InstanceUpdateCommand(ContractModel):
    """Move one instance to the image a revision prepares."""

    instance_id: UUID
    revision: Annotated[str, Field(min_length=1)]


class InstanceSecretsSetCommand(ContractModel):
    """Replace the named values in one instance's secrets. The result carries none of them back."""

    model_config = ConfigDict(hide_input_in_errors=True)

    instance_id: UUID
    #: Typed values, by field name. A typed value for a linked field removes its link.
    secrets: dict[str, SecretStr] = Field(default_factory=dict)
    #: The hub secret each field takes and stays linked to, by field name.
    hub_secrets: dict[str, HubSecretName] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _names_a_field(self) -> Self:
        if not self.secrets and not self.hub_secrets:
            raise ValueError("Name at least one secret to set or link.")
        return self

    @field_serializer("secrets", when_used="json")
    def serialize_secrets(self, secrets: dict[str, SecretStr]) -> dict[str, str]:
        return {name: value.get_secret_value() for name, value in secrets.items()}


class InstanceLogsCommand(ContractModel):
    instance_id: UUID
    tail: Annotated[int, Field(gt=0)] | None = None


class InstanceRelayReadCommand(ContractModel):
    """One read of an instance, which the hub makes with that instance's control token."""

    instance_id: UUID
    #: An instance method among the hub's relayed reads, such as "routine.list".
    method: str
    params: dict[str, JsonValue] = Field(default_factory=dict)


class InstanceRelayReadResult(ContractModel):
    #: What the instance answered the read with.
    result: dict[str, JsonValue]


class HubWriteCommand(ContractModel):
    """One hub tool's write, which the hub runs once it saw the user approve that exact call."""

    approval_id: UUID
    #: The hub tool the user approved, such as "instance_create".
    tool: str
    #: The call's arguments, as the user approved them.
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class HubWriteResult(ContractModel):
    #: What the hub answered the write with.
    result: dict[str, JsonValue]


class InstanceLoginStartCommand(ContractModel):
    """Run one of the instance's subscription logins in a setup container."""

    instance_id: UUID
    login_id: Annotated[str, Field(min_length=1)]


#: The version of the contract an instance speaks, reported before a lifecycle operation.
CONTRACT_VERSION = "1"


class Capability(StrEnum):
    """What an instance's contract server can do. A hub reads it before acting on the instance."""

    WS = "ws"
    DRAIN = "drain"
    #: step.run answers once per run, step and attempt, however often it is sent (ADR 0107).
    STEP_ONCE = "step_once"


class InstanceProbeCommand(ContractModel):
    pass


class RecreateReason(StrEnum):
    """A change a running instance applies only once it is recreated."""

    SECRETS = "secrets"


class InstanceProbeResult(ContractModel):
    contract_version: str
    capabilities: list[Capability]


class DrainState(StrEnum):
    """How an instance's accepted work ended when it drained."""

    DRAINED = "drained"
    INTERRUPTED = "interrupted"


class InstanceDrainCommand(ContractModel):
    force: bool = False


class InstanceDrainResult(ContractModel):
    state: DrainState


class OperationGetCommand(ContractModel):
    operation_id: UUID


class LifecycleOperationResult(ContractModel):
    operation_id: UUID
    instance_id: UUID


class RevisionBehind(ContractModel):
    """The instance runs another kinby revision than the hub's checkout, so an update is there."""

    code: Literal["revision_behind"] = "revision_behind"
    message: str
    instance_revision: str
    hub_revision: str


class TemplateBehind(ContractModel):
    """The template the instance's factory serves differs from the files the hub copied, or the
    hub kept no record of them, so an update is there."""

    code: Literal["template_behind"] = "template_behind"
    message: str


class HubRouteMissing(ContractModel):
    """The instance's environment lacks the hub route or scopes the registry grants it, or holds
    others, so an update is there."""

    code: Literal["hub_route_missing"] = "hub_route_missing"
    message: str


InstanceNotice = Annotated[
    RevisionBehind | TemplateBehind | HubRouteMissing, Field(discriminator="code")
]


class InstanceSummary(ContractModel):
    instance_id: UUID
    manifest_id: str
    persona_name: str | None
    source_revision: str
    image_id: str
    intended_state: IntendedState
    #: What the runtime saw when the list was read, as `instance.status` reports it.
    process: ProcessState
    runtime_id: str
    storage: list[StorageItem]
    avatar: Avatar
    #: A declared login is not signed in, or a required secret is not set.
    setup_pending: bool
    #: What the user may want to act on, such as an update the hub offers. None of them stops
    #: the instance.
    notices: list[InstanceNotice]
    #: The factory runs stopped for the user, or awaiting approval, at one of the instance's steps.
    needs_human: int
    #: Whether the instance registry marks it the home instance.
    home: bool
    #: The factory role the instance picked at creation, if any.
    factory_role: FactoryRole | None = None
    #: The runtime's word for the process, such as "restarting" beside `starting`.
    detail: str = ""


class InstanceListResult(ContractModel):
    instances: list[InstanceSummary]


class LoginState(StrEnum):
    """How an instance's subscription login stands: not signed in yet, or how its last one ended."""

    PENDING = "pending"
    SIGNED_IN = "signed_in"
    FAILED = "failed"


class LoginSetup(ContractModel):
    """One subscription login an instance declares, its state, and its sign-in while one runs."""

    id: str
    label: str
    description: str
    state: LoginState
    operation_id: UUID | None = None


class SecretSetup(ContractModel):
    """One secret field an instance declares, and whether it holds a value. Never the value."""

    name: str
    #: The environment variable the value lands in. The API key's has none while the instance's
    #: model cannot be read.
    variable: str | None
    label: str
    required: bool
    is_set: bool
    #: The hub secret the field is linked to, if any.
    hub_secret: HubSecretName | None = None


class InstanceSetup(ContractModel):
    """What an instance declares it needs, as its stored descriptor has it, and what is done."""

    logins: list[LoginSetup]
    secrets: list[SecretSetup]
    #: The lists of repository commands its factory role's steps name and it does not hold.
    missing_commands: list[CommandListName]

    @property
    def pending(self) -> bool:
        """A login is not signed in, a required secret is not set, or a list of repository
        commands is missing."""
        return (
            any(login.state is not LoginState.SIGNED_IN for login in self.logins)
            or any(secret.required and not secret.is_set for secret in self.secrets)
            or bool(self.missing_commands)
        )


class RepositoryCommandList(ContractModel):
    """One of an instance's lists of repository commands, its commands in the order they run."""

    name: CommandListName
    commands: list[str]


class InstanceStatusResult(ContractModel):
    instance_id: UUID
    process: ProcessState
    readiness: Readiness
    setup: InstanceSetup
    #: Whether the instance registry marks it the home instance.
    home: bool
    detail: str = ""
    #: The lifecycle operation still running, so a client that lost its response finds it again.
    active_operation_id: UUID | None = None
    #: What waits on a container recreation. A start applies them to a stopped instance.
    recreate_reasons: list[RecreateReason]
    #: The lists of repository commands it holds, when its factory role's steps name any.
    repository_commands: list[RepositoryCommandList]


class InstanceLogsResult(ContractModel):
    instance_id: UUID
    text: str


class ContainerOwner(StrEnum):
    """Who manages an existing container now, read from the labels it carries."""

    HUB = "hub"
    OTHER_HUB = "other-hub"
    COMPOSE = "compose"
    UNMANAGED = "unmanaged"


class AdoptionFindingKind(StrEnum):
    """What an adoption preflight found. Each one is a decision the operator has to take."""

    PREVIOUS_MANAGER = "previous-manager"
    LEGACY_RUNTIME = "legacy-runtime"
    UNREACHABLE = "unreachable"
    DUPLICATE_ADOPTION = "duplicate-adoption"
    STORAGE_OWNED = "storage-owned"
    RETAINED_STORAGE = "retained-storage"
    INVALID_INSTANCE = "invalid-instance"
    MANIFEST_ID_TAKEN = "manifest-id-taken"
    RETAINED_IDENTITY = "retained-identity"


class AdoptionFinding(ContractModel):
    """One observation of the preflight. A blocking finding stops the handoff."""

    kind: AdoptionFindingKind
    blocking: bool
    detail: str


class AdoptionHandoff(ContractModel):
    """What the handoff costs and what the operator does first. The hub rewrites no Compose file."""

    downtime: str
    steps: list[str]
    signals: str


class InstanceAdoptPreviewCommand(ContractModel):
    """Preview an adoption. It reads the instance and its container and changes neither."""

    #: The instance directory as the hub sees it. Its storage is read from the container.
    path: Path
    runtime_id: Annotated[str, Field(min_length=1)]
    #: The previous manager no longer recreates or updates this container.
    relinquished: bool = False
    #: A runtime that cannot drain may be interrupted. Never assumed.
    acknowledge_interrupting_stop: bool = False


class InstanceAdoptPreviewResult(ContractModel):
    """What the hub would take over, and what stands in the way of taking it."""

    instance_id: UUID
    manifest_id: str
    persona_name: str | None
    path: Path
    runtime_id: str
    image_id: str
    owner: ContainerOwner
    owner_name: str
    storage: list[StorageItem]
    capabilities: list[Capability]
    handoff: AdoptionHandoff
    findings: list[AdoptionFinding]


class InstanceAdoptCommand(InstanceAdoptPreviewCommand):
    """Take ownership. The preflight runs again here, and a blocking finding stops it."""

    #: Move the public signal path to this instance, so a webhook keeps the URL it was given.
    claim_signals: bool = False


class OperationKind(StrEnum):
    CREATE = "create"
    START = "start"
    STOP = "stop"
    SECRETS = "secrets"
    RECREATE = "recreate"
    ADOPT = "adopt"
    UPDATE = "update"
    REMOVE = "remove"
    RESTORE = "restore"
    DELETE = "delete"
    #: An image preparation. It belongs to no instance.
    PREPARE = "prepare"
    #: A subscription login. It runs beside the instance's other operations, not after them.
    LOGIN = "login"
    #: Setting a hub secret. It belongs to no instance.
    HUB_SECRET = "hub-secret"


class OperationState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class LoginPrompt(ContractModel):
    """Where the user finishes a subscription login, and the one-time code they enter there."""

    url: str
    code: str


class OperationStep(ContractModel):
    """One named stage of a lifecycle operation, in the order the hub ran it."""

    name: str
    state: OperationState
    detail: str
    #: What a login's sign-in step found in the setup container's output.
    prompt: LoginPrompt | None = None


class OperationGetResult(ContractModel):
    operation_id: UUID
    #: None for an operation that belongs to no instance, such as an image preparation.
    instance_id: UUID | None
    kind: OperationKind
    state: OperationState
    detail: str
    steps: list[OperationStep] = Field(default_factory=list)
    #: The instance that asked the hub for the operation. None for one the user started.
    requester: UUID | None = None


class UsageGetCommand(ContractModel):
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None


class TurnUsage(TokenTotals):
    turn_id: UUID
    recap_input_tokens: int
    recap_output_tokens: int
    delegated_runs: list[ReportedRun] = Field(default_factory=list)


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


class Navigation(ContractModel):
    read_calls: int = 0
    reads_before_first_write: int = 0
    write_calls: int = 0
    duration_ms: int = 0
    distinct_paths: int = 0
    repeat_opens: int = 0
    tokens_before_first_write: int = 0


class NavigationMeans(ContractModel):
    turns: int = 0
    read_calls: float | None = None
    duration_ms: float | None = None
    tokens_before_first_write: float | None = None
    repeat_opens: float | None = None


class TurnMetrics(TokenTotals):
    thread_id: UUID
    turn_id: UUID
    model: str | None
    prompt_version: PromptVersion | None
    profile_version: ProfileVersion | None = None
    origin: Origin | None
    closing_kind: TurnClosingKind
    #: Whether a completed turn did work. Always work for a failed or interrupted turn.
    outcome: CompletionOutcome = CompletionOutcome.WORK
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
    #: The tokens memory tool results added to the turn's context, each counted once.
    memory_tokens: float
    #: Memory's tokens times the model calls that read them again from the cache.
    memory_carried_tokens: float = 0
    #: The carried tokens at the turn model's cache-read rate. None when the model is unpriced.
    memory_carried_cost: float | None = None
    #: Whether memory tokens come from the provider's counts. A turn that returned memory but
    #: recorded no model calls has none to count, and an older core estimated them from characters.
    memory_measured: bool = False
    rating: TurnRated | None
    navigation: Navigation = Field(default_factory=Navigation)
    delegated_runs: list[ReportedRun] = Field(default_factory=list)
    #: The facts and episodes the turn's memory searches and opens returned, each listed once.
    recalled: list[NodeId] = Field(default_factory=list)


class SubscriptionUse(TokenTotals):
    """The delegated runs one subscription usage source paid for, counted and never priced."""

    usage_source: UsageSource
    runs: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0


class SourceRuns(ContractModel):
    """The delegated runs one subscription usage source made."""

    usage_source: UsageSource
    runs: int


class OriginUse(ContractModel):
    """The turns chat or one routine started, and what they cost and ran.

    A routine is keyed by the name its turns recorded, so a renamed routine has two rows.
    """

    origin: Literal["user", "routine"]
    #: None for chat.
    routine: RoutineName | None
    #: Closed turns other than no-work completions.
    turns: int
    no_work: int
    failed: int
    #: None only when no turn in the row was priced.
    cost: float | None
    runs: list[SourceRuns]


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
    memory_tokens: float = 0
    memory_carried_tokens: float = 0
    #: None only when no turn's carried tokens were priced.
    memory_carried_cost: float | None = None
    #: Turns whose memory tokens were not measured, so a range with some does not compare.
    memory_estimated_turns: int = 0
    approvals_requested: int
    denies: DenyCounts = Field(default_factory=DenyCounts)
    tool_duration: ToolTime = Field(default_factory=ToolTime)
    mean_duration_seconds: float | None
    good_ratings: int
    bad_ratings: int
    navigation: NavigationMeans = Field(default_factory=NavigationMeans)
    subscriptions: list[SubscriptionUse]
    #: Chat first, then each routine by name.
    origins: list[OriginUse]


class StatsBucket(StatsSummary):
    start: date


class ModelCallMismatch(ContractModel):
    thread_id: UUID
    turn_id: UUID


class StatsGetCommand(ContractModel):
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    by: StatsBucketSize = StatsBucketSize.DAY


class PlanUse(ContractModel):
    """The runs a subscription usage source made in one plan window, up to now."""

    usage_source: UsageSource
    duration_seconds: int
    runs: int


class PlanLimit(ContractModel):
    """A subscription usage source whose plan stopped a delegated run, until its window resets."""

    usage_source: UsageSource
    resets_at: AwareDatetime


class ApiUse(TokenTotals):
    """The model calls the API usage source paid for, priced where the model is known."""

    cost: float | None = None


class RunTokens(ContractModel):
    """One factory run's tokens in this instance: its agent steps' turns and its recap, priced,
    and its client steps' delegated runs, counted per subscription usage source."""

    run_id: UUID
    api: ApiUse
    subscriptions: list[SubscriptionUse]
    #: Whether an agent step's turn or a coding session recalled a fact for the run here.
    recalled_fact: bool = False


class RunTotals(ContractModel):
    """Factory runs with tokens in a range, their input and output tokens across every usage
    source, and their API cost."""

    runs: int
    tokens: int
    cost: float | None


class StatsGetResult(ContractModel):
    records: list[TurnMetrics]
    buckets: list[StatsBucket]
    total: StatsSummary
    #: Each subscription's runs in its 5-hour and 7-day windows, whatever the range.
    plan_use: list[PlanUse]
    limits: list[PlanLimit]
    unpriced_models: list[str]
    warnings: list[ModelCallMismatch] = Field(default_factory=list)
    #: Each factory run with tokens in the range. None from a core older than run usage.
    runs: list[RunTokens] | None = None


class StatsSummaryResult(ContractModel):
    """Usage across the hub's running instances, read live from each one and stored nowhere.

    A total leaves out every instance in ``skipped``, ``unreachable``, or ``outdated``.
    """

    #: Each counted instance's buckets, by hub instance ID.
    buckets: dict[UUID, list[StatsBucket]]
    api: ApiUse
    subscriptions: list[SubscriptionUse]
    #: Each subscription's runs per plan window, summed across instances.
    plan_use: list[PlanUse]
    #: The latest reset per subscription usage source, across instances.
    limits: list[PlanLimit]
    #: Each counted instance's factory runs, by hub instance ID.
    runs: dict[UUID, RunTotals]
    #: A run whose steps ran in more than one instance counts once.
    run_total: RunTotals
    #: Instances not running, so not asked.
    skipped: list[UUID]
    #: Running instances that failed to answer, or did not answer in time.
    unreachable: list[UUID]
    #: Running instances on an older core, whose answer this hub cannot read.
    outdated: list[UUID]


class StatsRunsCommand(ContractModel):
    """The run usage of the factory runs one instance handed in, created in the range."""

    instance_id: UUID
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None


class StepAttempts(ContractModel):
    step: StepId
    attempts: int


class RunUsage(ContractModel):
    """One factory run's cost and rework, read from the hub and its instances, stored nowhere."""

    run: FactoryRun
    #: Input and output tokens across every usage source.
    tokens: int
    api: ApiUse
    subscriptions: list[SubscriptionUse]
    #: In the order the run first reached each step.
    attempts: list[StepAttempts]
    #: Failed attempts tried again, and attempts that sent the work back, by the tally rules.
    retries: int
    send_backs: int
    #: From creation to done or cancelled. None until the run ends.
    duration_seconds: float | None
    #: Whether the run recalled a fact in any instance.
    recalled_fact: bool


class RunWeek(ContractModel):
    """The medians of the runs created in one UTC week, which starts on Monday."""

    start: date
    runs: int
    tokens: float
    attempts: float
    #: None when no run of the week has ended.
    duration_seconds: float | None


class StatsRunsResult(ContractModel):
    """Tokens leave out every instance in ``skipped``, ``unreachable``, or ``outdated``."""

    #: Oldest first.
    runs: list[RunUsage]
    #: Oldest first, only weeks with runs.
    weeks: list[RunWeek]
    skipped: list[UUID]
    unreachable: list[UUID]
    outdated: list[UUID]


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


#: The sha256 hex of a config file's bytes on disk. A missing file hashes as empty bytes.
FileHash = NewType("FileHash", str)
#: A config file's path in the instance directory, such as ``SYSTEM.md`` or ``routines/news``.
ConfigFile = NewType("ConfigFile", str)


class PromptName(StrEnum):
    BEHAVIOR = "behavior"
    RECAP = "recap"


class PromptGetCommand(ContractModel):
    name: PromptName


class PromptSetCommand(ContractModel):
    name: PromptName
    content: str
    #: The hash the client read. The write is refused as STALE when the file changed since.
    hash: FileHash


class PromptResult(ContractModel):
    content: str
    hash: FileHash
    #: True when the instance has no file and ``content`` is the text kinby ships.
    default: bool


#: A regex the gate matches against a Bash command.
BashPattern = NewType("BashPattern", str)


class DenyPattern(ContractModel):
    pattern: BashPattern
    #: True for a pattern kinby ships. It always applies, and a client shows it locked.
    shipped: bool


class BashPermissions(ContractModel):
    #: The shipped deny patterns first, then the instance's own.
    deny: list[DenyPattern]
    ask: list[BashPattern]


class OwnBashPatterns(ContractModel):
    #: Only the instance's own deny patterns. The shipped ones apply without being sent.
    deny: list[BashPattern]
    ask: list[BashPattern]


class PermissionsGetCommand(ContractModel):
    pass


class PermissionsSetCommand(ContractModel):
    mode: PermissionMode
    ceiling: PermissionMode
    #: The tools with a rule of their own. Every other tool follows the mode.
    tools: dict[str, GateAction]
    bash: OwnBashPatterns
    #: The hash the client read. The write is refused as STALE when the file changed since.
    hash: FileHash


class PermissionsResult(ContractModel):
    #: The mode a new thread starts in.
    mode: PermissionMode
    ceiling: PermissionMode
    #: The tools with a rule of their own. Every other tool follows the mode.
    tools: dict[str, GateAction]
    bash: BashPermissions
    hash: FileHash


class ConfigActor(StrEnum):
    APP = "app"
    AGENT = "agent"
    FAILURE_POLICY = "failure_policy"


class ConfigChange(ContractModel):
    """One recorded edit to the instance's configuration."""

    at: AwareDatetime
    file: ConfigFile
    actor: ConfigActor
    #: The thread and turn the change was made in, when it was made in one.
    thread_id: UUID | None = None
    turn_id: UUID | None = None
    #: A unified diff from the content before the change to the content after it.
    diff: str
    #: The hash of what the change left on disk, as the file's read returns it. Null when the
    #: change left nothing there, or when the log predates the field.
    hash: FileHash | None = None


class ConfigHistoryCommand(ContractModel):
    #: Only the changes to this file. Every file's changes when absent.
    file: ConfigFile | None = None
    limit: int = Field(ge=1)


class ConfigHistoryResult(ContractModel):
    #: Newest first.
    changes: list[ConfigChange]


class RoutineReadCommand(ContractModel):
    name: RoutineName


class RoutineFile(ContractModel):
    name: RoutineName
    #: The routine's ``ROUTINE.md``.
    content: str
    #: The sha256 over the sorted (relative path, bytes) pairs of the routine's directory.
    hash: FileHash


class RoutineWriteCommand(ContractModel):
    name: RoutineName
    content: str
    #: The hash the client read, or null to create a routine that does not exist yet.
    hash: FileHash | None


class RoutineSetEnabledCommand(ContractModel):
    name: RoutineName
    enabled: bool


class RoutineDeleteCommand(ContractModel):
    name: RoutineName
    hash: FileHash


class RoutineDeleteResult(ContractModel):
    pass


class RoutineRenameCommand(ContractModel):
    name: RoutineName
    #: The routine's new name, and so its new directory and signal path.
    new_name: RoutineName
    hash: FileHash


class RecapPolicy(StrEnum):
    """When kinby writes a model-assisted recap."""

    EVERY_TURN = "every-turn"
    TRACE_ONLY = "off"


class FeedbackPolicy(StrEnum):
    """When kinby asks the user to rate a completed turn."""

    EVERY_TURN = "every-turn"
    OFF = "off"


class ManifestModels(ContractModel):
    #: Each model as ``provider:model``. The recap model is the main one when absent.
    main: str
    recap: str | None
    embed: str | None


class ManifestBudgets(ContractModel):
    #: Each limit is off when absent.
    steps: int | None
    tokens: int | None
    seconds: float | None
    usd_per_day: float | None


class ManifestRoutines(ContractModel):
    #: The IANA time zone routine schedules read in.
    timezone: str


class ManifestTools(ContractModel):
    #: Whether kinby's default tools, such as bash and the file tools, are on.
    defaults: bool
    bash_timeout_seconds: int


class ManifestMemory(ContractModel):
    recap: RecapPolicy


class ManifestFeedback(ContractModel):
    ask: FeedbackPolicy


class ManifestValues(ContractModel):
    """The fields of ``kinby.toml`` a client may change. The hub owns the rest."""

    persona_name: str | None
    models: ManifestModels
    budgets: ManifestBudgets
    routines: ManifestRoutines
    tools: ManifestTools
    memory: ManifestMemory
    feedback: ManifestFeedback


class PriceSource(StrEnum):
    SHIPPED = "shipped"
    MANIFEST = "manifest"


class ModelChoice(ContractModel):
    """A model the budget can count, because kinby ships its price or the manifest sets one."""

    model: str
    priced_from: PriceSource
    #: Whether ``<PROVIDER>_API_KEY`` is set in the instance's environment.
    key_set: bool


class ManifestGetCommand(ContractModel):
    pass


class ManifestResult(ContractModel):
    values: ManifestValues
    model_choices: list[ModelChoice]
    hash: FileHash


class NewModelPrice(ContractModel):
    #: Dollars per million tokens.
    input: float
    output: float


class ManifestSetCommand(ContractModel):
    values: ManifestValues
    #: Prices to write as ``[prices."provider:model"]`` entries, by model.
    prices: dict[str, NewModelPrice] = Field(default_factory=dict)
    #: The hash the client read. The write is refused as STALE when the file changed since.
    hash: FileHash


SkillName = NewType("SkillName", str)


class SkillTier(StrEnum):
    """Where a skill comes from, in the order the model looks for it."""

    INSTANCE = "instance"
    KINBY = "kinby"
    WORKSPACE = "workspace"


class SkillListCommand(ContractModel):
    pass


class SkillSummary(ContractModel):
    name: SkillName
    tier: SkillTier
    description: str
    #: The tier of the skill that hides this one, when a higher tier has one by this name.
    shadowed_by: SkillTier | None


class SkillListResult(ContractModel):
    #: By name, and each name's skills in tier order, so the one the model reads comes first.
    skills: list[SkillSummary]
    warnings: tuple[Warning, ...]


class SkillReadCommand(ContractModel):
    name: SkillName
    tier: SkillTier


class SkillResult(ContractModel):
    #: The skill's ``SKILL.md``.
    content: str
    #: The skill directory's other files, by their path in it.
    files: list[str]
    #: The hash of the skill directory: every file's path and bytes.
    hash: FileHash


class SkillWriteCommand(ContractModel):
    """Write the instance copy's ``SKILL.md``, creating the skill when ``hash`` is null."""

    name: SkillName
    content: str
    hash: FileHash | None


class SkillCustomizeCommand(ContractModel):
    """Copy the skill the model reads into the instance, where it can be edited."""

    name: SkillName


class SkillDeleteCommand(ContractModel):
    """Delete the instance copy, so the skill it hid, if any, applies again."""

    name: SkillName
    hash: FileHash


class ToolRule(StrEnum):
    """What the gate does with a tool: its own rule in permissions.toml, or the mode's."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"
    MODE = "mode"


class ToolListCommand(ContractModel):
    pass


class ToolSummary(ContractModel):
    name: str
    #: ``core``, the distribution and version that ship it, or the instance file that defines it.
    source: str
    write: bool
    rule: ToolRule


class ToolListResult(ContractModel):
    #: By name.
    tools: list[ToolSummary]
    warnings: tuple[Warning, ...]


class NodeKind(StrEnum):
    FACT = "fact"
    EPISODE = "episode"


class NodeSource(StrEnum):
    """Where a knowledge graph node came from. The instance derives it from the node's file."""

    #: A fact the agent remembered in a thread.
    AGENT = "agent"
    #: An episode the recap wrote after a turn.
    RECAP = "recap"
    #: A fact the user added from a client.
    USER = "user"


class MemoryListCommand(ContractModel):
    #: Every term is a case-insensitive substring of the description or a subject, as in recall.
    #: Empty matches every node.
    query: str = ""
    kind: NodeKind | None = None
    #: One subject, matched exactly and case-insensitively.
    subject: str | None = None
    #: Inclusive.
    after: date | None = None
    #: Inclusive.
    before: date | None = None
    #: The last node of the previous page. The next page holds the matches that sort below it.
    cursor: NodeId | None = None
    limit: int = Field(default=50, ge=1, le=200)


class NodeSummary(ContractModel):
    node: NodeId
    kind: NodeKind
    date: date
    description: str
    subjects: list[str]
    source: NodeSource


class MemoryListResult(ContractModel):
    #: Newest first, by date and then node.
    items: list[NodeSummary]
    #: What the next page starts below, or null on the last page.
    cursor: NodeId | None


class MemoryOpenCommand(ContractModel):
    node: NodeId


class MemoryOpenResult(NodeSummary):
    body: str
    #: The thread the node was learned or recapped in. Null for a fact the user added, and for
    #: an episode of a factory run.
    thread: UUID | None
    #: The recapped turn. Null for a fact, and for an episode of a factory run.
    turn: UUID | None
    #: The recapped factory run. Null for a fact, and for an episode of a turn.
    run: UUID | None
    #: The episode's tool path. Null for a fact.
    tools: list[str] | None


class MemoryAddCommand(ContractModel):
    #: Must not be blank. Leading and trailing whitespace is dropped.
    description: str
    subjects: list[str]
    body: str


class MemoryCorrectCommand(MemoryAddCommand):
    #: The fact the correction replaces. It is tombstoned once the new fact is written.
    node: NodeId


class MemoryWriteResult(ContractModel):
    #: The fact written.
    node: NodeId


class MemoryForgetCommand(ContractModel):
    node: NodeId


class MemoryForgetResult(ContractModel):
    pass


class ProfileGetCommand(ContractModel):
    pass


class ProfileSetCommand(ContractModel):
    text: str
    #: The hash the client read. The write is refused as STALE when the file changed since.
    hash: FileHash


class ProfileResult(ContractModel):
    #: ``memory/profile.md``, which goes into every prompt. Empty when there is no file.
    text: str
    hash: FileHash
    #: About how many tokens the text takes in a prompt: a quarter of its characters, rounded up.
    tokens: int


class FactorySource(StrEnum):
    """Where the factory the hub serves under a name comes from."""

    #: The hub's own factories directory: one written over the contract, or a customized copy.
    HUB = "hub"
    #: kinby's source. Read-only on the hub; an edit copies it into the hub's factories.
    SHIPPED = "shipped"


class FactoryListCommand(ContractModel):
    pass


class FactorySummary(ContractModel):
    name: FactoryName
    source: FactorySource


class FactoryListResult(ContractModel):
    #: By name. A hub factory hides the shipped factory it customizes.
    factories: list[FactorySummary]


class FactoryGetCommand(ContractModel):
    name: FactoryName


class FactoryResult(ContractModel):
    name: FactoryName
    source: FactorySource
    #: Every file of the factory's folder, by its path in the folder.
    files: dict[str, str]
    #: The hash of the factory's folder: every file's path and bytes.
    hash: FileHash


class FactoryCheckCommand(ContractModel):
    name: FactoryName


class FactoryCheckResult(ContractModel):
    #: One message per problem the factory check found. Empty when the factory passes.
    problems: list[str]


class FactoryEditCommand(ContractModel):
    """Write files of a factory's folder at once. The hub keeps them only if the check passes.

    A shipped factory is never written: the edit lands on its copy in the hub's factories.
    """

    name: FactoryName
    #: The content of each file to write, by its path in the factory's folder.
    files: dict[str, str]
    #: The hash the client read, or null to create a factory no source has yet.
    hash: FileHash | None


class FactoryDescribeCommand(ContractModel):
    name: FactoryName


class FactoryDescription(ContractModel):
    #: What each instance template asks for, by the instance's name in the factory.
    instances: dict[str, DeclaredSetup]
    #: The library skills each role suggests, which a client preselects, by the role's name.
    suggested_picks: dict[str, list[SkillName]]


class LibraryKind(StrEnum):
    FACTORY = "factory"
    SKILL = "skill"


class LibrarySource(StrEnum):
    """Where a library entry came from."""

    #: kinby ships it, and the hub copied or serves it.
    SHIPPED = "shipped"
    #: The user wrote it on the hub.
    WRITTEN = "written"
    #: The user added it from a git repository, at a commit.
    GIT = "git"


#: A full commit hash.
type GitCommit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class GitSource(ContractModel):
    """Where in which repository a library entry from git came from, as a Claude Code marketplace
    entry's git source records it."""

    url: str
    #: The entry's folder in the repository, ``.`` for its root.
    path: str
    #: The branch or tag the address named. None is the repository's default branch.
    ref: str | None
    commit: GitCommit


class LibraryListCommand(ContractModel):
    pass


class LibraryEntry(ContractModel):
    kind: LibraryKind
    name: str
    #: A skill's, from its ``SKILL.md``. A factory declares none.
    description: str | None
    source: LibrarySource
    #: Where an entry from git came from. None for any other entry.
    git: GitSource | None = None
    #: A shipped or git skill the user changed since it landed. A written skill is the user's own,
    #: and never counts as edited.
    edited: bool = False
    #: A shipped skill this kinby ships a newer version of. A git skill's newer commit shows only
    #: on a check, which reads its remote.
    newer: bool = False


class LibraryListResult(ContractModel):
    #: By kind, then by name.
    entries: list[LibraryEntry]


class LibrarySkillReadCommand(ContractModel):
    name: SkillName


class LibrarySkillWriteCommand(ContractModel):
    """Write a library skill's ``SKILL.md``, creating the skill when ``hash`` is null."""

    name: SkillName
    content: str
    hash: FileHash | None


class LibrarySkillDeleteCommand(ContractModel):
    """Delete a skill from the library. Every instance that picked it keeps its copy."""

    name: SkillName
    hash: FileHash


class LibrarySkillCheckCommand(ContractModel):
    """Look for a newer version of a shipped or git skill, and change nothing. A git skill's ref
    is read from its remote."""

    name: SkillName


class NewerVersion(ContractModel):
    #: The commit a git skill's ref names now. None for a shipped skill, whose newer version is
    #: the one this kinby ships.
    commit: GitCommit | None
    #: The library folder's hash, which the diff starts from.
    hash: FileHash
    #: From the library's files to the newer version's, as a unified diff.
    diff: str


class LibrarySkillCheckResult(ContractModel):
    #: None when the skill is current, or was written on the hub.
    newer: NewerVersion | None


class LibrarySkillMoveCommand(ContractModel):
    """Move a library skill to the newer version a check showed. No instance changes."""

    name: SkillName
    #: The hash the check showed. STALE when the folder changed since.
    hash: FileHash
    #: The commit the check showed for a git skill, which lands even when the ref has moved
    #: since. None for a shipped skill.
    commit: GitCommit | None
    #: Replace a skill the user edited since it landed. Without it, the hub refuses one.
    overwrite: bool = False


class LibraryGitPreviewCommand(ContractModel):
    """Show the skills a git repository holds, and change nothing."""

    #: In the ``skills`` CLI's syntax: ``owner/repo``, ``owner/repo/path`` or a URL, each with an
    #: optional ``#ref``.
    address: str


class PreviewedSkill(ContractModel):
    name: SkillName
    #: Its folder in the repository, ``.`` for the root.
    path: str
    #: Its ``SKILL.md``.
    content: str
    #: Its other files, by their path in it.
    files: list[str]


class LibraryGitPreviewResult(ContractModel):
    url: str
    #: None is the repository's default branch.
    ref: str | None
    #: What the ref resolved to.
    commit: GitCommit
    #: By name.
    skills: list[PreviewedSkill]


class LibraryGitAddCommand(ContractModel):
    """Add skills a preview showed to the library."""

    address: str
    #: The commit the preview resolved. It lands even when the ref has moved since.
    commit: GitCommit
    skills: list[SkillName] = Field(min_length=1)


class LibraryGitAddResult(ContractModel):
    #: The entries added, by name.
    entries: list[LibraryEntry]


class InstancePicksGetCommand(ContractModel):
    instance_id: UUID


class InstancePicksSetCommand(ContractModel):
    instance_id: UUID
    #: Every library skill the instance should hold. A pick left out is dropped.
    skills: list[SkillName]
    #: The lock's hash the client read. The set is refused as STALE when the picks changed since.
    hash: FileHash | None


class PickState(StrEnum):
    """How a pick stands against its lock entry and the library."""

    UP_TO_DATE = "up-to-date"
    #: The library's entry changed since the instance's copy was taken from it.
    BEHIND = "behind"
    #: The instance's copy no longer matches its lock entry, so an update keeps it.
    EDITED = "edited"


class SkillPick(ContractModel):
    name: SkillName
    #: Where the library entry it was copied from came from.
    source: LibrarySource
    state: PickState


class InstancePicksResult(ContractModel):
    #: By name. A pick whose folder the instance deleted is no pick.
    picks: list[SkillPick]
    #: The hash of the instance's ``skills-lock.json``. Null when it has none.
    hash: FileHash | None


class HubSecretListCommand(ContractModel):
    pass


class HubSecretLink(ContractModel):
    """An instance's secret field that takes a hub secret."""

    instance_id: UUID
    manifest_id: str
    #: The secret setup field, by name.
    field: str


class HubSecret(ContractModel):
    """A hub secret as the hub shows it: by name, never its value."""

    name: HubSecretName
    #: In instance creation order.
    links: list[HubSecretLink] = Field(default_factory=list)


class HubSecretListResult(ContractModel):
    #: By name.
    secrets: list[HubSecret]


class HubSecretSetCommand(ContractModel):
    """Create or replace one hub secret. The result carries no value back."""

    model_config = ConfigDict(hide_input_in_errors=True)

    name: HubSecretName
    value: Annotated[SecretStr, Field(min_length=1)]
    #: The linked running instances to recreate once their secrets hold the new value.
    recreate: list[UUID] = Field(default_factory=list)

    @field_serializer("value", when_used="json")
    def serialize_value(self, value: SecretStr) -> str:
        return value.get_secret_value()


class HubSecretSetResult(ContractModel):
    operation_id: UUID


class HubSecretDeleteCommand(ContractModel):
    name: HubSecretName


class HubSecretDeleteResult(ContractModel):
    #: Each instance's field keeps the value it held.
    unlinked: list[HubSecretLink]


#: A channel kind, by its entry point's name in the ``kinby.channels`` group.
type ChannelKindName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ChannelKindsCommand(ContractModel):
    pass


class ChannelKindDescription(ContractModel):
    """A channel kind the hub found, and what a channel of it asks for."""

    kind: ChannelKindName
    #: The value of a secret field is kept as a hub secret.
    settings: list[SetupField]
    #: A channel of it sends nothing until the user sends it the pairing code.
    pairs: bool


class ChannelKindsResult(ContractModel):
    #: By name.
    kinds: list[ChannelKindDescription]


class ChannelFailure(ContractModel):
    """The last send through a channel that failed."""

    at: datetime
    error: str


class Channel(ContractModel):
    """A channel as the hub shows it: never the value of a secret setting."""

    channel_id: UUID
    name: str
    kind: ChannelKindName
    #: Each setting, by field name. A secret field holds the name of the hub secret with its value.
    settings: dict[str, SetupValue]
    enabled: bool
    paired: bool
    #: What the user sends through the channel to pair it, until it is paired.
    pairing_code: str | None = None
    pairing_expires_at: datetime | None = None
    #: The instances whose notifications it does not send.
    muted: list[UUID] = Field(default_factory=list)
    last_sent_at: datetime | None = None
    last_failure: ChannelFailure | None = None


class ChannelListCommand(ContractModel):
    pass


class ChannelListResult(ContractModel):
    #: In creation order.
    channels: list[Channel]


class ChannelCreateCommand(ContractModel):
    """Set up a channel of one kind. The hub keeps its secret settings as hub secrets."""

    model_config = ConfigDict(hide_input_in_errors=True)

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    kind: ChannelKindName
    #: The kind's configuration settings, by field name.
    settings: dict[str, SetupValue] = Field(default_factory=dict)
    #: The kind's secret settings, by field name.
    secrets: dict[str, SecretStr] = Field(default_factory=dict)

    @field_serializer("secrets", when_used="json")
    def serialize_secrets(self, secrets: dict[str, SecretStr]) -> dict[str, str]:
        return {name: value.get_secret_value() for name, value in secrets.items()}


class ChannelUpdateCommand(ContractModel):
    """Change what the command sends of one channel, and keep the rest."""

    model_config = ConfigDict(hide_input_in_errors=True)

    channel_id: UUID
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None = None
    enabled: bool | None = None
    settings: dict[str, SetupValue] = Field(default_factory=dict)
    #: Each one replaces the value of the hub secret that holds it.
    secrets: dict[str, SecretStr] = Field(default_factory=dict)

    @field_serializer("secrets", when_used="json")
    def serialize_secrets(self, secrets: dict[str, SecretStr]) -> dict[str, str]:
        return {name: value.get_secret_value() for name, value in secrets.items()}


class ChannelDeleteCommand(ContractModel):
    """Delete a channel, with its hub secrets and the notifications it has still to send."""

    channel_id: UUID


class ChannelTestCommand(ContractModel):
    """Send a test notification through a paired channel now."""

    channel_id: UUID


class ChannelMuteCommand(ContractModel):
    channel_id: UUID
    instance_id: UUID


class NotificationRaiseCommand(NotificationContent):
    """A notification an instance raises on its hub route. The hub adds its header and link."""

    #: Unique among the instance's notifications. Raising a key again returns its notification.
    key: Annotated[str, Field(min_length=1)]
    #: The thread it came from, which its link opens.
    thread_id: UUID


class NotificationRaiseResult(ContractModel):
    notification_id: UUID


class DeliveryStatus(StrEnum):
    """Where one notification stands on one channel."""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    #: The channel mutes the notification's instance, so it is never sent.
    MUTED = "muted"


class NotificationDelivery(ContractModel):
    channel_id: UUID
    status: DeliveryStatus


class ReplyStatus(StrEnum):
    """What came of a reply."""

    #: Recorded, and not applied yet.
    RECEIVED = "received"
    APPLIED = "applied"
    #: It answered something already settled, so it changed nothing.
    STALE = "stale"
    FAILED = "failed"


class NotificationReply(ContractModel):
    #: One of the notification's options. None for "Mute this instance".
    option: str | None
    #: The channel it came through. None for a reply from the web app.
    channel_id: UUID | None
    received_at: datetime
    status: ReplyStatus
    #: What came of it, as the channel's message shows it once edited. None until applied.
    outcome: str | None = None


class Notification(ContractModel):
    """A notification an instance raised, where it was delivered, and how the user replied."""

    notification_id: UUID
    instance_id: UUID
    #: None for a notification kinby raised itself, such as an instance notice.
    thread_id: UUID | None
    #: The persona name and where the notification came from.
    header: str
    title: str
    text: str
    #: The page of the web app it came from. None while the hub has no public URL.
    link: str | None
    options: list[str]
    raised_at: datetime
    deliveries: list[NotificationDelivery]
    #: Oldest first.
    replies: list[NotificationReply]


class NotificationListCommand(ContractModel):
    """The notifications of one instance or one thread, or every one without either."""

    instance_id: UUID | None = None
    thread_id: UUID | None = None


class NotificationListResult(ContractModel):
    #: Oldest first.
    notifications: list[Notification]


class NotificationReplyCommand(ContractModel):
    """Answer a notification with one of its options, as a reply through a channel would."""

    notification_id: UUID
    option: str


type StepId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")]
#: The name of a value a work item carries or a step result declares, such as ``branch``.
type ValueName = Annotated[str, Field(pattern=r"^[a-z_][a-z0-9_]*$")]
#: One value of a work item or of a step result.
type StepValue = bool | int | str
#: An instance tool, by its function's name.
type ToolName = Annotated[str, Field(min_length=1)]
#: An instance hook, by its function's name.
type HookName = Annotated[str, Field(min_length=1)]


class CodingClient(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"


#: A model a coding client runs, by a name or alias the client accepts, such as
#: ``claude-opus-5-5``.
type CodingModel = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/\[\]-]*$")]


class ReasoningEffort(StrEnum):
    """How hard a coding client reasons. Codex has no ``max``."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class FactoryRunStatus(StrEnum):
    """Where a factory run stands."""

    #: Its current step runs in the step's instance.
    RUNNING = "running"
    #: Its current step waits for the step's instance, which runs one step at a time.
    QUEUED = "queued"
    #: It waits on a signal or on the user's approval, and holds no instance.
    PARKED = "parked"
    #: It stopped for the user, keeping everything done so far.
    NEEDS_HUMAN = "needs-human"
    DONE = "done"
    CANCELLED = "cancelled"


class StepEnding(StrEnum):
    """How one attempt at a step ended."""

    #: The step did its work. The result's outcome says what the run does next.
    CLEAN = "clean"
    #: The step failed. The run tries it again under its ``retry``.
    FAILED = "failed"
    #: The step ran past its ``timeout``. The run never tries it again.
    TIMED_OUT = "timed-out"
    #: The hub stopped while the attempt ran, which only a hub before ADR 0102 recorded. It
    #: counts as failed.
    INTERRUPTED = "interrupted"
    #: The step cannot run until the user supplies what it lacks. The run stops for them, and
    #: never tries the step again or sends its work back.
    NEEDS_HUMAN = "needs-human"
    #: The subscription's limit stopped the step's coding client. The run parks until the plan
    #: window resets, then tries the step again without spending a retry.
    LIMITED = "limited"


#: One of an instance's lists of repository commands, by name.
type CommandListName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")]


class RepositoryCommands(ContractModel):
    """The instance's list of repository commands named *commands*, in a command step's run.

    The instance reads the list when the step runs. Its commands run as written, with no run
    values.
    """

    commands: CommandListName


class CommandStepRun(ContractModel):
    """Run each command in the instance's workspace without a shell, in order.

    The step passes when every command exits with code zero, and stops at the first that does not.
    """

    kind: Literal["command"] = "command"
    run: list[Annotated[str, Field(min_length=1)] | RepositoryCommands] = Field(min_length=1)
    #: How long the commands may run in all before the step fails. None lets them run.
    timeout_seconds: int | None = Field(default=None, ge=1)
    #: The branch whose run worktree the commands run in. None runs them in the workspace.
    branch: Annotated[str, Field(min_length=1)] | None = None


class CodeStepRun(ContractModel):
    """Call one of the instance's tools, with no model turn around it.

    The tool's parameters named like a value of the run receive that value, and its ``run_id``
    parameter the run's id. A mapping the tool returns is the step result's values.
    """

    kind: Literal["code"] = "code"
    call: ToolName
    #: Set when the hub calls the factory's needs_human tool: the summary of the last attempt of
    #: the step the run stopped at. The tool's ``summary`` parameter receives it, and its
    #: ``step`` parameter that step's id.
    summary: str | None = None


class AgentStepRun(ContractModel):
    """Run a turn on a new thread of the instance, with the prompt and the run's values."""

    kind: Literal["agent"] = "agent"
    #: The text of the step's prompt file.
    prompt: str


class ClientStepRun(ContractModel):
    """Run a coding client in the instance's workspace, with the prompt and the run's values.

    No kinby turn runs around it. The client is killed once its timeout passes.
    """

    kind: Literal["client"] = "client"
    client: CodingClient
    #: The text of the step's prompt file.
    prompt: str
    #: The session to continue, an earlier step's. None starts a new one.
    resume: CodingSessionId | None = None
    timeout_seconds: Annotated[int, Field(ge=1)]
    #: The model and reasoning effort the client runs with. None leaves the client's default.
    model: CodingModel | None = None
    effort: ReasoningEffort | None = None
    #: The branch whose run worktree the client runs in. None runs it in the workspace.
    branch: Annotated[str, Field(min_length=1)] | None = None


class StepRunCommand(ContractModel):
    """Run one step of a factory run in this instance, and return its result."""

    step: Annotated[
        AgentStepRun | ClientStepRun | CommandStepRun | CodeStepRun, Field(discriminator="kind")
    ]
    #: The run and step this is. A turn the step runs records it as its origin.
    origin: FactoryRunOrigin
    #: The attempt at the step this is. None for the factory's needs_human and ended calls,
    #: which are none.
    attempt: int | None = None
    #: The hook that records the step result once the step ends, however it ended.
    hook: HookName | None = None
    work_item: dict[ValueName, StepValue]
    #: Every value the run's earlier steps recorded, by name.
    results: dict[ValueName, StepValue] = Field(default_factory=dict)


class StepResult(ContractModel):
    """What one attempt at a step hands back to the hub."""

    ending: StepEnding
    #: The declared outcome the result names. None takes the next step.
    outcome: str | None = None
    values: dict[ValueName, StepValue] = Field(default_factory=dict)
    #: What happened, for the user to read.
    summary: str = ""
    #: The coding client session a client step ran in, for a later step to resume.
    session: CodingSessionId | None = None
    #: When the plan window of a limited step resets. Only a limited step has one.
    resets_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _resets_at_only_when_limited(self) -> Self:
        limited = self.ending is StepEnding.LIMITED
        if limited and self.resets_at is None:
            raise ValueError("A limited step needs resets_at, the time its plan window resets.")
        if not limited and self.resets_at is not None:
            raise ValueError(f"A {self.ending} step has no resets_at; only a limited step has one.")
        return self


class FactoryRun(ContractModel):
    """One work item's trip through a factory's steps."""

    run_id: UUID
    factory: FactoryName
    #: The instance that handed in its work item.
    instance_id: UUID
    work_item: dict[ValueName, StepValue]
    status: FactoryRunStatus
    #: The step the run is at. None once it is done.
    step: StepId | None
    #: When a run parked until its plan window resets tries its step again. None for any other.
    wakes_at: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class StepAttempt(ContractModel):
    """One attempt at one step of a factory run."""

    step: StepId
    #: Counted from 1 for each step of the run.
    attempt: int
    #: None while the attempt runs.
    ending: StepEnding | None
    outcome: str | None
    values: dict[ValueName, StepValue]
    summary: str
    #: The coding client session a client step ran in.
    session: CodingSessionId | None = None
    started_at: AwareDatetime
    ended_at: AwareDatetime | None


class RunStop(ContractModel):
    """A factory run that moved to done, cancelled or needs human, with what it did since its
    previous stop."""

    run_id: UUID
    factory: FactoryName
    work_item: dict[ValueName, StepValue]
    status: FactoryRunStatus
    #: Grows with each stop of the run, so the instance records each stop once.
    generation: int
    #: The attempts since the run's previous stop, in the order they started.
    attempts: list[StepAttempt]


class FactoryRunIntakeCommand(ContractModel):
    """Hand a work item to the factory whose intake is this instance and routine.

    A work item that matches an unfinished run this instance handed in returns that run instead,
    and retries the step of one that needs a human.
    """

    routine: RoutineName
    work_item: dict[ValueName, StepValue]


class FactoryRunListCommand(ContractModel):
    factory: FactoryName
    #: Only the runs in this status. None lists every run of the factory.
    status: FactoryRunStatus | None = None
    #: Only the runs this instance handed in. None lists every instance's.
    instance_id: UUID | None = None


class FactoryRunListResult(ContractModel):
    #: Oldest first.
    runs: list[FactoryRun]


class FactoryRunGetCommand(ContractModel):
    run_id: UUID


class FactoryRunRetryCommand(ContractModel):
    """Try the step of a run that needs a human again, with the step's retries reset."""

    run_id: UUID


class FactoryRunSendBackCommand(ContractModel):
    """Send a run that needs a human or awaits approval back to an earlier step.

    Its retries and send-backs count afresh.
    """

    run_id: UUID
    step: StepId


class FactoryRunApproveCommand(ContractModel):
    """Approve a run parked at an approve step, which moves it on as the step's next outcome."""

    run_id: UUID


class FactoryRunCancelCommand(ContractModel):
    """End a run that needs a human or is parked, where it is, keeping everything it did."""

    run_id: UUID


class FactoryRunDetail(ContractModel):
    run: FactoryRun
    #: Every attempt at every step, in the order they started.
    attempts: list[StepAttempt]
    #: The steps before the run's step in its factory, which a send back can go to.
    earlier_steps: list[StepId]


class FactoryRunSubscribeCommand(ContractModel):
    """Follow every factory run of the hub as its state changes. Nothing is replayed."""
