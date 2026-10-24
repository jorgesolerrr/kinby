"""Typed hub lifecycle service."""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import re
import shutil
from collections.abc import Callable, Collection, Coroutine, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import IO
from uuid import UUID, uuid4

from dotenv import dotenv_values
from pydantic import JsonValue

from kinby.contracts import (
    CHANNEL_CREATE,
    CHANNEL_DELETE,
    CHANNEL_KINDS,
    CHANNEL_LIST,
    CHANNEL_MUTE,
    CHANNEL_TEST,
    CHANNEL_UNMUTE,
    CHANNEL_UPDATE,
    DEFAULT_AVATAR,
    FACTORY_CHECK,
    FACTORY_DESCRIBE,
    FACTORY_EDIT,
    FACTORY_GET,
    FACTORY_LIST,
    FACTORY_RUN_APPROVE,
    FACTORY_RUN_CANCEL,
    FACTORY_RUN_GET,
    FACTORY_RUN_INTAKE,
    FACTORY_RUN_LIST,
    FACTORY_RUN_RETRY,
    FACTORY_RUN_SEND_BACK,
    FACTORY_RUN_SUBSCRIBE,
    HOME_SCOPES,
    HUB_SECRET_DELETE,
    HUB_SECRET_LIST,
    HUB_SECRET_SET,
    HUB_WRITE,
    IMAGE_DESCRIBE,
    IMAGE_PREPARE,
    INSTANCE_ADOPT,
    INSTANCE_ADOPT_PREVIEW,
    INSTANCE_CREATE,
    INSTANCE_DELETE,
    INSTANCE_DELETE_PREVIEW,
    INSTANCE_LIST,
    INSTANCE_LOGIN_START,
    INSTANCE_LOGS,
    INSTANCE_PICKS_GET,
    INSTANCE_PICKS_SET,
    INSTANCE_RECREATE,
    INSTANCE_RELAY_READ,
    INSTANCE_REMOVE,
    INSTANCE_RESTORE,
    INSTANCE_SECRETS_SET,
    INSTANCE_START,
    INSTANCE_STATUS,
    INSTANCE_STOP,
    INSTANCE_UPDATE,
    INTAKE_SCOPES,
    LIBRARY_GIT_ADD,
    LIBRARY_GIT_PREVIEW,
    LIBRARY_LIST,
    LIBRARY_SKILL_CHECK,
    LIBRARY_SKILL_DELETE,
    LIBRARY_SKILL_MOVE,
    LIBRARY_SKILL_READ,
    LIBRARY_SKILL_WRITE,
    NOTIFICATION_LIST,
    NOTIFICATION_RAISE,
    NOTIFICATION_REPLY,
    NOTIFICATION_SCOPES,
    OPERATION_GET,
    RELAYED_READS,
    STATS_RUNS,
    STATS_SUMMARY,
    THREAD_APPROVAL_RESPOND,
    THREAD_ASK,
    THREAD_TURN_START,
    ApprovalDecision,
    Avatar,
    Capability,
    ChannelKindName,
    ContainerOwner,
    ControlToken,
    DeclaredSetup,
    DrainState,
    ErrorCode,
    FactoryEditCommand,
    FactoryName,
    FactoryResult,
    FactoryRole,
    HubRouteMissing,
    HubSecret,
    HubSecretDeleteCommand,
    HubSecretDeleteResult,
    HubSecretLink,
    HubSecretListCommand,
    HubSecretListResult,
    HubSecretName,
    HubSecretSetCommand,
    HubSecretSetResult,
    HubWriteCommand,
    HubWriteResult,
    ImageDescribeCommand,
    ImagePrepareCommand,
    ImagePrepareResult,
    InstanceAdoptCommand,
    InstanceAdoptPreviewCommand,
    InstanceAdoptPreviewResult,
    InstanceCreateCommand,
    InstanceDeleteCommand,
    InstanceDeletePreviewCommand,
    InstanceDeletePreviewResult,
    InstanceListCommand,
    InstanceListResult,
    InstanceLoginStartCommand,
    InstanceLogsCommand,
    InstanceLogsResult,
    InstanceNotice,
    InstancePicksGetCommand,
    InstancePicksResult,
    InstancePicksSetCommand,
    InstanceRecreateCommand,
    InstanceRelayReadCommand,
    InstanceRelayReadResult,
    InstanceRemoveCommand,
    InstanceRestoreCommand,
    InstanceSecretsSetCommand,
    InstanceSetup,
    InstanceStartCommand,
    InstanceStatusCommand,
    InstanceStatusResult,
    InstanceStopCommand,
    InstanceSummary,
    InstanceUpdateCommand,
    IntendedState,
    LibraryGitAddCommand,
    LibraryListResult,
    LibrarySkillCheckCommand,
    LibrarySkillWriteCommand,
    LifecycleOperationResult,
    LoginPrompt,
    NewerVersion,
    OperationGetCommand,
    OperationGetResult,
    OperationKind,
    OperationState,
    ProcessState,
    Readiness,
    RecreateReason,
    RevisionBehind,
    RunStop,
    Scope,
    SetupValue,
    SkillName,
    StatsGetCommand,
    StatsGetResult,
    StatsRunsCommand,
    StatsRunsResult,
    StatsSummaryResult,
    StepEnding,
    StepResult,
    StepRunCommand,
    StorageItem,
    StorageKind,
    SubscriptionLogin,
    TemplateBehind,
    ThreadApprovalRespondCommand,
    ThreadAskCommand,
    ThreadTurnStartCommand,
    WaitingApproval,
)
from kinby.core.clock import utc_now
from kinby.core.contract_server import CONTROL_TOKEN_VARIABLE
from kinby.core.dispatcher import Dispatcher
from kinby.core.errors import (
    AdoptionBlocked,
    FactoryCheckFailed,
    FactoryNotFound,
    HomeInstanceKept,
    ImageNotPrepared,
    InstanceRefused,
    InstanceUnavailable,
    InvalidSetup,
    LifecycleOperationInFlight,
    LifecycleOperationNotFound,
    LoginNotFound,
    ManagedInstanceNotFound,
    NameTaken,
    PermissionDenied,
)
from kinby.core.usage import TimeRange
from kinby.factories import SHIPPED_FACTORIES
from kinby.factories.file import InstanceName
from kinby.hub.access import HubAccess, new_control_token
from kinby.hub.adoption import INSTANCE_MOUNT, blocker, preflight, previous_manager
from kinby.hub.approvals import HomeApprovals
from kinby.hub.channels import ChannelKind, Channels, installed_kinds
from kinby.hub.control import (
    ControlConnectionLost,
    ControlEndpoint,
    ControlUndelivered,
    ControlUnreachable,
    HttpInstanceControl,
    IncompatibleLifecycleEndpoint,
    InstanceControl,
)
from kinby.hub.factories import FactoryStore, InstanceTemplate
from kinby.hub.home import (
    ChannelMute,
    ChannelUnmute,
    InstanceAsk,
    LibraryMove,
    RelayedWrite,
    home_write,
)
from kinby.hub.library import Library, skill_names
from kinby.hub.models import (
    ContainerRuntime,
    ImageArtifact,
    ImagePreparation,
    ImageSelection,
    InstanceEndpoint,
    InstanceSpec,
    InstanceUnreachable,
    LifecycleRecovery,
    RecoveredState,
    RuntimeStatus,
    SetupSpec,
    Signal,
    secrets_digest,
)
from kinby.hub.outbox import Outbox, Sleep
from kinby.hub.recovery import recover_lifecycle
from kinby.hub.registry import FactoryMember, HubRegistry, ManagedInstance
from kinby.hub.relay import relayed_call
from kinby.hub.replies import Replies
from kinby.hub.runs import FactoryRuns, LostAnswer, UndeliveredCall, Untold
from kinby.hub.secrets import HubSecrets, replace_protected, write_protected
from kinby.hub.setup import (
    ENVIRONMENT_NAME,
    configuration,
    held_secrets,
    instance_setup,
    link_errors,
    manifest_settings,
    secret_field,
    setup_errors,
)
from kinby.hub.template_sync import sync_template
from kinby.hub.usage import Uncounted, run_usage, summed_usage
from kinby.instance import (
    PLACEHOLDER_MODEL,
    Instance,
    ManifestError,
    ModelName,
    init_from_template,
    init_instance,
    inspect_instance,
)
from kinby.instance.config_changes import file_hash
from kinby.instance.layout import SYSTEM_NAME
from kinby.instance.skills_lock import lock_hash, read_lock
from kinby.library import HOME_SKILLS, SHIPPED_SKILLS
from kinby.plugins.hub import HUB_SCOPES_VARIABLE, HUB_URL_VARIABLE
from kinby.setup import API_KEY_FIELD, BEHAVIOR_PROMPT_FIELD, MODEL_FIELD

_logger = logging.getLogger(__name__)

_INSTANCE_HOST = "0.0.0.0"
_INSTANCE_PORT = 8787
_SERVE_HEADER = re.compile(r"\s*\[\s*serve\s*\]\s*(#.*)?$")
_TABLE_HEADER = re.compile(r"\s*\[")
_LISTEN_KEY = re.compile(r"\s*listen\s*=")
#: How long a forced container may take to exit before the runtime terminates it.
STOP_GRACE_SECONDS = 30
#: How long a forced instance may take to report its own drain before the container goes down.
FORCE_ANSWER_SECONDS = 30
#: How often the hub looks at a container, and how long it waits for a stop or for a boot.
_POLL_SECONDS = 0.2
_STOP_OBSERVE_SECONDS = 120
READY_OBSERVE_SECONDS = 120
#: How long a stats summary waits for one instance before it counts it unreachable.
STATS_SECONDS = 5
#: Pause before calling a drain again, so a socket that closes immediately does not spin.
_DRAIN_RETRY_SECONDS = 0.2
_RUNTIME_STOPPED = frozenset({"absent", "created", "stopped", "failed"})
#: A failed container is not among them: Docker's restart policy brings it back until it is stopped.
_ALREADY_STOPPED = frozenset({"absent", "created", "stopped"})
_INTERRUPTED_OPERATION = "The hub stopped before this operation finished."
#: What an image preparation builds on: the hub's own checkout, as `instance.create` defaults to.
_PREPARED_REVISION = "HEAD"
_HOME_NAME = "home"
_HOME_PERSONA_NAME = "Kinby"
#: How long a login waits for the user to sign in before its code counts as expired.
LOGIN_SECONDS = 15 * 60
#: The title of the notification each kind of instance notice raises.
_NOTICE_TITLES = {
    "revision_behind": "Revision behind",
    "template_behind": "Template behind",
    "hub_route_missing": "Hub route missing",
}
#: What an instance answers for an approval that no longer waits: gone, or its turn ended.
_NOT_WAITING = frozenset({ErrorCode.NOT_FOUND, ErrorCode.NO_ACTIVE_TURN})
#: Colors and cursor moves a command prints around its URL and code.
_TERMINAL_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class HubAlreadyRunning(RuntimeError):
    """Another process holds this hub directory."""


class ReplacementNotReady(RuntimeError):
    """The replacement container never reported a booted instance behind it."""


def _acquire_directory(directory: Path) -> IO[str]:
    """Hold this directory until the process exits, or until close.

    The kernel releases the lock when the process dies, so a later hub can tell
    that unfinished operations belong to a process that is gone.
    """
    handle = (directory / "hub.lock").open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise HubAlreadyRunning(f"Another hub is already running for {directory}.") from None
    return handle


def _notices(
    record: ManagedInstance,
    hub_revision: str,
    served: Mapping[tuple[FactoryName, InstanceName], InstanceTemplate],
    route: Mapping[str, str],
    environment: Mapping[str, str],
) -> dict[str, InstanceNotice]:
    """The hub route the instance's environment lacks, the kinby revision the hub moved past, and
    the template the factory moved past, if any, each by its kind and the value it reports.

    *served* holds each template the hub serves now, by its factory and name. *route* is what the
    hub writes into the instance's *environment* when it creates a container. An update refuses a
    factory's instance whose template the hub does not serve, so its route notice waits until the
    hub serves the template again.
    """
    notices: dict[str, InstanceNotice] = {}
    member = record.factory
    template = None if member is None else served.get((member.factory, member.name))
    updatable = member is None or template is not None
    if updatable and any(environment.get(name) != value for name, value in route.items()):
        notices[f"hub_route_missing:{route[HUB_URL_VARIABLE]} {route[HUB_SCOPES_VARIABLE]}"] = (
            HubRouteMissing(
                message=(
                    "The instance cannot reach the hub with the scopes it holds, "
                    f"{route[HUB_SCOPES_VARIABLE]}. An instance update gives it the route."
                )
            )
        )
    instance_revision = record.source_revision
    if instance_revision and instance_revision != hub_revision:
        notices[f"revision_behind:{hub_revision}"] = RevisionBehind(
            message=(
                f"The instance runs kinby {instance_revision[:7]}, "
                f"and the hub is at {hub_revision[:7]}."
            ),
            instance_revision=instance_revision,
            hub_revision=hub_revision,
        )
    if member is not None and template is not None and template.baseline != member.baseline:
        found = (
            f'The hub kept no record of the files template "{member.name}" '
            f'of factory "{member.factory}" supplied.'
            if member.baseline is None
            else f'Factory "{member.factory}" changed template "{member.name}" '
            "since the hub copied it."
        )
        baseline = file_hash(json.dumps(template.baseline, sort_keys=True).encode())
        notices[f"template_behind:{baseline}"] = TemplateBehind(
            message=f"{found} An instance update brings the files up to the factory's."
        )
    return notices


def _selection(record: ManagedInstance, revision: str) -> ImageSelection:
    """The image an instance runs on: the revision, and its template's recipe."""
    recipe = record.factory.recipe if record.factory is not None else None
    return ImageSelection(revision, recipe)


def _instance_name(command: InstanceCreateCommand) -> str:
    """The name the request gives, or ``{factory}-{role}`` for one that picks a factory role."""
    if command.manifest_id is not None:
        return command.manifest_id
    if command.factory_role is None:
        raise InvalidSetup({"manifest_id": "Name the instance, or pick a factory role."})
    return f"{command.factory_role.factory}-{command.factory_role.name}"


def _member(factory: FactoryName, template: InstanceTemplate) -> FactoryMember:
    """The role an instance written from *template* holds, with what the template supplied."""
    return FactoryMember(
        factory=factory,
        name=template.name,
        recipe=template.recipe,
        setup=template.setup,
        baseline=template.baseline,
    )


def _write_behavior_prompt(staging: Path, configured: Mapping[str, SetupValue]) -> None:
    """Write the behavior prompt the creation resolved. A template's baseline keeps the hash of
    its own, so an instance update keeps one the user changed."""
    behavior_prompt = configured.get(BEHAVIOR_PROMPT_FIELD.name)
    if isinstance(behavior_prompt, str):
        (staging / SYSTEM_NAME).write_text(behavior_prompt, encoding="utf-8")


def _instance_mount(storage: tuple[StorageItem, ...]) -> StorageItem | None:
    return next(
        (
            item
            for item in storage
            if item.kind is StorageKind.BIND and item.destination == INSTANCE_MOUNT
        ),
        None,
    )


def _delete_directory(directory: Path) -> None:
    """A retry finds a directory an interrupted attempt already deleted, and that is done."""
    try:
        shutil.rmtree(directory)
    except FileNotFoundError:
        return


def _set_listen(lines: list[str]) -> None:
    """Set ``serve.listen`` to the hub's address, inside ``[serve]`` when the file has one."""
    listen = f'listen = "{_INSTANCE_HOST}:{_INSTANCE_PORT}"'
    header = next((i for i, line in enumerate(lines) if _SERVE_HEADER.match(line)), None)
    if header is None:
        lines.extend(("", "[serve]", listen))
        return
    end = next(
        (i for i in range(header + 1, len(lines)) if _TABLE_HEADER.match(lines[i])),
        len(lines),
    )
    for index in range(header + 1, end):
        if _LISTEN_KEY.match(lines[index]):
            lines[index] = listen
            return
    lines.insert(header + 1, listen)


@dataclass(frozen=True)
class PendingStop:
    """The stop running on one instance, and the way a later request escalates it."""

    operation_id: UUID
    force: asyncio.Event
    #: Recreation is the way back for a broken container, so it terminates one that never answers.
    terminates_unreachable: bool = False


@dataclass(frozen=True)
class ObservedProcess:
    """What the container runtime reports for one instance, its detail redacted."""

    process: ProcessState
    readiness: Readiness
    detail: str


class Hub:
    """Own instance management without booting instance runtimes in this process."""

    def __init__(
        self,
        directory: Path,
        *,
        runtime: ContainerRuntime,
        images: ImagePreparation,
        control: InstanceControl | None = None,
        docker_host_directory: Path | None = None,
        shipped_factories: Path = SHIPPED_FACTORIES,
        shipped_skills: Path = SHIPPED_SKILLS,
        private_url: str | None = None,
        public_url: str | None = None,
        channel_kinds: Mapping[ChannelKindName, type[ChannelKind]] | None = None,
        clock: Callable[[], datetime] = utc_now,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        """*private_url* is where the hub's instances reach it on their private network, and
        *public_url* where the user reaches its web app. *channel_kinds* stand in for the kinds
        installed packages expose."""
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._directory_lock = _acquire_directory(self.directory)
        try:
            self.instances_directory = self.directory / "instances"
            self.instances_directory.mkdir(parents=True, exist_ok=True)
            self._docker_host_directory = (
                Path(docker_host_directory).resolve()
                if docker_host_directory is not None
                else self.directory
            )
            self.registry = HubRegistry(self.directory)
            self.registry.fail_interrupted_operations(_INTERRUPTED_OPERATION)
            self.access = HubAccess(self.registry)
            self._runtime = runtime
            self._images = images
            self._private_url = private_url
            self.factories = FactoryStore(self.directory / "factories", shipped_factories)
            self.library = Library(
                self.directory / "skills", shipped_skills, self.factories, self.registry
            )
            self.library.receive()
            self.secrets = HubSecrets(self.directory / "hub-secrets.json")
            self.approvals = HomeApprovals()
            self.channels = Channels(
                self.registry,
                self.secrets,
                installed_kinds() if channel_kinds is None else channel_kinds,
                public_url,
                clock,
            )
            self.outbox = Outbox(self.registry, self.channels, public_url, clock, sleep)
            self.runs = FactoryRuns(
                self.registry, self.factories, self._run_step, self._tell_stop, self.outbox
            )
            self.replies = Replies(
                self.registry,
                self.channels,
                self._start_turn,
                self._answer_approval,
                self.runs.answer_stop,
                clock,
                sleep,
            )
            self._control = control if control is not None else HttpInstanceControl()
            self._locks: dict[UUID, asyncio.Lock] = {}
            self._hub_secrets_lock = asyncio.Lock()
            self._stopping: dict[UUID, PendingStop] = {}
            self._tasks: set[asyncio.Task[None]] = set()
            self.dispatcher = Dispatcher()
            self._serve_hub(self.dispatcher)
        except BaseException:
            self._directory_lock.close()
            raise

    def close(self) -> None:
        """Release this directory so another process can own it."""
        self._directory_lock.close()

    def _serve_hub(self, dispatcher: Dispatcher) -> None:
        """Register every hub method. A caller reaches the ones its scopes admit."""
        dispatcher.register(INSTANCE_CREATE, self.create)
        dispatcher.register(INSTANCE_START, self.start)
        dispatcher.register(INSTANCE_STOP, self.stop)
        dispatcher.register(INSTANCE_RECREATE, self.recreate)
        dispatcher.register(INSTANCE_REMOVE, self.remove)
        dispatcher.register(INSTANCE_RESTORE, self.restore)
        dispatcher.register(INSTANCE_DELETE_PREVIEW, self.delete_preview)
        dispatcher.register(INSTANCE_DELETE, self.delete)
        dispatcher.register(INSTANCE_UPDATE, self.update)
        dispatcher.register(INSTANCE_SECRETS_SET, self.set_secrets)
        dispatcher.register(INSTANCE_ADOPT_PREVIEW, self.adopt_preview)
        dispatcher.register(INSTANCE_ADOPT, self.adopt)
        dispatcher.register(INSTANCE_LIST, self.list)
        dispatcher.register(INSTANCE_STATUS, self.status)
        dispatcher.register(INSTANCE_LOGS, self.logs)
        dispatcher.register(INSTANCE_LOGIN_START, self.start_login)
        dispatcher.register(OPERATION_GET, self.operation)
        dispatcher.register(IMAGE_PREPARE, self.prepare_image)
        dispatcher.register(IMAGE_DESCRIBE, self.describe_image)
        dispatcher.register(FACTORY_LIST, self.factories.list)
        dispatcher.register(FACTORY_GET, self.factories.get)
        dispatcher.register(FACTORY_CHECK, self.factories.check)
        dispatcher.register(FACTORY_EDIT, self.edit_factory)
        dispatcher.register(FACTORY_DESCRIBE, self.factories.describe)
        dispatcher.register(FACTORY_RUN_LIST, self.runs.list)
        dispatcher.register(FACTORY_RUN_GET, self.runs.get)
        dispatcher.register(FACTORY_RUN_RETRY, self.runs.retry)
        dispatcher.register(FACTORY_RUN_SEND_BACK, self.runs.send_back)
        dispatcher.register(FACTORY_RUN_APPROVE, self.runs.approve)
        dispatcher.register(FACTORY_RUN_CANCEL, self.runs.cancel)
        dispatcher.register_subscription(FACTORY_RUN_SUBSCRIBE, self.runs.subscribe)
        dispatcher.register(LIBRARY_LIST, self.library.list)
        dispatcher.register(LIBRARY_SKILL_READ, self.library.read_skill)
        dispatcher.register(LIBRARY_SKILL_WRITE, self.library.write_skill)
        dispatcher.register(LIBRARY_SKILL_DELETE, self.library.delete_skill)
        dispatcher.register(LIBRARY_SKILL_CHECK, self.library.check)
        dispatcher.register(LIBRARY_SKILL_MOVE, self.library.move)
        dispatcher.register(LIBRARY_GIT_PREVIEW, self.library.preview)
        dispatcher.register(LIBRARY_GIT_ADD, self.library.add)
        dispatcher.register(INSTANCE_PICKS_GET, self.picks)
        dispatcher.register(INSTANCE_PICKS_SET, self.set_picks)
        dispatcher.register(HUB_SECRET_LIST, self.list_hub_secrets)
        dispatcher.register(HUB_SECRET_SET, self.set_hub_secret)
        dispatcher.register(HUB_SECRET_DELETE, self.delete_hub_secret)
        dispatcher.register(STATS_SUMMARY, self.stats_summary)
        dispatcher.register(STATS_RUNS, self.stats_runs)
        dispatcher.register(CHANNEL_KINDS, self.channels.kinds)
        dispatcher.register(CHANNEL_LIST, self.channels.list)
        dispatcher.register(CHANNEL_CREATE, self.channels.create)
        dispatcher.register(CHANNEL_UPDATE, self.channels.update)
        dispatcher.register(CHANNEL_DELETE, self.channels.delete)
        dispatcher.register(CHANNEL_TEST, self.channels.test)
        dispatcher.register(CHANNEL_MUTE, self.channels.mute)
        dispatcher.register(CHANNEL_UNMUTE, self.channels.unmute)
        dispatcher.register(NOTIFICATION_LIST, self.outbox.list)
        dispatcher.register(NOTIFICATION_REPLY, self.replies.reply)

    def instance_scopes(self, instance_id: UUID) -> frozenset[Scope]:
        """The scopes the registry grants one of the hub's instances on its hub route."""
        record = self.registry.instance(instance_id)
        if record is None:
            return frozenset()
        return (
            NOTIFICATION_SCOPES
            | (INTAKE_SCOPES if record.factory is not None else frozenset())
            | (HOME_SCOPES if record.home else frozenset())
        )

    def hub_route(self, instance_id: UUID) -> tuple[frozenset[Scope], Dispatcher]:
        """What one of the hub's instances may call on the hub, built from its scopes."""
        scopes = self.instance_scopes(instance_id)
        dispatcher = Dispatcher()
        if Scope.NOTIFICATION_RAISE in scopes:
            dispatcher.register(
                NOTIFICATION_RAISE, partial(self.outbox.raise_notification, instance_id)
            )
        if Scope.FACTORY_INTAKE in scopes:
            dispatcher.register(FACTORY_RUN_INTAKE, partial(self.runs.intake, instance_id))
        if Scope.HUB_HOME in scopes:
            self._serve_hub(dispatcher)
            dispatcher.register(INSTANCE_RELAY_READ, self.relay_read)
            dispatcher.register(HUB_WRITE, partial(self.write_for_home, instance_id))
        return scopes, dispatcher

    def home_approvals(self, instance_id: UUID) -> HomeApprovals | None:
        """What the hub witnesses on the instance's relay: the home instance's approvals only."""
        record = self.registry.instance(instance_id)
        return self.approvals if record is not None and record.home else None

    async def write_for_home(self, home: UUID, command: HubWriteCommand) -> HubWriteResult:
        """Run a hub tool's write once, for the approval the hub saw the user give that call."""
        write = home_write(command.tool, command.arguments)
        approved = self.approvals.approved(command.approval_id)
        if approved.name != command.tool or home_write(approved.name, approved.arguments) != write:
            raise PermissionDenied(f'Approval "{command.approval_id}" is for another call.')
        self.approvals.use(command.approval_id)
        match write:
            case InstanceCreateCommand():
                result = await self.create(write, requester=home)
            case InstanceStartCommand():
                result = await self.start(write, requester=home)
            case InstanceStopCommand():
                result = await self.stop(write, requester=home)
            case InstanceRecreateCommand():
                result = await self.recreate(write, requester=home)
            case InstancePicksSetCommand():
                result = await self.set_picks(write)
            case LibrarySkillWriteCommand():
                result = await self.library.write_skill(write)
            case LibraryGitAddCommand():
                result = await self.library.add(write)
            case LibraryMove():
                result = await self._move(write)
            case FactoryEditCommand():
                result = await self.edit_factory(write)
            case ChannelMute():
                result = await self.channels.mute(write)
            case ChannelUnmute():
                result = await self.channels.unmute(write)
            case RelayedWrite():
                params = write.command.model_dump(mode="json")
                return HubWriteResult(
                    result=await self._relayed(write.instance_id, write.method.name, params)
                )
            case InstanceAsk():
                return HubWriteResult(result=await self._ask(write))
        return HubWriteResult(result=result.model_dump(mode="json"))

    async def edit_factory(self, command: FactoryEditCommand) -> FactoryResult:
        """Edit the factory, then raise the notices of the instances its templates moved past."""
        edited = await self.factories.edit(command)
        await self._raise_notices()
        return edited

    async def _move(self, move: LibraryMove) -> LibraryListResult:
        checked = await self.library.check(LibrarySkillCheckCommand(name=move.name))
        if checked.newer != NewerVersion(commit=move.commit, hash=move.hash, diff=move.diff):
            raise PermissionDenied(
                f'The move of "{move.name}" brings another diff than the approval showed. '
                "Check the skill again."
            )
        return await self.library.move(move)

    async def _ask(self, ask: InstanceAsk) -> dict[str, JsonValue]:
        # Only the control socket admits thread.ask, so no client of the relay claims the origin.
        command = ThreadAskCommand(message=ask.message)
        return await self._relayed(
            ask.instance_id,
            THREAD_ASK.name,
            command.model_dump(mode="json"),
            socket_path="/control",
        )

    async def _start_turn(self, instance_id: UUID, command: ThreadTurnStartCommand) -> None:
        await self._relayed(instance_id, THREAD_TURN_START.name, command.model_dump(mode="json"))

    async def _answer_approval(
        self,
        instance_id: UUID,
        thread_id: UUID,
        waiting: WaitingApproval,
        decision: ApprovalDecision,
    ) -> bool:
        """Answer the approval on the instance's socket, as the relay carries the user's answer.
        False when it no longer waits.

        The hub sees a home instance's approval approved before the instance does, so the hub
        write the resumed turn makes finds it (ADR 0096).
        """
        approval_id = waiting.request.approval_id
        approvals = self.home_approvals(instance_id)
        if approvals is not None and decision is ApprovalDecision.APPROVE:
            approvals.approve(waiting.turn_id, waiting.request)
        command = ThreadApprovalRespondCommand(
            thread_id=thread_id, approval_id=approval_id, decision=decision
        )
        try:
            await self._relayed(
                instance_id, THREAD_APPROVAL_RESPOND.name, command.model_dump(mode="json")
            )
        except InstanceRefused as exc:
            if exc.error.code not in _NOT_WAITING:
                raise
            if approvals is not None:
                approvals.use(approval_id)
            return False
        return True

    async def relay_read(self, command: InstanceRelayReadCommand) -> InstanceRelayReadResult:
        if command.method not in RELAYED_READS:
            raise PermissionDenied(f'"{command.method}" is not a read the hub relays.')
        return InstanceRelayReadResult(
            result=await self._relayed(command.instance_id, command.method, command.params)
        )

    async def _relayed(
        self,
        instance_id: UUID,
        method: str,
        params: dict[str, JsonValue],
        *,
        socket_path: str = "/ws",
    ) -> dict[str, JsonValue]:
        """Call an instance with its own control token, as the relay reaches it."""
        endpoint = await self.endpoint(instance_id)
        match endpoint:
            case InstanceUnreachable.MISSING:
                raise ManagedInstanceNotFound(f'Instance "{instance_id}" was not found.')
            case InstanceUnreachable.UNAVAILABLE:
                raise InstanceUnavailable(f'Instance "{instance_id}" is not running.')
        return await relayed_call(endpoint, method, params, socket_path=socket_path)

    async def _run_step(
        self, instance_id: UUID, command: StepRunCommand
    ) -> StepResult | UndeliveredCall | LostAnswer:
        """A step attempt whose answer a lost connection kept from the hub fails, unless the
        instance holds that answer to give when the hub calls again."""
        try:
            endpoint = await self._endpoint(self._active_instance(instance_id))
            holds = (
                command.attempt is not None
                and Capability.STEP_ONCE in (await self._control.probe(endpoint)).capabilities
            )
        except Exception as exc:
            return UndeliveredCall(str(exc) or type(exc).__name__)
        try:
            return await self._control.run_step(endpoint, command)
        except ControlUndelivered as exc:
            return UndeliveredCall(str(exc) or type(exc).__name__)
        except ControlConnectionLost as exc:
            if holds:
                return LostAnswer(str(exc) or type(exc).__name__)
            return StepResult(ending=StepEnding.FAILED, summary=str(exc) or type(exc).__name__)
        except Exception as exc:
            return StepResult(ending=StepEnding.FAILED, summary=str(exc) or type(exc).__name__)

    async def _tell_stop(self, instance_id: UUID, stop: RunStop) -> Untold | None:
        try:
            endpoint = await self._endpoint(self._active_instance(instance_id))
            await self._control.run_stopped(endpoint, stop)
        except Exception as exc:
            return Untold(str(exc) or type(exc).__name__)
        return None

    def _schedule(self, work: Coroutine[object, object, None], *, eager: bool = False) -> None:
        """An eager task runs up to its first wait before the call that scheduled it returns."""
        task = asyncio.create_task(work, eager_start=eager)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _collecting_after(self, operation: Coroutine[object, object, None]) -> None:
        """Run the operation, then remove the images it may have left unreferenced."""
        await operation
        await self._collect_images()

    async def _collect_images(self) -> None:
        """An image the collection cannot remove waits for the next one, and fails nothing."""
        try:
            await self._images.collect()
        except Exception:
            _logger.exception("Could not collect unreferenced images")

    async def create(
        self, command: InstanceCreateCommand, requester: UUID | None = None
    ) -> LifecycleOperationResult:
        """Accept a creation only once the whole request checks out: a vanilla instance's image is
        prepared, a factory role's factory passes the check, the name is free, the setup values
        fit what the image or the role's template declares, the hub keeps each hub secret a field
        takes, and the library holds each pick. Typed values to save to the hub are saved first.

        *requester* is the instance that asked for it through the hub, if one did."""
        manifest_id = _instance_name(command)
        role = command.factory_role
        template: InstanceTemplate | None = None
        member: FactoryMember | None = None
        if role is not None:
            template = await self.factories.template(role.factory, role.name)
            if template is None:
                raise InvalidSetup(
                    {"factory_role": f'Factory "{role.factory}" has no role "{role.name}".'}
                )
            member = _member(role.factory, template)
        secrets = {name: value.get_secret_value() for name, value in command.secrets.items()}
        kept = self.secrets.values()
        linked = {field: kept[name] for field, name in command.hub_secrets.items() if name in kept}
        errors = setup_errors(
            self._prepared_description() if template is None else template.setup,
            model=command.model,
            config=command.config,
            secrets=secrets | linked,
        ) | link_errors(secrets, command.hub_secrets, command.save_to_hub, kept)
        if self._name_taken(manifest_id):
            errors["manifest_id"] = f'An instance named "{manifest_id}" exists already.'
        picks = sorted(set(command.picks))
        refusal = self.library.refusal(
            picks, skill_names(template.files) if template is not None else ()
        )
        if refusal is not None:
            errors["picks"] = refusal
        if errors:
            raise InvalidSetup(errors)
        for field, name in command.save_to_hub.items():
            self.secrets.save(name, secrets[field])
        record = self._new_record(
            manifest_id,
            persona_name=command.persona_name,
            revision=command.revision,
            avatar=command.avatar,
            factory=member,
        )
        accepted = self._begin_create(
            record, command.model, command.config, secrets | linked, template, picks, requester
        )
        self.registry.link_secrets(
            record.instance_id,
            held_secrets(command.model, command.hub_secrets | command.save_to_hub),
        )
        return accepted

    def _held(self, record: ManagedInstance) -> bool:
        """Whether the hub lists the instance, or an operation is still at work on it."""
        return record.active or self.registry.active_operation(record.instance_id) is not None

    def _name_taken(self, manifest_id: str) -> bool:
        return any(
            record.manifest_id == manifest_id and self._held(record)
            for record in self.registry.managed_instances()
        )

    def _new_record(
        self,
        manifest_id: str,
        *,
        persona_name: str | None = None,
        revision: str = _PREPARED_REVISION,
        avatar: Avatar = DEFAULT_AVATAR,
        factory: FactoryMember | None = None,
        home: bool = False,
    ) -> ManagedInstance:
        instance_id = uuid4()
        return ManagedInstance(
            instance_id=instance_id,
            path=self.instances_directory / str(instance_id),
            manifest_id=manifest_id,
            persona_name=persona_name,
            requested_revision=revision,
            source_revision=None,
            image_id=None,
            intended_state=IntendedState.STOPPED,
            runtime_id=str(instance_id),
            prepared=False,
            storage=(),
            avatar=avatar,
            factory=factory,
            home=home,
        )

    def _begin_create(
        self,
        record: ManagedInstance,
        model: str,
        config: Mapping[str, SetupValue],
        secrets: dict[str, str],
        template: InstanceTemplate | None = None,
        picks: Sequence[SkillName] = (),
        requester: UUID | None = None,
    ) -> LifecycleOperationResult:
        operation_id = uuid4()
        self.registry.begin_create(record, operation_id, requester)
        self._schedule(
            self._collecting_after(
                self._create(operation_id, record, model, config, secrets, template, picks)
            )
        )
        return LifecycleOperationResult(operation_id=operation_id, instance_id=record.instance_id)

    async def _create(
        self,
        operation_id: UUID,
        record: ManagedInstance,
        model: str,
        config: Mapping[str, SetupValue],
        secrets: dict[str, str],
        template: InstanceTemplate | None,
        picks: Sequence[SkillName],
    ) -> None:
        """Prepare the image, write the instance from its template or its image, copy in its
        picks, and publish it."""
        staging = record.path.with_name(f"{record.path.name}.creating")
        unmounted: tuple[StorageItem, ...] = ()
        try:
            self._record(operation_id, "image", "Preparing the selected image.")
            artifact = (
                await self._images.build(_selection(record, record.requested_revision))
            ).artifact
            if template is None:
                declared = await self._initialize_from_image(
                    operation_id, record, staging, artifact, model, config, secrets
                )
            else:
                declared = self._initialize_from_template(
                    operation_id, record, staging, template, model, config, secrets
                )
            if picks:
                self._record(operation_id, "picks", f"Copying {', '.join(picks)} from the library.")
                self.library.pick(inspect_instance(staging), picks, lock_hash(staging))
            self._record(
                operation_id, "publish", "Creating the container and listing the instance."
            )
            storage = self._storage(record.instance_id, record.path, declared.logins)
            if record.path.exists():
                raise FileExistsError(f"Instance directory already exists: {record.path}")
            staging.replace(record.path)
            unmounted = storage
            self._write_hub_url(record)
            self.registry.record_preparation(record.instance_id, artifact, storage)
            await self._runtime.create(
                InstanceSpec(
                    instance_id=record.runtime_id,
                    image=artifact.image_id,
                    storage=storage,
                    env=self._environment(record.path),
                    port=_INSTANCE_PORT,
                )
            )
            unmounted = ()
            self.registry.seed_logins(record.instance_id, [login.id for login in declared.logins])
            self.registry.mark_prepared(record.instance_id)
            self.registry.finish_operation(
                operation_id,
                OperationState.SUCCEEDED,
                "Instance prepared and stopped.",
            )
        except Exception as exc:
            try:
                await self._discard_failed_create(record, staging, unmounted)
            except Exception:
                _logger.exception("Could not clean up the failed create of %s", record.instance_id)
            self.registry.finish_operation(
                operation_id,
                OperationState.FAILED,
                self._redact(str(exc) or type(exc).__name__, secrets.values()),
            )

    async def _discard_failed_create(
        self,
        record: ManagedInstance,
        staging: Path,
        unmounted: tuple[StorageItem, ...],
    ) -> None:
        """Remove what a failed create wrote and no container mounts.

        A create can raise after Docker made the container, so the published directory goes
        only once the runtime reports no container there.
        """
        if staging.exists():
            shutil.rmtree(staging)
        if unmounted and (await self._runtime.status(record.runtime_id)).state == "absent":
            shutil.rmtree(record.path)
            self.registry.release_storage(record.instance_id, list(unmounted))

    async def _initialize_from_image(
        self,
        operation_id: UUID,
        record: ManagedInstance,
        staging: Path,
        artifact: ImageArtifact,
        model: str,
        config: Mapping[str, SetupValue],
        secrets: dict[str, str],
    ) -> DeclaredSetup:
        """Write a vanilla instance, with what the image just built declares."""
        self._record(
            operation_id,
            "validate",
            "Checking the setup values against what the image declares.",
        )
        description = await self._declared(artifact)
        errors = setup_errors(description, model=model, config=config, secrets=secrets)
        if errors:
            raise ValueError(
                "The image asks for other setup values now. "
                + " ".join(f"{name}: {message}" for name, message in errors.items())
            )
        self._record(
            operation_id,
            "initialize",
            "Writing the instance's configuration and secrets.",
        )
        configured = configuration(description, model=model, config=config)
        model = str(configured[MODEL_FIELD.name])
        init_instance(staging, model=model)
        self._write_configuration(staging, record.manifest_id, record.persona_name)
        _write_behavior_prompt(staging, configured)
        self._write_secrets(staging / ".env", self._instance_secrets(model, secrets))
        inspect_instance(staging)
        return description

    def _initialize_from_template(
        self,
        operation_id: UUID,
        record: ManagedInstance,
        staging: Path,
        template: InstanceTemplate,
        model: str,
        config: Mapping[str, SetupValue],
        secrets: dict[str, str],
    ) -> DeclaredSetup:
        """Write a factory's instance from its template, whose setup the creation checked."""
        self._record(
            operation_id,
            "initialize",
            f'Writing the instance from template "{template.name}", with its secrets.',
        )
        configured = configuration(template.setup, model=model, config=config)
        model = str(configured[MODEL_FIELD.name])
        init_from_template(
            staging,
            template.files,
            model=model,
            settings=manifest_settings(template.setup, configured),
        )
        self._write_configuration(staging, record.manifest_id, record.persona_name)
        _write_behavior_prompt(staging, configured)
        self._write_secrets(staging / ".env", self._instance_secrets(model, secrets))
        inspect_instance(staging)
        return template.setup

    def _hub_route(self, record: ManagedInstance) -> dict[str, str]:
        """The hub route, and the scopes an instance holds there, as its environment carries them:
        none unless the registry grants it a scope and the hub has an address."""
        scopes = self.instance_scopes(record.instance_id)
        if self._private_url is None or not scopes:
            return {}
        return {
            HUB_URL_VARIABLE: f"{self._private_url}/instances/{record.instance_id}/hub",
            HUB_SCOPES_VARIABLE: ",".join(sorted(scopes)),
        }

    def _write_hub_url(self, record: ManagedInstance) -> None:
        """Give an instance its hub route before a container reads its environment.

        An instance created before its route reaches the hub from its next container on.
        """
        if route := self._hub_route(record):
            self._replace_secrets(record.path, route)

    async def prepare_image(self, command: ImagePrepareCommand) -> ImagePrepareResult:
        """Build or reuse kinby's base image before any instance exists, and describe it."""
        operation_id = uuid4()
        opened = self.registry.begin_preparation(operation_id, "Preparation queued.")
        if opened == operation_id:
            self._schedule(self._prepare_image(operation_id))
        return ImagePrepareResult(operation_id=opened)

    async def _prepare_image(self, operation_id: UUID) -> None:
        try:
            self._record(operation_id, "image", "Building the image, or reusing the one prepared.")
            built = await self._images.build(ImageSelection(_PREPARED_REVISION))
            self.registry.succeed_step(
                operation_id,
                "Reused the image already prepared." if built.reused else "Built the image.",
            )
            self._record(operation_id, "describe", "Reading what the image declares.")
            description = await self._images.describe(built.artifact)
            self.registry.record_description(built.artifact.image_id, description)
            self.registry.succeed_step(operation_id, "Read what the image declares.")
        except asyncio.CancelledError:
            self._fail_if_unfinished(operation_id)
            raise
        except Exception as exc:
            self.registry.finish_operation(
                operation_id,
                OperationState.FAILED,
                str(exc) or type(exc).__name__,
            )
            return
        self.registry.finish_operation(operation_id, OperationState.SUCCEEDED, "Image prepared.")

    async def describe_image(self, command: ImageDescribeCommand) -> DeclaredSetup:
        """What the prepared image declares, as its preparation stored it. It never builds."""
        return self._prepared_description()

    async def _declared(self, artifact: ImageArtifact) -> DeclaredSetup:
        """What the image just built declares.

        A rebuilt image may declare other fields than the one the client was shown, so it is
        described again. Kept as the last description even when it was stored before, so a
        client that reads the image's fields again sees the ones this creation checked.
        """
        stored = self.registry.description(artifact.image_id)
        description = stored if stored is not None else await self._images.describe(artifact)
        self.registry.record_description(artifact.image_id, description)
        return description

    def _prepared_description(self) -> DeclaredSetup:
        description = self.registry.description()
        if description is None:
            raise ImageNotPrepared("The image has not been prepared. Prepare it first.")
        return description

    async def start(
        self, command: InstanceStartCommand, requester: UUID | None = None
    ) -> LifecycleOperationResult:
        record = self._active_instance(command.instance_id)
        operation_id = uuid4()
        opened = self.registry.begin_operation(
            operation_id,
            record.instance_id,
            OperationKind.START,
            "Start queued.",
            requester,
        )
        if opened == operation_id:
            self._schedule(self._start(operation_id, record.instance_id))
        return LifecycleOperationResult(
            operation_id=opened,
            instance_id=record.instance_id,
        )

    async def _start(self, operation_id: UUID, instance_id: UUID) -> None:
        try:
            await self._run_start(operation_id, instance_id, recreates=True)
        except asyncio.CancelledError:
            self._fail_if_unfinished(operation_id)
            raise

    async def _run_start(self, operation_id: UUID, instance_id: UUID, *, recreates: bool) -> None:
        """Start the container. Only a start the user asked for *recreates* a stopped one whose
        secrets were replaced: recovery starts no replacement (ADR 0051).
        """
        lock = self._locks.setdefault(instance_id, asyncio.Lock())
        async with lock:
            try:
                record = self._active_instance(instance_id)
            except ManagedInstanceNotFound as exc:
                self.registry.finish_operation(operation_id, OperationState.FAILED, str(exc))
                return
            secrets = self._environment(record.path).values()
            self.registry.set_intended_state(instance_id, IntendedState.RUNNING)
            try:
                record = self._active_instance(instance_id)
                if recreates and await self._stopped_with_replaced_secrets(record):
                    self._record(
                        operation_id,
                        "recreate",
                        "Recreating the container to apply the replaced secrets.",
                    )
                    await self._replace_container(operation_id, record)
                else:
                    self._record(operation_id, "start", "Starting selected image.")
                    await self._runtime.start(record.runtime_id)
            except Exception as exc:
                self.registry.finish_operation(
                    operation_id,
                    OperationState.FAILED,
                    self._redact(str(exc) or type(exc).__name__, secrets),
                )
                return
            self.registry.finish_operation(
                operation_id,
                OperationState.SUCCEEDED,
                "Instance started.",
            )

    async def _stopped_with_replaced_secrets(self, record: ManagedInstance) -> bool:
        """A stopped container still holds the secrets it was created with. A running one waits
        for an explicit recreation.
        """
        stopped = (await self._runtime.status(record.runtime_id)).state in _ALREADY_STOPPED
        return stopped and await self._secrets_replaced(record)

    async def _secrets_replaced(self, record: ManagedInstance) -> bool:
        """The instance's secrets are not the ones its container was created with.

        A container from before the hub labeled it reads as holding the current ones.
        """
        description = await self._runtime.describe(record.runtime_id)
        if description is None or description.secrets_digest is None:
            return False
        return description.secrets_digest != secrets_digest(self._environment(record.path))

    async def start_login(self, command: InstanceLoginStartCommand) -> LifecycleOperationResult:
        """Sign one of the instance's subscription logins in, or return the one running.

        A login takes no lifecycle lock, so the instance starts and stops meanwhile (ADR 0065).
        """
        record = self._active_instance(command.instance_id)
        login = self._login(record, command.login_id)
        operation_id = uuid4()
        opened = self.registry.begin_login(
            operation_id, record.instance_id, login.id, "Sign-in queued."
        )
        if opened == operation_id:
            self._schedule(self._sign_in(operation_id, record, login))
        return LifecycleOperationResult(operation_id=opened, instance_id=record.instance_id)

    def _login(self, record: ManagedInstance, login_id: str) -> SubscriptionLogin:
        description = self._declared_setup(record)
        logins = description.logins if description is not None else []
        for login in logins:
            if login.id == login_id:
                return login
        raise LoginNotFound(f'Instance "{record.instance_id}" declares no login "{login_id}".')

    async def _sign_in(
        self,
        operation_id: UUID,
        record: ManagedInstance,
        login: SubscriptionLogin,
    ) -> None:
        """Run the login's command in a setup container until it exits, or its code expires.

        The pattern is matched against everything printed so far, so a URL and a code on
        separate lines are found too. The first match is the one shown.
        """
        printed: list[str] = []
        prompt: LoginPrompt | None = None

        def watch(line: str) -> None:
            nonlocal prompt
            if not printed:
                self._record(
                    operation_id, "sign-in", f"Waiting for you to sign in to {login.label}."
                )
            printed.append(_TERMINAL_ESCAPE.sub("", line))
            if prompt is None and (found := pattern.search("\n".join(printed))) is not None:
                prompt = LoginPrompt(url=found["url"], code=found["code"])
                self.registry.record_prompt(operation_id, prompt)

        try:
            self._record(operation_id, "container", "Starting the setup container.")
            pattern = re.compile(login.prompt_pattern)
            volume = self._login_volume(record, login)
            if not record.image_id:
                raise ValueError("This instance has no recorded image to sign in from.")
            async with asyncio.timeout(LOGIN_SECONDS):
                exit_code = await self._runtime.run_setup(
                    SetupSpec(
                        instance_id=record.runtime_id,
                        image=record.image_id,
                        command=login.command,
                        volume=volume,
                    ),
                    watch,
                )
            self._record(operation_id, "verify", "Checking how the sign-in ended.")
        except TimeoutError:
            self.registry.finish_operation(
                operation_id,
                OperationState.FAILED,
                "The code expired. Sign in again for a new one.",
            )
            return
        except asyncio.CancelledError:
            self._fail_if_unfinished(operation_id)
            raise
        except Exception as exc:
            self.registry.finish_operation(
                operation_id, OperationState.FAILED, str(exc) or type(exc).__name__
            )
            return
        if exit_code == 0:
            self.registry.finish_operation(operation_id, OperationState.SUCCEEDED, "Signed in.")
            return
        last = next((line for line in reversed(printed) if line.strip()), "")
        self.registry.finish_operation(
            operation_id,
            OperationState.FAILED,
            f"The sign-in exited with code {exit_code}. {last}".strip(),
        )

    @staticmethod
    def _login_volume(record: ManagedInstance, login: SubscriptionLogin) -> StorageItem:
        """The volume the instance mounts where the login keeps its credentials.

        It is found by where it mounts, so a volume from before logins were declared, or
        one an adopted instance brought along, is the one the login signs in.
        """
        for item in record.storage:
            if item.kind is StorageKind.VOLUME and item.destination == login.volume:
                return item
        raise ValueError(f"This instance has no volume at {login.volume} for {login.label}.")

    async def set_secrets(self, command: InstanceSecretsSetCommand) -> LifecycleOperationResult:
        """Replace the named values in this instance's secrets, typed or taken from the hub secrets
        each field links. A typed value unlinks its field. No value travels back out."""
        record = self._active_instance(command.instance_id)
        secrets = {name: value.get_secret_value() for name, value in command.secrets.items()}
        operation_id = uuid4()
        self.registry.record_operation(
            operation_id,
            record.instance_id,
            OperationKind.SECRETS,
            "Secret replacement queued.",
        )
        self._schedule(
            self._set_secrets(operation_id, record.instance_id, secrets, command.hub_secrets)
        )
        return LifecycleOperationResult(
            operation_id=operation_id,
            instance_id=record.instance_id,
        )

    async def _set_secrets(
        self,
        operation_id: UUID,
        instance_id: UUID,
        secrets: dict[str, str],
        hub_secrets: dict[str, HubSecretName],
    ) -> None:
        try:
            # The lock serializes this write against every other lifecycle mutation.
            async with self._locks.setdefault(instance_id, asyncio.Lock()):
                try:
                    record = self._active_instance(instance_id)
                except ManagedInstanceNotFound as exc:
                    self.registry.finish_operation(operation_id, OperationState.FAILED, str(exc))
                    return
                self.registry.advance_operation(
                    operation_id,
                    "secrets",
                    "Replacing the instance's secrets.",
                )
                typed: dict[str, str] = {}
                links: dict[str, HubSecretName] = {}
                linked: dict[str, str] = {}
                try:
                    held = self._held_by(record, secrets | hub_secrets)
                    typed, links = held(secrets), held(hub_secrets)
                    linked = self._linked_values(typed, links)
                    values = typed | linked
                    self._validate_secret_names(values)
                    self._replace_secrets(record.path, values)
                except Exception as exc:
                    self._fail(
                        operation_id,
                        record,
                        str(exc) or type(exc).__name__,
                        [*secrets.values(), *linked.values()],
                    )
                    return
                self.registry.link_secrets(instance_id, links, unlinked=typed)
                self.registry.finish_operation(
                    operation_id,
                    OperationState.SUCCEEDED,
                    "Secrets replaced. Recreate the container to apply them.",
                )
        except asyncio.CancelledError:
            self._fail_if_unfinished(operation_id)
            raise

    def _linked_values(
        self, secrets: Mapping[str, str], hub_secrets: Mapping[str, HubSecretName]
    ) -> dict[str, str]:
        """The value of the hub secret each variable takes. Both maps are keyed by the variable
        the instance holds a value under, so the API key collides with its provider's variable."""
        kept = self.secrets.values()
        for variable, name in hub_secrets.items():
            if variable in secrets:
                raise ValueError(f"{variable} takes a typed value or a hub secret, not both.")
            if name not in kept:
                raise ValueError(f'The hub keeps no secret named "{name}".')
        return {variable: kept[name] for variable, name in hub_secrets.items()}

    @staticmethod
    def _held_by(
        record: ManagedInstance, fields: Mapping[str, str]
    ) -> Callable[[Mapping[str, str]], dict[str, str]]:
        """Renames a map by field name to the variables the instance holds them under. Only the
        API key needs the instance's model, so only then is its manifest read."""
        if API_KEY_FIELD.name not in fields:
            return dict
        model = inspect_instance(record.path).manifest.models.main
        return partial(held_secrets, model)

    async def list_hub_secrets(self, command: HubSecretListCommand) -> HubSecretListResult:
        """Every hub secret by name, with the instance fields linked to it. Never a value."""
        return HubSecretListResult(
            secrets=[
                HubSecret(name=name, links=self._links(name))
                for name in sorted(self.secrets.values())
            ]
        )

    def _links(self, name: HubSecretName) -> tuple[HubSecretLink, ...]:
        linked = self.registry.linked_to(name)
        return tuple(
            HubSecretLink(
                instance_id=record.instance_id,
                manifest_id=record.manifest_id,
                field=secret_field(self._model(record), variable),
            )
            for record in self.registry.managed_instances()
            if record.active
            for variable in linked.get(record.instance_id, [])
        )

    async def set_hub_secret(self, command: HubSecretSetCommand) -> HubSecretSetResult:
        """Create or replace one hub secret. A replacement reaches the instances linked to it.
        No value travels back out."""
        operation_id = uuid4()
        self.registry.record_operation(
            operation_id, None, OperationKind.HUB_SECRET, "Hub secret queued."
        )
        # Eager, so the write takes its place in the queue before a later delete can.
        self._schedule(self._set_hub_secret(operation_id, command), eager=True)
        return HubSecretSetResult(operation_id=operation_id)

    async def _set_hub_secret(self, operation_id: UUID, command: HubSecretSetCommand) -> None:
        name = command.name
        value = command.value.get_secret_value()
        try:
            # One hub secret write or delete at a time, in the order they were asked for.
            async with self._hub_secrets_lock:
                self.registry.advance_operation(operation_id, "secret", f'Saving "{name}".')
                previous = self.secrets.values().get(name)
                try:
                    self.secrets.save(name, value)
                    try:
                        rewritten, unlinked = await self._rewrite_links(
                            operation_id, name, previous, value
                        )
                    except Exception:
                        # A retry then compares each linked field against the previous value again.
                        if previous is None:
                            self.secrets.delete(name)
                        else:
                            self.secrets.save(name, previous)
                        raise
                except Exception as exc:
                    self.registry.finish_operation(
                        operation_id,
                        OperationState.FAILED,
                        self._redact(str(exc) or type(exc).__name__, [value, previous or ""]),
                    )
                    return
                for record in rewritten:
                    if record.instance_id in command.recreate:
                        await self._queue_recreation(operation_id, record)
                detail = f'Hub secret "{name}" saved.'
                if unlinked:
                    detail += (
                        f" Unlinked {', '.join(unlinked)}: each held another value, and keeps it."
                    )
                self.registry.finish_operation(operation_id, OperationState.SUCCEEDED, detail)
        except asyncio.CancelledError:
            self._fail_if_unfinished(operation_id)
            raise

    async def _rewrite_links(
        self,
        operation_id: UUID,
        name: HubSecretName,
        previous: str | None,
        value: str,
    ) -> tuple[Sequence[ManagedInstance], Sequence[str]]:
        """Write the new value into each linked field that still holds the previous one. A field
        that holds anything else was set on purpose, so it is unlinked and left as it is (ADR
        0095). A failed write puts the previous value back into each field already rewritten.

        Returns the instances that took the new value, and the names of those with a field
        unlinked.
        """
        rewritten: list[tuple[ManagedInstance, list[str]]] = []
        unlinked: list[str] = []
        try:
            for instance_id in self.registry.linked_to(name):
                # The lock serializes this write against every other lifecycle mutation.
                async with self._locks.setdefault(instance_id, asyncio.Lock()):
                    record = self.registry.instance(instance_id)
                    linked = self.registry.secret_links(instance_id)
                    variables = [variable for variable, hub in linked.items() if hub == name]
                    if record is None or not record.active or not variables:
                        continue
                    environment = self._environment(record.path)
                    held = [v for v in variables if environment.get(v) in (previous, value)]
                    drifted = [v for v in variables if v not in held]
                    if held:
                        self._record(
                            operation_id,
                            f"instance {record.manifest_id}",
                            f"Writing the new value into {record.manifest_id}'s secrets.",
                        )
                        self._replace_secrets(record.path, dict.fromkeys(held, value))
                        rewritten.append((record, held))
                    if drifted:
                        self.registry.link_secrets(instance_id, {}, unlinked=drifted)
                        unlinked.append(record.manifest_id)
        except Exception:
            if previous is not None:
                for record, held in rewritten:
                    async with self._locks.setdefault(record.instance_id, asyncio.Lock()):
                        self._replace_secrets(record.path, dict.fromkeys(held, previous))
            raise
        return [record for record, _ in rewritten], unlinked

    async def _queue_recreation(self, operation_id: UUID, record: ManagedInstance) -> None:
        step = f"recreate {record.manifest_id}"
        try:
            await self.recreate(InstanceRecreateCommand(instance_id=record.instance_id))
        except LifecycleOperationInFlight as exc:
            self._record(operation_id, step, f"{record.manifest_id} was not recreated. {exc}")
            return
        self._record(operation_id, step, f"Recreating {record.manifest_id}.")

    async def delete_hub_secret(self, command: HubSecretDeleteCommand) -> HubSecretDeleteResult:
        """Delete one hub secret and every link to it. Each instance keeps the value it holds."""
        async with self._hub_secrets_lock:
            unlinked = self._links(command.name)
            self.secrets.delete(command.name)
            self.registry.unlink_hub_secret(command.name)
        return HubSecretDeleteResult(unlinked=unlinked)

    async def picks(self, command: InstancePicksGetCommand) -> InstancePicksResult:
        return self.library.picks(self._active_instance(command.instance_id).path)

    async def set_picks(self, command: InstancePicksSetCommand) -> InstancePicksResult:
        """Change the instance's picks in place. A running instance reads them at its next turn."""
        record = self._active_instance(command.instance_id)
        return self.library.pick(inspect_instance(record.path), command.skills, command.hash)

    async def stop(
        self, command: InstanceStopCommand, requester: UUID | None = None
    ) -> LifecycleOperationResult:
        """Stop the instance gracefully, or escalate the stop already running to a force stop."""
        record = self._active_instance(command.instance_id)
        pending = self._stopping.get(record.instance_id)
        if pending is None:
            operation_id = uuid4()
            opened = self.registry.begin_operation(
                operation_id,
                record.instance_id,
                OperationKind.STOP,
                "Stop queued.",
                requester,
            )
            pending = PendingStop(opened, asyncio.Event())
            self._stopping[record.instance_id] = pending
            if opened == operation_id:
                self._schedule(self._stop(record.instance_id, pending))
        if command.force:
            pending.force.set()
        return LifecycleOperationResult(
            operation_id=pending.operation_id,
            instance_id=record.instance_id,
        )

    async def adopt_preview(
        self,
        command: InstanceAdoptPreviewCommand,
    ) -> InstanceAdoptPreviewResult:
        """Preview a handoff: what the hub would take over, and what stands in the way."""
        return await preflight(command, self.registry, self._runtime, self._control)

    async def adopt(self, command: InstanceAdoptCommand) -> LifecycleOperationResult:
        """Take an existing instance over, preserving its identity, storage and webhook URL."""
        preview = await preflight(command, self.registry, self._runtime, self._control)
        stopped = blocker(preview)
        if stopped is not None:
            raise AdoptionBlocked(f"This instance was not adopted. {stopped.detail}")
        record = ManagedInstance(
            instance_id=preview.instance_id,
            path=preview.path,
            manifest_id=preview.manifest_id,
            persona_name=preview.persona_name,
            requested_revision="",
            source_revision=None,
            image_id=preview.image_id,
            intended_state=await self._observed_state(preview.runtime_id),
            runtime_id=preview.runtime_id,
            prepared=False,
            storage=tuple(preview.storage),
        )
        operation_id = uuid4()
        try:
            opened = self.registry.begin_adoption(record, operation_id)
        except ValueError as exc:
            raise AdoptionBlocked(f"This instance was not adopted. {exc}") from exc
        if opened != operation_id:
            raise LifecycleOperationInFlight(
                f'Lifecycle operation "{opened}" is still running for this instance.'
            )
        self._schedule(self._adopt(operation_id, record, command))
        return LifecycleOperationResult(
            operation_id=operation_id,
            instance_id=record.instance_id,
        )

    async def _observed_state(self, runtime_id: str) -> IntendedState:
        """The state the instance is in now becomes the state the hub keeps it in."""
        running = (await self._runtime.status(runtime_id)).state not in _RUNTIME_STOPPED
        return IntendedState.RUNNING if running else IntendedState.STOPPED

    async def _adopt(
        self,
        operation_id: UUID,
        record: ManagedInstance,
        command: InstanceAdoptCommand,
    ) -> None:
        try:
            async with self._locks.setdefault(record.instance_id, asyncio.Lock()):
                try:
                    await self._hand_over(operation_id, record, command)
                except Exception as exc:
                    self._fail(operation_id, record, str(exc) or type(exc).__name__)
                    return
                self.registry.finish_operation(
                    operation_id,
                    OperationState.SUCCEEDED,
                    "The hub owns this instance.",
                )
        except asyncio.CancelledError:
            self._fail_if_unfinished(operation_id)
            raise

    async def _hand_over(
        self,
        operation_id: UUID,
        record: ManagedInstance,
        command: InstanceAdoptCommand,
    ) -> None:
        """Take the previous runtime down, then bring the same storage up under this hub.

        Ownership moves in one recorded step, once the previous runtime is down and
        before its container goes. From there on the data is this hub's, so an
        interruption leaves a missing container rather than an unclaimed instance.
        """
        await self._relinquish(operation_id, record, command)
        self._record(operation_id, "configure", "Writing this hub's control token beside it.")
        self._replace_secrets(record.path, {CONTROL_TOKEN_VARIABLE: new_control_token()})
        self._record(operation_id, "claim", "The previous runtime is down. The hub owns this now.")
        self.registry.mark_prepared(record.instance_id)
        self._record(operation_id, "remove", "Removing the previous container, keeping storage.")
        await self._runtime.remove(record.runtime_id)
        self._record(operation_id, "create", "Creating the container under this hub.")
        await self._runtime.create(
            InstanceSpec(
                instance_id=record.runtime_id,
                image=record.image_id or "",
                storage=record.storage,
                env=self._environment(record.path),
                port=_INSTANCE_PORT,
            )
        )
        if record.intended_state is IntendedState.RUNNING:
            self._record(operation_id, "start", "Starting the instance the hub now owns.")
            await self._runtime.start(record.runtime_id)
        if command.claim_signals:
            self._record(operation_id, "signals", "Keeping the established webhook URL here.")
            self.registry.set_signal_alias(record.instance_id)

    async def _relinquish(
        self,
        operation_id: UUID,
        record: ManagedInstance,
        command: InstanceAdoptCommand,
    ) -> None:
        """Stop the previous runtime. It drains when it can, and is interrupted when it cannot.

        The preflight already settled which of those the operator gets, so a graceful
        handoff never quietly becomes the interrupting one.
        """
        if (await self._runtime.status(record.runtime_id)).state in _RUNTIME_STOPPED:
            self._record(operation_id, "stopped", "The previous runtime was already stopped.")
            return
        self._record(operation_id, "probe", "Checking the previous runtime's endpoint.")
        endpoint = await self._probe_endpoint(record)
        probed = await self._control.probe(endpoint)
        if Capability.DRAIN in probed.capabilities:
            await self._take_down(record, PendingStop(operation_id, asyncio.Event()))
            return
        if not command.acknowledge_interrupting_stop:
            raise IncompatibleLifecycleEndpoint(
                "The previous runtime can no longer drain, and the interrupting stop was not "
                "acknowledged, so it was left running."
            )
        self._record(operation_id, "interrupt", "The previous runtime cannot drain. Stopping it.")
        await self._halt_container(record.runtime_id)

    async def recreate(
        self, command: InstanceRecreateCommand, requester: UUID | None = None
    ) -> LifecycleOperationResult:
        """Replace this instance's container from the image and secrets already recorded for it."""
        record = self._claimed(self._active_instance(command.instance_id))
        operation_id = uuid4()
        self.registry.record_operation(
            operation_id,
            record.instance_id,
            OperationKind.RECREATE,
            "Recreation queued.",
            requester,
        )
        self._schedule(self._recreate(operation_id, record.instance_id))
        return LifecycleOperationResult(
            operation_id=operation_id,
            instance_id=record.instance_id,
        )

    def _claimed(self, record: ManagedInstance) -> ManagedInstance:
        """One container change at a time: a client asks again once the running one finishes."""
        active = self.registry.active_operation(record.instance_id)
        if active is not None:
            raise LifecycleOperationInFlight(
                f'Lifecycle operation "{active}" is still running for this instance.'
            )
        return record

    def _not_signing_in(self, record: ManagedInstance) -> ManagedInstance:
        """No login is writing into this instance's storage while it is taken away."""
        running = self.registry.running_login(record.instance_id)
        if running is not None:
            _, login_id = running
            description = self._declared_setup(record)
            logins = description.logins if description is not None else []
            label = next((login.label for login in logins if login.id == login_id), login_id)
            raise LifecycleOperationInFlight(
                f"The {label} sign-in is still running for this instance. "
                "Finish the sign-in or let its code expire first."
            )
        return record

    async def _recreate(self, operation_id: UUID, instance_id: UUID) -> None:
        try:
            async with self._locks.setdefault(instance_id, asyncio.Lock()):
                record = self._active_instance(instance_id)
                try:
                    await self._replace_container(operation_id, record)
                except Exception as exc:
                    self._fail(operation_id, record, str(exc) or type(exc).__name__)
                    return
                self.registry.finish_operation(
                    operation_id,
                    OperationState.SUCCEEDED,
                    "Container recreated.",
                )
        except asyncio.CancelledError:
            self._fail_if_unfinished(operation_id)
            raise

    async def _replace_container(self, operation_id: UUID, record: ManagedInstance) -> None:
        """Take the existing container down the established way, then build its replacement."""
        self._revalidate(operation_id, record)
        if not record.image_id:
            raise ValueError("This instance has no recorded image to recreate its container from.")
        await self._remove_container(
            record, PendingStop(operation_id, asyncio.Event(), terminates_unreachable=True)
        )
        await self._create_container(operation_id, record, record.image_id)
        if record.intended_state is IntendedState.RUNNING:
            self._record(operation_id, "start", "Restoring the intended running state.")
            await self._runtime.start(record.runtime_id)

    def _revalidate(self, operation_id: UUID, record: ManagedInstance) -> Instance:
        """Revalidate what a container is built on. No revision is resolved here."""
        self._record(
            operation_id,
            "validate",
            f"Revalidating the retained configuration of instance {record.instance_id}.",
        )
        instance = inspect_instance(record.path)
        conflict = self.registry.conflicting_storage(record.instance_id, record.storage)
        if conflict is not None:
            raise ValueError(
                f'Storage source "{conflict.item.source}" is owned by instance {conflict.owner}, '
                "so nothing was changed."
            )
        return instance

    async def _remove_container(self, record: ManagedInstance, pending: PendingStop) -> None:
        """Drain and remove the container that is there, keeping every storage it owns."""
        if (await self._runtime.status(record.runtime_id)).state == "absent":
            return
        await self._stop_running(record, pending)
        self._record(pending.operation_id, "remove", "Removing the container, keeping its storage.")
        await self._runtime.remove(record.runtime_id)

    async def _create_container(
        self,
        operation_id: UUID,
        record: ManagedInstance,
        image: str,
    ) -> None:
        self._record(operation_id, "create", f"Creating the container from image {image}.")
        self._write_hub_url(record)
        await self._runtime.create(
            InstanceSpec(
                instance_id=record.runtime_id,
                image=image,
                storage=record.storage,
                env=self._environment(record.path),
                port=_INSTANCE_PORT,
            )
        )

    async def update(self, command: InstanceUpdateCommand) -> LifecycleOperationResult:
        """Move this instance onto the image a selected revision prepares, and a factory's
        instance onto the files its template supplies now."""
        record = self._claimed(self._active_instance(command.instance_id))
        selection = _selection(record, command.revision)
        operation_id = uuid4()
        self.registry.record_operation(
            operation_id,
            record.instance_id,
            OperationKind.UPDATE,
            "Update queued.",
        )
        self._schedule(
            self._collecting_after(self._update(operation_id, record.instance_id, selection))
        )
        return LifecycleOperationResult(
            operation_id=operation_id,
            instance_id=record.instance_id,
        )

    async def _update(
        self,
        operation_id: UUID,
        instance_id: UUID,
        selection: ImageSelection,
    ) -> None:
        try:
            # The lock serializes the replacement against every other lifecycle mutation.
            async with self._locks.setdefault(instance_id, asyncio.Lock()):
                record = self._active_instance(instance_id)
                try:
                    detail = await self._replace_image(operation_id, record, selection)
                except Exception as exc:
                    self._fail(operation_id, record, str(exc) or type(exc).__name__)
                else:
                    self.registry.finish_operation(operation_id, OperationState.SUCCEEDED, detail)
        except asyncio.CancelledError:
            self._fail_if_unfinished(operation_id)
            raise
        await self._raise_notices()

    async def _replace_image(
        self,
        operation_id: UUID,
        record: ManagedInstance,
        selection: ImageSelection,
    ) -> str:
        """Prepare the candidate before anything moves, then replace the container with it.

        A failure before the replacement leaves the instance running on its selected
        image. A failure after it stays a failure: the new image may already have
        changed this instance's data, so nothing here starts an older one against it.
        """
        revision = selection.revision
        self._revalidate(operation_id, record)
        member = record.factory
        template = None if member is None else await self._served_template(member)
        self._record(operation_id, "image", f"Preparing the image {revision} selects.")
        artifact = (await self._images.build(selection)).artifact
        self._record(
            operation_id,
            "replace",
            f"Replacing image {record.image_id} with {artifact.image_id}. "
            "The previous image stays recorded for an explicit recovery.",
        )
        self.registry.stage_candidate(record.instance_id, revision, artifact)
        await self._remove_container(record, PendingStop(operation_id, asyncio.Event()))
        baseline = None if member is None else member.baseline
        taken: tuple[SkillName, ...] = ()
        if template is not None and baseline is not None:
            baseline, taken = self.library.take_supplied(record.path, baseline, template.files)
        if taken or read_lock(record.path).skills:
            self._record(operation_id, "picks", "Bringing the picks up to the library's.")
            detail = self.library.sync(record.path).detail
            if taken:
                detail = f"Made {', '.join(taken)} picks. {detail}"
            self.registry.succeed_step(operation_id, detail)
        if template is not None:
            self._record(
                operation_id,
                "template",
                f'Bringing the files of template "{template.name}" up to the factory\'s.',
            )
            sync = sync_template(template, record.path, baseline)
            self.registry.record_template(record.instance_id, template.setup, sync.baseline)
            self.registry.succeed_step(operation_id, sync.detail)
        await self._create_container(operation_id, record, artifact.image_id)
        self.registry.record_selection(record.instance_id, revision, artifact)
        running = record.intended_state is IntendedState.RUNNING
        if running:
            self._record(operation_id, "start", "Starting the replacement.")
            await self._runtime.start(record.runtime_id)
            self._record(
                operation_id, "ready", "Waiting for the replacement to report itself ready."
            )
            await self._observe_ready(record)
        return "Instance updated." if running else "Instance updated and left stopped."

    async def _served_template(self, member: FactoryMember) -> InstanceTemplate:
        """The template the hub serves now for a factory's instance, from a factory that passes
        the check."""
        template = await self.factories.template(member.factory, member.name)
        if template is None:
            raise ValueError(
                f'Factory "{member.factory}" no longer has template "{member.name}", '
                "so nothing was changed."
            )
        return template

    async def remove(self, command: InstanceRemoveCommand) -> LifecycleOperationResult:
        """Drain and remove the container. The data and the record stay for a restoration."""
        record = self._active_instance(command.instance_id)
        if record.home:
            raise HomeInstanceKept(
                f"{record.persona_name or record.manifest_id} is the home instance, "
                "so the hub never removes it."
            )
        record = self._not_signing_in(self._claimed(record))
        operation_id = uuid4()
        self.registry.record_operation(
            operation_id,
            record.instance_id,
            OperationKind.REMOVE,
            "Removal queued.",
        )
        # A force stop escalates this drain the way it escalates a stop's.
        pending = PendingStop(operation_id, asyncio.Event())
        self._stopping[record.instance_id] = pending
        self._schedule(self._remove(record.instance_id, pending))
        return LifecycleOperationResult(
            operation_id=operation_id,
            instance_id=record.instance_id,
        )

    async def _remove(self, instance_id: UUID, pending: PendingStop) -> None:
        operation_id = pending.operation_id
        try:
            try:
                async with self._locks.setdefault(instance_id, asyncio.Lock()):
                    record = self._active_instance(instance_id)
                    try:
                        await self._take_away(record, pending)
                    except Exception as exc:
                        self._fail(operation_id, record, str(exc) or type(exc).__name__)
                        return
                    self.registry.finish_operation(
                        operation_id,
                        OperationState.SUCCEEDED,
                        "Instance removed. Its data and record are retained.",
                    )
            except asyncio.CancelledError:
                self._fail_if_unfinished(operation_id)
                raise
        finally:
            self._stopping.pop(instance_id, None)

    async def _take_away(self, record: ManagedInstance, pending: PendingStop) -> None:
        """Remove only a container this hub labeled, and record the removal once it is gone.

        The record says removed only after the container is, so an interruption before
        that leaves an instance the user can still see and remove again.
        """
        described = await self._runtime.describe(record.runtime_id)
        if described is not None and described.owner is not ContainerOwner.HUB:
            raise ValueError(
                f"{previous_manager(described)} The hub removes only a container it labeled, "
                "so the instance was left as it is."
            )
        self.registry.set_intended_state(record.instance_id, IntendedState.STOPPED)
        await self._remove_container(record, pending)
        self.registry.set_intended_state(record.instance_id, IntendedState.REMOVED)
        self.registry.link_secrets(
            record.instance_id, {}, unlinked=self.registry.secret_links(record.instance_id)
        )

    async def restore(self, command: InstanceRestoreCommand) -> LifecycleOperationResult:
        """Bring a removed instance back from its retained record, and leave it stopped."""
        record = self._claimed(self._removed_instance(command.instance_id))
        if self._name_taken(record.manifest_id):
            raise NameTaken(
                f'An instance named "{record.manifest_id}" exists already. '
                "Remove it before restoring this one."
            )
        operation_id = uuid4()
        self.registry.record_operation(
            operation_id,
            record.instance_id,
            OperationKind.RESTORE,
            "Restoration queued.",
        )
        self._schedule(self._restore(operation_id, record.instance_id))
        return LifecycleOperationResult(
            operation_id=operation_id,
            instance_id=record.instance_id,
        )

    async def _restore(self, operation_id: UUID, instance_id: UUID) -> None:
        try:
            async with self._locks.setdefault(instance_id, asyncio.Lock()):
                record = self._removed_instance(instance_id)
                try:
                    await self._bring_back(operation_id, record)
                except Exception as exc:
                    self._fail(operation_id, record, str(exc) or type(exc).__name__)
                    return
                self.registry.finish_operation(
                    operation_id,
                    OperationState.SUCCEEDED,
                    "Instance restored and left stopped. Start it to run it.",
                )
        except asyncio.CancelledError:
            self._fail_if_unfinished(operation_id)
            raise

    async def _bring_back(self, operation_id: UUID, record: ManagedInstance) -> None:
        """Check that everything the record retains is still there, then create the container.

        Nothing missing is replaced. Docker would mount a new, empty volume under a
        retained volume's name, and another image would be an update.
        """
        manifest_id = self._revalidate(operation_id, record).manifest.id
        if manifest_id != record.manifest_id:
            raise ValueError(
                f'{record.path} now holds instance "{manifest_id}", not "{record.manifest_id}".'
            )
        image = record.image_id
        if not image or not await self._runtime.has_image(image):
            raise ValueError(
                f"The selected image {image} is no longer available, and restoration selects "
                "no other."
            )
        for item in record.storage:
            if item.kind is StorageKind.VOLUME and not await self._runtime.has_volume(item.source):
                raise ValueError(
                    f'Named volume "{item.source}" is missing, and restoration does not replace '
                    "it with an empty one."
                )
        if await self._runtime.describe(record.runtime_id) is not None:
            raise ValueError(
                f'A container "{record.runtime_id}" already exists, so the instance was not '
                "restored over it."
            )
        await self._create_container(operation_id, record, image)
        self.registry.set_intended_state(record.instance_id, IntendedState.STOPPED)

    async def delete_preview(
        self,
        command: InstanceDeletePreviewCommand,
    ) -> InstanceDeletePreviewResult:
        """Name exactly what a permanent deletion would take. Nothing is touched here."""
        return self._deletion_targets(self._removed_instance(command.instance_id))

    async def delete(self, command: InstanceDeleteCommand) -> LifecycleOperationResult:
        """Permanently delete the previewed directories and named volumes of a removed instance."""
        record = self._not_signing_in(self._claimed(self._removed_instance(command.instance_id)))
        operation_id = uuid4()
        self.registry.record_operation(
            operation_id,
            record.instance_id,
            OperationKind.DELETE,
            "Deletion queued.",
        )
        self._schedule(self._collecting_after(self._delete(operation_id, command)))
        return LifecycleOperationResult(
            operation_id=operation_id,
            instance_id=record.instance_id,
        )

    async def _delete(self, operation_id: UUID, command: InstanceDeleteCommand) -> None:
        try:
            # The lock serializes the deletion against restoration and every other mutation.
            async with self._locks.setdefault(command.instance_id, asyncio.Lock()):
                try:
                    record = self._removed_instance(command.instance_id)
                except ManagedInstanceNotFound as exc:
                    self.registry.finish_operation(operation_id, OperationState.FAILED, str(exc))
                    return
                try:
                    targets = await self._erase(operation_id, record, command)
                except Exception as exc:
                    self._fail(operation_id, record, str(exc) or type(exc).__name__)
                    return
                detail = "Instance deleted. Its directories and named volumes are gone."
                for path in targets.retained:
                    detail += f" {path} stays on the Docker host."
                self.registry.finish_operation(operation_id, OperationState.SUCCEEDED, detail)
        except asyncio.CancelledError:
            self._fail_if_unfinished(operation_id)
            raise

    async def _erase(
        self,
        operation_id: UUID,
        record: ManagedInstance,
        command: InstanceDeleteCommand,
    ) -> InstanceDeletePreviewResult:
        """Revalidate the preview, then delete each target and release it from the inventory.

        A target leaves the inventory only once it is gone. A retry therefore picks up
        a target that failed, and never reaches for one that was already deleted, even
        when something unrelated appears in its place later.
        """
        self._record(
            operation_id,
            "validate",
            "Revalidating the retained inventory against the preview.",
        )
        targets = self._deletion_targets(record)
        if (targets.directories, targets.volumes) != (command.directories, command.volumes):
            raise ValueError(
                "The deletion targets changed since the preview, so nothing was deleted. "
                "Preview the deletion again."
            )
        shared = self.registry.shared_storage(record.instance_id, record.storage)
        if shared is not None:
            raise ValueError(
                f'Storage "{shared.item.source}" is shared with instance {shared.owner}, '
                "so nothing was deleted."
            )
        if await self._runtime.describe(record.runtime_id) is not None:
            raise ValueError(
                f'A container "{record.runtime_id}" still exists and may mount this storage, '
                "so nothing was deleted."
            )
        owned = [item for item in record.storage if item.writable]
        gone: list[Path] = []
        for directory in targets.directories:
            # It went and left the inventory with its parent, so another instance may own it now.
            if any(directory.is_relative_to(parent) for parent in gone):
                continue
            entries = [
                item
                for item in owned
                if item.kind is StorageKind.BIND
                and (seen := self._visible_directory(record, item)) is not None
                and seen.is_relative_to(directory)
            ]
            self._record(operation_id, f"directory {directory}", f"Deleting {directory}.")
            await asyncio.to_thread(_delete_directory, directory)
            self.registry.release_storage(record.instance_id, entries)
            gone.append(directory)
        for volume in targets.volumes:
            entries = [
                item for item in owned if item.kind is StorageKind.VOLUME and item.source == volume
            ]
            self._record(operation_id, f"volume {volume}", f'Deleting named volume "{volume}".')
            await self._runtime.delete_volume(volume)
            self.registry.release_storage(record.instance_id, entries)
        self.registry.mark_deleted(record.instance_id)
        return targets

    def _deletion_targets(self, record: ManagedInstance) -> InstanceDeletePreviewResult:
        """The writable storage the retained inventory records, and nothing it does not.

        A read-only mount was never reserved against other instances, so it is not
        this instance's to delete. Neither is anything the manifest merely references.
        """
        owned = [item for item in record.storage if item.writable]
        binds = [
            (item, self._visible_directory(record, item))
            for item in owned
            if item.kind is StorageKind.BIND
        ]
        return InstanceDeletePreviewResult(
            instance_id=record.instance_id,
            directories=list(
                dict.fromkeys(directory for _, directory in binds if directory is not None)
            ),
            volumes=list(
                dict.fromkeys(item.source for item in owned if item.kind is StorageKind.VOLUME)
            ),
            retained=list(
                dict.fromkeys(item.source for item, directory in binds if directory is None)
            ),
        )

    def _visible_directory(self, record: ManagedInstance, item: StorageItem) -> Path | None:
        if item.destination == INSTANCE_MOUNT:
            return record.path.resolve()
        mount = _instance_mount(record.storage)
        seen_from = ((Path(mount.source), record.path.resolve()),) if mount is not None else ()
        source = Path(item.source)
        for host, seen in (*seen_from, (self._docker_host_directory, self.directory)):
            if source.is_relative_to(host):
                mapped = seen / source.relative_to(host)
                # The hub would follow a symlink in its own namespace, not where Docker mounted it.
                return mapped if mapped.resolve() == mapped else None
        return None

    async def _observe_ready(self, record: ManagedInstance) -> None:
        """A container that runs is not an instance that booted.

        The replacement answers its own lifecycle endpoint before the update reports
        success, so an image that fails against the data it now owns fails the update.
        """
        try:
            async with asyncio.timeout(READY_OBSERVE_SECONDS):
                while not await self._ready(record):
                    await asyncio.sleep(_POLL_SECONDS)
        except TimeoutError as exc:
            raise ReplacementNotReady(
                "The replacement did not answer its lifecycle endpoint within "
                f"{READY_OBSERVE_SECONDS} seconds."
            ) from exc

    async def _ready(self, record: ManagedInstance) -> bool:
        """An instance still booting has no endpoint yet, and that is not a failure."""
        status = await self._runtime.status(record.runtime_id)
        if status.state in _RUNTIME_STOPPED:
            raise ReplacementNotReady(
                "The replacement container stopped before the instance answered: "
                f"{status.detail or status.state}."
            )
        if status.healthy is False and status.state == "running":
            raise ReplacementNotReady("The replacement container reports itself unhealthy.")
        if status.state != "running":
            return False
        try:
            await self._control.probe(await self._endpoint(record))
        except ControlUnreachable:
            return False
        return True

    async def _stop(self, instance_id: UUID, pending: PendingStop) -> None:
        operation_id = pending.operation_id
        try:
            try:
                # The lock serializes stops against other mutations; escalation never takes it.
                async with self._locks.setdefault(instance_id, asyncio.Lock()):
                    record = self._active_instance(instance_id)
                    self.registry.set_intended_state(instance_id, IntendedState.STOPPED)
                    try:
                        detail = await self._stop_running(record, pending)
                    except Exception as exc:
                        self._fail(operation_id, record, str(exc) or type(exc).__name__)
                        return
                    self.registry.finish_operation(operation_id, OperationState.SUCCEEDED, detail)
            except asyncio.CancelledError:
                self._fail_if_unfinished(operation_id)
                raise
        finally:
            self._stopping.pop(instance_id, None)

    async def _stop_running(self, record: ManagedInstance, pending: PendingStop) -> str:
        """Drain the instance through its own runtime, then take the container down.

        A container that is not running has no process to drain, and one whose endpoint
        never answers is terminated only when forced (ADR 0066).
        """
        operation_id = pending.operation_id
        status = await self._runtime.status(record.runtime_id)
        if status.state in _ALREADY_STOPPED:
            return "Instance was already stopped."
        # A booting instance reports starting too, but only a container Docker runs has an address.
        if await self._runtime.address(record.runtime_id) is None:
            self._record(
                operation_id,
                "container",
                f"The container is {status.detail or status.state}, so nothing drains. "
                "Stopping it.",
            )
            await self._halt_container(record.runtime_id)
            return "Instance stopped."
        self._record(operation_id, "probe", "Checking the instance's lifecycle endpoint.")
        try:
            probed = await self._control.probe(await self._endpoint(record))
        except ControlUnreachable as exc:
            if not (pending.force.is_set() or pending.terminates_unreachable):
                raise ControlUnreachable(
                    "The instance's lifecycle endpoint cannot be reached, so it was left "
                    f"running. Force stop it to terminate the container. {exc}"
                ) from exc
            await self._terminate(
                operation_id,
                record.runtime_id,
                "The lifecycle endpoint cannot be reached. Terminating the container.",
            )
            return "Instance stopped."
        if Capability.DRAIN not in probed.capabilities:
            raise IncompatibleLifecycleEndpoint(
                "The instance's lifecycle endpoint cannot drain, so it was left running. "
                "Update the instance before stopping it."
            )
        await self._take_down(record, pending)
        return "Instance stopped."

    async def _take_down(self, record: ManagedInstance, pending: PendingStop) -> None:
        """Wait for the instance's own drain, then take its container down and watch it go."""
        operation_id = pending.operation_id
        state = await self._drain(operation_id, await self._endpoint(record), pending)
        await self._terminate(
            operation_id,
            record.runtime_id,
            f"Instance {state.value}. Stopping the container."
            if state is not None
            else "The instance did not report its drain. Terminating the container.",
        )

    async def _terminate(self, operation_id: UUID, runtime_id: str, result: str) -> None:
        """Record why the container goes down, then take it down and watch it go."""
        self._record(operation_id, "result", result)
        await self._halt_container(runtime_id)
        self._record(operation_id, "container", "Stopping the container.")

    async def _halt_container(self, runtime_id: str) -> None:
        """A recorded interruption is not evidence that the process exited. Watch it stop."""
        await self._runtime.stop(runtime_id, grace_seconds=STOP_GRACE_SECONDS)
        await self._observe_stop(runtime_id)

    async def _drain(
        self,
        operation_id: UUID,
        endpoint: ControlEndpoint,
        pending: PendingStop,
    ) -> DrainState | None:
        """Wait for the drain, escalating on request. None when a forced instance never answered."""
        forced = pending.force.is_set()
        self._record(
            operation_id,
            "drain",
            "Interrupting active work." if forced else "Draining accepted work.",
        )
        draining = asyncio.create_task(self._call_drain(operation_id, endpoint, pending))
        escalation = asyncio.create_task(pending.force.wait())
        interrupting: asyncio.Task[DrainState] | None = None
        try:
            if not forced:
                await asyncio.wait((draining, escalation), return_when=asyncio.FIRST_COMPLETED)
                if draining.done():
                    return draining.result()
                self._record(
                    operation_id,
                    "force",
                    "Force stop requested. Interrupting active work.",
                )
                interrupting = asyncio.create_task(
                    self._call_drain(operation_id, endpoint, pending)
                )
            try:
                return await asyncio.wait_for(
                    asyncio.shield(draining),
                    timeout=FORCE_ANSWER_SECONDS,
                )
            except TimeoutError:
                return None
        finally:
            for task in (escalation, draining, interrupting):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(task for task in (escalation, draining, interrupting) if task is not None),
                return_exceptions=True,
            )

    async def _call_drain(
        self,
        operation_id: UUID,
        endpoint: ControlEndpoint,
        pending: PendingStop,
    ) -> DrainState:
        """Call the drain again when the socket drops. The instance keeps draining either way."""
        reported = False
        while True:
            try:
                return await self._control.drain(endpoint, force=pending.force.is_set())
            except ControlConnectionLost:
                if not reported:
                    self._record(
                        operation_id,
                        "reconnect",
                        "The control socket closed. Waiting for the drain again.",
                    )
                    reported = True
                await asyncio.sleep(_DRAIN_RETRY_SECONDS)

    async def _observe_stop(self, runtime_id: str) -> None:
        """Report stopped only once the container itself is: an interrupt alone is not enough."""
        try:
            async with asyncio.timeout(_STOP_OBSERVE_SECONDS):
                while (await self._runtime.status(runtime_id)).state not in _RUNTIME_STOPPED:
                    await asyncio.sleep(_POLL_SECONDS)
        except TimeoutError as exc:
            raise TimeoutError(
                f"The container was still running {_STOP_OBSERVE_SECONDS} seconds "
                "after it was told to stop."
            ) from exc

    async def _probe_endpoint(self, record: ManagedInstance) -> ControlEndpoint:
        """Where the instance answers, with whatever token it holds: a health probe needs none."""
        address = await self._runtime.address(record.runtime_id)
        if address is None:
            raise ControlUnreachable("The instance's lifecycle endpoint cannot be reached.")
        token = self._environment(record.path).get(CONTROL_TOKEN_VARIABLE, "")
        return ControlEndpoint(address=address, token=ControlToken(token))

    async def _endpoint(self, record: ManagedInstance) -> ControlEndpoint:
        """The same endpoint, for the calls a token opens."""
        endpoint = await self._probe_endpoint(record)
        if not endpoint.token:
            raise ControlUnreachable("The instance's lifecycle endpoint cannot be reached.")
        return endpoint

    def _record(self, operation_id: UUID, step: str, detail: str) -> None:
        self.registry.advance_operation(operation_id, step, detail)

    def _fail(
        self,
        operation_id: UUID,
        record: ManagedInstance,
        detail: str,
        submitted: Collection[str] = (),
    ) -> None:
        """Fail with every secret cut from the detail: the stored ones and the submitted ones."""
        self.registry.finish_operation(
            operation_id,
            OperationState.FAILED,
            self._redact(detail, [*submitted, *self._environment(record.path).values()]),
        )

    def _fail_if_unfinished(self, operation_id: UUID) -> None:
        current = self.registry.operation(operation_id)
        unfinished = {OperationState.PENDING, OperationState.RUNNING}
        if current is not None and current.state in unfinished:
            self.registry.finish_operation(
                operation_id,
                OperationState.FAILED,
                _INTERRUPTED_OPERATION,
            )

    async def recover(self) -> LifecycleRecovery:
        """Reconcile every managed instance against what the container runtime still has, pick
        factory runs, pending notifications and unapplied replies up where the previous process
        left them, and create a home instance when the hub holds none.

        Setup containers from a login the previous process did not finish go first, so
        they are not still holding a login volume when recovery looks at the instances.
        The images nothing refers to go after it, in the background.
        """
        await self._runtime.remove_setup_containers()
        recovery = await recover_lifecycle(self.registry, self._runtime, self._restore_start)
        self._schedule(self._collect_images())
        self.runs.resume()
        self.outbox.resume()
        self.replies.resume()
        await self._raise_notices()
        if not self._home_lost(recovery):
            return recovery
        return replace(recovery, home=self._create_home())

    def _home_lost(self, recovery: LifecycleRecovery) -> bool:
        """Whether no instance is marked home, or recovery found the marked one's creation never
        reached its container. One recovery could not look at stays marked for a later start."""
        home = next((record for record in self.registry.managed_instances() if record.home), None)
        if home is None:
            return True
        return any(
            recovered.instance_id == home.instance_id
            and recovered.state is RecoveredState.INCOMPLETE
            for recovered in recovery.instances
        )

    def _create_home(self) -> LifecycleOperationResult:
        """Create a vanilla instance and mark it home, through the ordinary creation. Its API
        key stays pending setup, and the image is built if no preparation built it yet. It picks
        each home skill the library still holds."""
        record = self._new_record(_HOME_NAME, persona_name=_HOME_PERSONA_NAME, home=True)
        picks = [name for name in HOME_SKILLS if self.library.refusal([name], ()) is None]
        return self._begin_create(record, PLACEHOLDER_MODEL, {}, {}, picks=picks)

    async def _restore_start(self, instance_id: UUID) -> OperationState:
        """Start one instance again under its own lifecycle operation, so the attempt is visible."""
        operation_id = self.registry.begin_operation(
            uuid4(),
            instance_id,
            OperationKind.START,
            "Restoring the intended running state.",
        )
        await self._run_start(operation_id, instance_id, recreates=False)
        finished = self.registry.operation(operation_id)
        return finished.state if finished is not None else OperationState.FAILED

    async def list(self, command: InstanceListCommand) -> InstanceListResult:
        hub_revision = await self._images.resolve(_PREPARED_REVISION)
        records = self.registry.listed_instances(removed=command.removed)
        served = await self._served_templates(records)
        return InstanceListResult(
            instances=[await self._summary(record, hub_revision, served) for record in records]
        )

    async def _served_templates(
        self, records: Iterable[ManagedInstance]
    ) -> dict[tuple[FactoryName, InstanceName], InstanceTemplate]:
        """Each template the factories of *records* serve now, by factory and name.

        A factory the hub no longer serves, or one that fails its check, serves none.
        """
        served: dict[tuple[FactoryName, InstanceName], InstanceTemplate] = {}
        for factory in {record.factory.factory for record in records if record.factory}:
            try:
                templates = await self.factories.templates(factory)
            except FactoryNotFound, FactoryCheckFailed:
                continue
            served |= {(factory, template.name): template for template in templates}
        return served

    async def _summary(
        self,
        record: ManagedInstance,
        hub_revision: str,
        served: Mapping[tuple[FactoryName, InstanceName], InstanceTemplate],
    ) -> InstanceSummary:
        observed = await self._observed(record)
        return InstanceSummary(
            instance_id=record.instance_id,
            manifest_id=record.manifest_id,
            persona_name=record.persona_name,
            source_revision=record.source_revision or "",
            image_id=record.image_id or "",
            intended_state=record.intended_state,
            process=observed.process,
            detail=observed.detail,
            runtime_id=record.runtime_id,
            storage=list(record.storage),
            avatar=record.avatar,
            setup_pending=self._setup(record).pending,
            home=record.home,
            factory_role=(
                FactoryRole(factory=record.factory.factory, name=record.factory.name)
                if record.factory is not None
                else None
            ),
            notices=[*self._notices_of(record, hub_revision, served).values()],
            needs_human=self.runs.needing_human(record),
        )

    def _notices_of(
        self,
        record: ManagedInstance,
        hub_revision: str,
        served: Mapping[tuple[FactoryName, InstanceName], InstanceTemplate],
    ) -> dict[str, InstanceNotice]:
        return _notices(
            record, hub_revision, served, self._hub_route(record), self._environment(record.path)
        )

    async def _raise_notices(self) -> None:
        """Raise a notification for each instance notice, once per instance, kind and value."""
        try:
            hub_revision = await self._images.resolve(_PREPARED_REVISION)
            records = self.registry.listed_instances()
            served = await self._served_templates(records)
        except Exception:
            _logger.exception("Could not look for instance notices")
            return
        for record in records:
            for key, notice in self._notices_of(record, hub_revision, served).items():
                self.outbox.notify(
                    record.instance_id,
                    key,
                    source="Instance notice",
                    title=_NOTICE_TITLES[notice.code],
                    text=notice.message,
                    page=f"/instances/{record.instance_id}",
                )

    async def status(self, command: InstanceStatusCommand) -> InstanceStatusResult:
        record = self._active_instance(command.instance_id)
        observed = await self._observed(record)
        return InstanceStatusResult(
            instance_id=record.instance_id,
            process=observed.process,
            readiness=observed.readiness,
            setup=self._setup(record),
            home=record.home,
            detail=observed.detail,
            active_operation_id=self.registry.active_operation(record.instance_id),
            recreate_reasons=[*await self._recreate_reasons(record, observed.process)],
            repository_commands=[*self.runs.held_commands(record)],
        )

    async def _recreate_reasons(
        self, record: ManagedInstance, process: ProcessState
    ) -> tuple[RecreateReason, ...]:
        """Secrets replaced since the container was created."""
        if process in {ProcessState.MISSING, ProcessState.UNAVAILABLE}:
            return ()
        return (RecreateReason.SECRETS,) if await self._secrets_replaced(record) else ()

    async def _observed(self, record: ManagedInstance) -> ObservedProcess:
        """Read the runtime once. A runtime that cannot be read reports the instance unavailable."""
        secrets = self._environment(record.path).values()
        try:
            status = await self._runtime.status(record.runtime_id)
        except Exception as exc:
            return ObservedProcess(
                ProcessState.UNAVAILABLE,
                Readiness.UNKNOWN,
                self._redact(str(exc) or type(exc).__name__, secrets),
            )
        process, readiness = self._status(status)
        return ObservedProcess(process, readiness, self._redact(status.detail, secrets))

    def _declared_setup(self, record: ManagedInstance) -> DeclaredSetup | None:
        """What the instance asks for: its template's as created, or its image's."""
        if record.factory is not None:
            return record.factory.setup
        return self.registry.description()

    @staticmethod
    def _model(record: ManagedInstance) -> ModelName | None:
        try:
            return inspect_instance(record.path).manifest.models.main
        except ManifestError:
            return None

    def _setup(self, record: ManagedInstance) -> InstanceSetup:
        """The logins and secrets the instance's stored descriptor declares, how they stand, and
        the lists of repository commands it is missing."""
        description = self._declared_setup(record)
        declared = description.logins if description is not None else []
        running = {
            login.id: running[0]
            for login in declared
            if (running := self.registry.running_login(record.instance_id, login.id))
        }
        return instance_setup(
            description,
            logins=self.registry.login_states(record.instance_id),
            running=running,
            secrets=self._environment(record.path),
            links=self.registry.secret_links(record.instance_id),
            model=self._model(record),
            missing_commands=self.runs.missing_commands(record),
        )

    async def logs(self, command: InstanceLogsCommand) -> InstanceLogsResult:
        record = self._active_instance(command.instance_id)
        chunks = [chunk async for chunk in self._runtime.logs(record.runtime_id, tail=command.tail)]
        text = b"".join(chunks).decode(errors="replace")
        return InstanceLogsResult(
            instance_id=record.instance_id,
            text=self._redact(text, self._environment(record.path).values()),
        )

    async def endpoint(self, instance_id: UUID) -> InstanceEndpoint | InstanceUnreachable:
        """Answer where a public route may reach one instance, and with which control token."""
        record = self.registry.instance(instance_id)
        if record is None or not record.active:
            return InstanceUnreachable.MISSING
        try:
            address = await self._runtime.address(record.runtime_id)
        except Exception:
            return InstanceUnreachable.UNAVAILABLE
        token = self._environment(record.path).get(CONTROL_TOKEN_VARIABLE)
        if address is None or not token:
            return InstanceUnreachable.UNAVAILABLE
        return InstanceEndpoint(
            instance_id=instance_id, url=address, control_token=ControlToken(token)
        )

    async def signal_endpoint(self) -> InstanceEndpoint | InstanceUnreachable:
        """Reach the instance that kept the public webhook URL it was registered with."""
        alias = self.registry.signal_alias()
        if alias is None:
            return InstanceUnreachable.MISSING
        return await self.endpoint(alias)

    def signal_accepted(self, instance_id: UUID, signal: Signal) -> None:
        """Wake the parked waits of the instance's factory that the signal matches."""
        self.runs.signal_accepted(instance_id, signal)

    async def stats_summary(self, command: StatsGetCommand) -> StatsSummaryResult:
        """Ask every instance at once, and keep none of the answers (ADR 0063)."""
        return summed_usage(await self._answers(command))

    async def stats_runs(self, command: StatsRunsCommand) -> StatsRunsResult:
        """Join the runs the instance handed in with their tokens in every instance, which are
        asked at once, and keep none of the answers (ADR 0063)."""
        self._active_instance(command.instance_id)
        created = TimeRange(command.since, command.until)
        runs = [
            run
            for run in self.registry.runs(None, None, command.instance_id)
            if created.includes(run.created_at)
        ]
        answers = await self._answers(StatsGetCommand(since=command.since, until=command.until))
        return run_usage([(run, self.runs.rework(run)) for run in runs], answers)

    async def _answers(self, command: StatsGetCommand) -> dict[UUID, StatsGetResult | Uncounted]:
        records = [record for record in self.registry.managed_instances() if record.active]
        answers = await asyncio.gather(*(self._usage(record, command) for record in records))
        return dict(zip((record.instance_id for record in records), answers, strict=True))

    async def _usage(
        self, record: ManagedInstance, command: StatsGetCommand
    ) -> StatsGetResult | Uncounted:
        """A slow instance counts as unreachable, so it cannot hold up the others."""
        try:
            async with asyncio.timeout(STATS_SECONDS):
                if (await self._runtime.status(record.runtime_id)).state != "running":
                    return Uncounted.SKIPPED
                return await self._control.stats(await self._endpoint(record), command)
        except ControlUnreachable, TimeoutError:
            return Uncounted.UNREACHABLE
        except IncompatibleLifecycleEndpoint as exc:
            _logger.warning("Instance %s runs an older core: %s", record.instance_id, exc)
            return Uncounted.OUTDATED

    async def operation(self, command: OperationGetCommand) -> OperationGetResult:
        result = self.registry.operation(command.operation_id)
        if result is None:
            raise LifecycleOperationNotFound(
                f'Lifecycle operation "{command.operation_id}" was not found.'
            )
        return result

    def _active_instance(self, instance_id: UUID) -> ManagedInstance:
        record = self.registry.instance(instance_id)
        if record is None or not record.active:
            raise ManagedInstanceNotFound(f'Instance "{instance_id}" was not found.')
        return record

    def _removed_instance(self, instance_id: UUID) -> ManagedInstance:
        record = self.registry.instance(instance_id)
        if record is None or record.intended_state is not IntendedState.REMOVED:
            raise ManagedInstanceNotFound(f'Removed instance "{instance_id}" was not found.')
        return record

    def _storage(
        self,
        instance_id: UUID,
        path: Path,
        logins: Collection[SubscriptionLogin],
    ) -> tuple[StorageItem, ...]:
        """What a created instance owns: its directory on the Docker host, its workspace
        volume, and one volume per declared login.

        A login's volume is named after its id, so the factory's `codex` login keeps the name
        the hub gave the Codex volume before packages declared their logins.
        """
        relative = path.relative_to(self.directory)
        host_path = self._docker_host_directory / relative
        volume = f"kinby-{instance_id}"
        login_volumes = [
            StorageItem(
                kind=StorageKind.VOLUME,
                source=f"{volume}-{login.id}",
                destination=login.volume,
                writable=True,
            )
            for login in logins
        ]
        return (
            StorageItem(
                kind=StorageKind.BIND,
                source=str(host_path),
                destination="/instance",
                writable=True,
            ),
            StorageItem(
                kind=StorageKind.VOLUME,
                source=f"{volume}-workspace",
                destination="/instance/workspace",
                writable=True,
            ),
            *login_volumes,
        )

    @staticmethod
    def _instance_secrets(model: str, secrets: dict[str, str]) -> dict[str, str]:
        """The secrets as the instance reads them: the API key under its provider's variable.

        Blank text counts as not sent, the same way setup validation reads it, so an optional
        secret left empty stays unset.
        """
        held = held_secrets(
            model, {name: value for name, value in secrets.items() if value.strip()}
        )
        held[CONTROL_TOKEN_VARIABLE] = new_control_token()
        return held

    @staticmethod
    def _validate_secret_names(secrets: dict[str, str]) -> None:
        invalid = [name for name in secrets if ENVIRONMENT_NAME.fullmatch(name) is None]
        if invalid:
            raise ValueError(f'Invalid environment variable name: "{invalid[0]}".')

    @staticmethod
    def _dotenv_value(value: str) -> str:
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

    @classmethod
    def _dotenv(cls, secrets: dict[str, str]) -> str:
        return "".join(f"{name}={cls._dotenv_value(value)}\n" for name, value in secrets.items())

    @classmethod
    def _write_secrets(cls, path: Path, secrets: dict[str, str]) -> None:
        write_protected(path, cls._dotenv(secrets))

    @classmethod
    def _replace_secrets(cls, directory: Path, secrets: dict[str, str]) -> None:
        """Merge the submitted values in through one rename."""
        replace_protected(directory / ".env", cls._dotenv(cls._environment(directory) | secrets))

    @staticmethod
    def _toml_string(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    @classmethod
    def _write_configuration(
        cls,
        directory: Path,
        manifest_id: str,
        persona_name: str | None,
    ) -> None:
        path = directory / "kinby.toml"
        lines = path.read_text(encoding="utf-8").splitlines()
        identity = [f"id = {cls._toml_string(manifest_id)}"]
        if persona_name is not None:
            identity.append(f"persona_name = {cls._toml_string(persona_name)}")
        for index, line in enumerate(lines):
            if line.startswith("id = "):
                lines[index : index + 1] = identity
                break
        _set_listen(lines)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _environment(path: Path) -> dict[str, str]:
        return {
            name: value
            for name, value in dotenv_values(path / ".env", interpolate=False).items()
            if value is not None
        }

    @staticmethod
    def _redact(message: str, secrets: Collection[str]) -> str:
        redacted = message
        for secret in sorted(secrets, key=len, reverse=True):
            if secret:
                redacted = redacted.replace(secret, "[REDACTED]")
        return redacted

    @staticmethod
    def _status(status: RuntimeStatus) -> tuple[ProcessState, Readiness]:
        if status.state == "absent":
            return ProcessState.MISSING, Readiness.NOT_RUNNING
        if status.state == "created":
            return ProcessState.CREATED, Readiness.NOT_RUNNING
        if status.state == "starting":
            return ProcessState.STARTING, Readiness.STARTING
        if status.state == "stopped":
            return ProcessState.STOPPED, Readiness.NOT_RUNNING
        if status.state == "failed":
            return ProcessState.FAILED, Readiness.NOT_RUNNING
        if status.healthy is True:
            return ProcessState.RUNNING, Readiness.READY
        if status.healthy is False:
            return ProcessState.RUNNING, Readiness.UNHEALTHY
        return ProcessState.RUNNING, Readiness.UNKNOWN
