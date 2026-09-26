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

#: What a client authenticated against the hub holds: a hub has one user.
HUB_SCOPES = frozenset(Scope)
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
    #: Raised by a client, never sent by a server: its connection dropped under a call.
    CONNECTION_LOST = "CONNECTION_LOST"
    INTERNAL = "INTERNAL"


class ErrorEnvelope(ContractModel):
    code: ErrorCode
    message: str
    retryable: bool
    #: What is wrong with each value the client sent, by field name. Only INVALID_SETUP fills it.
    fields: dict[str, str] = Field(default_factory=dict)


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
    RUN_DELEGATED = "run.delegated"
    MEMORY_RECAPPED = "memory.recapped"
    ROUTINE_FAILURE_HANDLED = "routine.failure.handled"
    SIGNAL_RECEIVED = "signal.received"
    WORKSPACE_REVERTED = "workspace.reverted"


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
SystemPrompt = NewType("SystemPrompt", str)
# The forty-character hex id of a git tree: one workspace snapshot.
TreeId = NewType("TreeId", str)


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
    prompt_version: PromptVersion | None = None
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
    """One run of an outside agent that a tool started, with that run's own tokens.

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

    @model_validator(mode="after")
    def _resets_at_only_when_limited(self) -> Self:
        limited = self.outcome is DelegatedRunOutcome.LIMITED
        if limited and self.resets_at is None:
            raise ValueError("A limited run needs resets_at, the time its plan window resets.")
        if not limited and self.resets_at is not None:
            raise ValueError(f"A {self.outcome} run has no resets_at; only a limited run has one.")
        return self


class RunDelegated(ContractModel):
    type: Literal[EventType.RUN_DELEGATED] = EventType.RUN_DELEGATED
    run: DelegatedRun


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
    | RunDelegated
    | MemoryRecapped
    | RoutineFailureHandled
    | SignalReceived
    | WorkspaceReverted,
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


class ThreadApprovalRespondCommand(ContractModel):
    thread_id: UUID
    approval_id: UUID
    answer: str


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


class ThreadListCommand(ContractModel):
    pass


class ThreadSummary(ContractModel):
    id: UUID
    title: str | None
    created_at: datetime


class ThreadListResult(ContractModel):
    threads: list[ThreadSummary]


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


#: A full git commit SHA, so a pin names one commit and never a branch or a tag.
type CommitSha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class PackageCommit(ContractModel):
    """One commit of a package's git repository, installed in place of an index version."""

    url: Annotated[str, Field(min_length=1)]
    sha: CommitSha


class PackagePin(ContractModel):
    """Move an instance's package to another commit of the git repository it comes from.

    It names the package it moves, which must be the instance's own (ADR 0040).
    """

    id: Annotated[str, Field(min_length=1)]
    sha: CommitSha
    #: The image recipe of that commit. None keeps the recipe the instance records (ADR 0061).
    image_recipe: str | None = None


class PackageSelection(ContractModel):
    id: Annotated[str, Field(min_length=1)]
    distribution: Annotated[str, Field(min_length=1)]
    #: A version from the package index, or a git commit.
    version: Annotated[str, Field(min_length=1)] | PackageCommit
    image_recipe: str = ""


class PackageSummary(ContractModel):
    id: str
    distribution: str
    version: str | PackageCommit


class SetupFieldKind(StrEnum):
    """Where a setup field's value lands: the instance's configuration, or its secrets."""

    CONFIG = "config"
    SECRET = "secret"


class SetupFieldType(StrEnum):
    """How a client asks for a setup field's value."""

    TEXT = "text"
    MULTILINE = "multiline"


class SetupField(ContractModel):
    """One value a prepared image asks for before an instance is created from it."""

    name: str
    label: str
    description: str
    kind: SetupFieldKind
    type: SetupFieldType
    required: bool


class PackageDescription(ContractModel):
    """What a prepared image declares: its card, its version, and its setup fields.

    The built-in fields come first, then the package's own.
    """

    display_name: str
    description: str
    icon: str
    version: str
    setup_fields: list[SetupField]


class ImagePrepareCommand(ContractModel):
    """Prepare a package selection's image. No package prepares kinby's base image."""

    package: PackageSelection | None


class ImagePrepareResult(ContractModel):
    operation_id: UUID


class PackageDescribeCommand(ContractModel):
    """Read what a prepared selection declares. It never builds."""

    package: PackageSelection | None


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


class InstanceCreateCommand(ContractModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    manifest_id: Annotated[str, Field(min_length=1)]
    persona_name: Annotated[str, Field(min_length=1)] | None = None
    #: The value of the built-in model field. The hub checks it with the other setup values.
    model: str
    revision: Annotated[str, Field(min_length=1)] = "HEAD"
    package: PackageSelection | None = None
    #: Values for the configuration fields the prepared image declares, by field name.
    config: dict[str, str] = Field(default_factory=dict)
    #: Values for its secret fields, and any other variables the instance should hold.
    secrets: dict[str, SecretStr] = Field(default_factory=dict)
    avatar: Avatar = DEFAULT_AVATAR

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


class InstanceDeleteCommand(ContractModel):
    """Delete the previewed targets. A target that changed since the preview stops it."""

    instance_id: UUID
    directories: list[Path]
    volumes: list[str]


class InstanceUpdateCommand(ContractModel):
    """Move one instance to the image a revision prepares.

    Its package selection travels along, moved to another commit when a pin names one.
    """

    instance_id: UUID
    revision: Annotated[str, Field(min_length=1)]
    package: PackagePin | None = None


class InstanceSecretsSetCommand(ContractModel):
    """Replace the named values in one instance's secrets. The result carries none of them back."""

    model_config = ConfigDict(hide_input_in_errors=True)

    instance_id: UUID
    secrets: Annotated[dict[str, SecretStr], Field(min_length=1)]

    @field_serializer("secrets", when_used="json")
    def serialize_secrets(self, secrets: dict[str, SecretStr]) -> dict[str, str]:
        return {name: value.get_secret_value() for name, value in secrets.items()}


class InstanceLogsCommand(ContractModel):
    instance_id: UUID
    tail: Annotated[int, Field(gt=0)] | None = None


#: The version of the contract an instance speaks, reported before a lifecycle operation.
CONTRACT_VERSION = "1"


class Capability(StrEnum):
    """What an instance's contract server can do. A hub reads it before acting on the instance."""

    WS = "ws"
    DRAIN = "drain"


class InstanceProbeCommand(ContractModel):
    pass


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


class InstanceSummary(ContractModel):
    instance_id: UUID
    manifest_id: str
    persona_name: str | None
    source_revision: str
    image_id: str
    intended_state: IntendedState
    runtime_id: str
    storage: list[StorageItem]
    avatar: Avatar
    package: PackageSummary | None = None


class InstanceListResult(ContractModel):
    instances: list[InstanceSummary]


class InstanceStatusResult(ContractModel):
    instance_id: UUID
    process: ProcessState
    readiness: Readiness
    detail: str = ""
    #: The lifecycle operation still running, so a client that lost its response finds it again.
    active_operation_id: UUID | None = None


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
    PACKAGE_MISMATCH = "package-mismatch"


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
    #: The package the manifest's [package] names, recorded so later updates build and pin it.
    package: PackageSelection | None = None


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


class OperationState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class OperationStep(ContractModel):
    """One named stage of a lifecycle operation, in the order the hub ran it."""

    name: str
    state: OperationState
    detail: str


class OperationGetResult(ContractModel):
    operation_id: UUID
    #: None for an image preparation, which runs before any instance exists.
    instance_id: UUID | None
    kind: OperationKind
    state: OperationState
    detail: str
    steps: list[OperationStep] = Field(default_factory=list)


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
    navigation: Navigation = Field(default_factory=Navigation)
    delegated_runs: list[ReportedRun] = Field(default_factory=list)


class SubscriptionUse(TokenTotals):
    """The delegated runs one subscription usage source paid for, counted and never priced."""

    usage_source: UsageSource
    runs: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0


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
    navigation: NavigationMeans = Field(default_factory=NavigationMeans)
    subscriptions: list[SubscriptionUse]


class StatsBucket(StatsSummary):
    start: date


class ModelCallMismatch(ContractModel):
    thread_id: UUID
    turn_id: UUID


class StatsGetCommand(ContractModel):
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    by: StatsBucketSize = StatsBucketSize.DAY


class PlanWindow(ContractModel):
    """A rolling period over which a subscription usage source limits use."""

    usage_source: UsageSource
    duration_seconds: int


class PlanLimit(ContractModel):
    """A subscription usage source whose plan stopped a delegated run, until its window resets."""

    usage_source: UsageSource
    resets_at: AwareDatetime


class StatsGetResult(ContractModel):
    records: list[TurnMetrics]
    buckets: list[StatsBucket]
    total: StatsSummary
    plan_windows: list[PlanWindow]
    limits: list[PlanLimit]
    unpriced_models: list[str]
    warnings: list[ModelCallMismatch] = Field(default_factory=list)


class ApiUse(TokenTotals):
    """The model calls the API usage source paid for, priced where the model is known."""

    cost: float | None = None


class StatsSummaryResult(ContractModel):
    """Usage across the hub's running instances, read live from each one and stored nowhere.

    A total leaves out every instance in ``skipped`` or ``unreachable``.
    """

    #: Each counted instance's buckets, by hub instance ID.
    buckets: dict[UUID, list[StatsBucket]]
    api: ApiUse
    subscriptions: list[SubscriptionUse]
    #: The latest reset per subscription usage source, across instances.
    limits: list[PlanLimit]
    #: Instances not running, so not asked.
    skipped: list[UUID]
    #: Running instances that failed to answer, or did not answer in time.
    unreachable: list[UUID]


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
