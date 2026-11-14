"""Typed hub lifecycle service."""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import re
import shutil
from collections.abc import (
    Awaitable,
    Callable,
    Collection,
    Coroutine,
    Iterable,
    Mapping,
    Sequence,
)
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import IO
from uuid import UUID, uuid4

from dotenv import dotenv_values
from pydantic import JsonValue

from kinby.contracts import (
    BROWSER_CLOSE,
    BROWSER_OPEN,
    BROWSER_PROFILE_DELETE,
    BROWSER_PROFILE_LIST,
    BROWSER_PROFILE_RENAME,
    BROWSER_PROFILE_SAVE,
    BROWSER_PROFILES,
    BROWSER_RUN,
    BROWSER_SCOPES,
    BROWSER_SCREENSHOT,
    BROWSER_SNAPSHOT,
    BROWSER_TAKEOVER_FINISH,
    BROWSER_TAKEOVER_INPUT,
    BROWSER_TAKEOVER_START,
    BROWSER_TEXT,
    BROWSER_VAULT,
    BROWSER_VAULT_ADD,
    BROWSER_VAULT_DELETE,
    BROWSER_VAULT_EDIT,
    BROWSER_VAULT_LIST,
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
    FACTORY_REMOVE,
    FACTORY_RUN_ANSWER,
    FACTORY_RUN_APPROVE,
    FACTORY_RUN_CANCEL,
    FACTORY_RUN_GET,
    FACTORY_RUN_INTAKE,
    FACTORY_RUN_LIST,
    FACTORY_RUN_RETRY,
    FACTORY_RUN_SEND_BACK,
    FACTORY_RUN_SUBSCRIBE,
    FACTORY_RUN_TAKEOVER_FINISH,
    FACTORY_RUN_TRANSCRIPT,
    HOME_SCOPES,
    HUB_SECRET_DELETE,
    HUB_SECRET_LIST,
    HUB_SECRET_SET,
    HUB_WRITE,
    IMAGE_DESCRIBE,
    IMAGE_PREPARE,
    INSTANCE_ADOPT,
    INSTANCE_ADOPT_PREVIEW,
    INSTANCE_CONVERT,
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
    INSTANCE_TOOLSET_GET,
    INSTANCE_UPDATE,
    INTAKE_SCOPES,
    LIBRARY_GIT_ADD,
    LIBRARY_GIT_PREVIEW,
    LIBRARY_LIST,
    LIBRARY_MCP_SERVER_DELETE,
    LIBRARY_MCP_SERVER_READ,
    LIBRARY_MCP_SERVER_WRITE,
    LIBRARY_SKILL_CHECK,
    LIBRARY_SKILL_DELETE,
    LIBRARY_SKILL_FILE_READ,
    LIBRARY_SKILL_MOVE,
    LIBRARY_SKILL_READ,
    LIBRARY_SKILL_WRITE,
    LINE_GET,
    LINE_HAND_IN,
    LINE_INTAKE,
    LINE_LIST,
    LINE_OWNED_HAND_IN,
    LINE_OWNED_LIST,
    LINE_OWNER_SET,
    LINE_REMOVE,
    LINE_SCOPES,
    LINE_SETUP,
    LINE_SETUP_DESCRIBE,
    LINE_UPDATE,
    NOTIFICATION_LIST,
    NOTIFICATION_RAISE,
    NOTIFICATION_REPLY,
    NOTIFICATION_SCOPES,
    OPERATION_GET,
    RELAYED_READS,
    ROUTINE_DELETE,
    ROUTINE_READ,
    ROUTINE_WRITE,
    STATS_RUNS,
    STATS_SUMMARY,
    THREAD_APPROVAL_RESPOND,
    THREAD_ASK,
    THREAD_TAKEOVER_FINISH,
    THREAD_TAKEOVER_INPUT,
    THREAD_TAKEOVER_RESUME,
    THREAD_TAKEOVER_START,
    THREAD_TURN_START,
    VIEW_WRITE,
    WORKBENCH_COMMANDS_SET,
    WORKBENCH_CREATE,
    WORKBENCH_DELETE,
    WORKBENCH_GET,
    WORKBENCH_LIST,
    WORKBENCH_LOGIN_START,
    WORKBENCH_OWNED_COMMAND_RUN,
    WORKBENCH_OWNED_COMMANDS_SET,
    WORKBENCH_OWNED_DIRECTORY_LIST,
    WORKBENCH_OWNED_FILE_READ,
    WORKBENCH_PICKS_GET,
    WORKBENCH_PICKS_SET,
    WORKBENCH_RECREATE,
    WORKBENCH_REMOVE,
    WORKBENCH_RESTORE,
    WORKBENCH_SECRETS_SET,
    WORKBENCH_START,
    WORKBENCH_STOP,
    WORKBENCH_UPDATE,
    WORKSPACE_COMMAND_RUN,
    WORKSPACE_DIRECTORY_LIST,
    WORKSPACE_FILE_READ,
    AcceptedResult,
    ApprovalDecision,
    AuthoredTool,
    Avatar,
    BrowserProfileDeleteCommand,
    BrowserProfileListCommand,
    BrowserProfileListResult,
    BrowserProfileRenameCommand,
    BrowserProfilesCommand,
    BrowserVaultCommand,
    Capability,
    ChannelKindName,
    ContainerOwner,
    ContractModel,
    ControlToken,
    DeclaredSetup,
    DefaultTool,
    DrainState,
    ErrorCode,
    FactoryEditCommand,
    FactoryListResult,
    FactoryName,
    FactoryRemoveCommand,
    FactoryResult,
    FactoryRole,
    FactoryRun,
    FactoryRunStatus,
    FactoryRunTranscript,
    FactoryRunTranscriptCommand,
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
    InstanceConvertCommand,
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
    InstanceToolsetGetCommand,
    InstanceToolsetResult,
    InstanceUpdateCommand,
    IntakeRunCommand,
    IntendedState,
    LibraryGitAddCommand,
    LibraryListResult,
    LibraryMcpServerWriteCommand,
    LibrarySkillCheckCommand,
    LibrarySkillWriteCommand,
    LifecycleOperationResult,
    Line,
    LineGetCommand,
    LineHandInCommand,
    LineIntakeCommand,
    LineIntakeResult,
    LineListCommand,
    LineListResult,
    LineOwnedListCommand,
    LineOwnedListResult,
    LineOwnerSetCommand,
    LineRemoveCommand,
    LineSetupCommand,
    LineSetupDescribeCommand,
    LineSetupDescription,
    LineSetupResult,
    LineUpdateCommand,
    LoginPrompt,
    LoginSetup,
    LoginState,
    McpServerFailed,
    Method,
    NewerVersion,
    OperationGetCommand,
    OperationGetResult,
    OperationKind,
    OperationState,
    OwnedLine,
    PermissionMode,
    ProcessState,
    Readiness,
    RecreateReason,
    RepositoryCommandList,
    RevisionBehind,
    RoutineDeleteCommand,
    RoutineFile,
    RoutineName,
    RoutineReadCommand,
    RoutineWriteCommand,
    RunFolderRemoveCommand,
    RunFolderRemoveResult,
    RunStop,
    Scope,
    SearchSetup,
    SetupField,
    SetupFieldKind,
    SetupFieldType,
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
    ThreadTakeoverResumeCommand,
    ThreadTurnStartCommand,
    VaultEntryAddCommand,
    VaultEntryDeleteCommand,
    VaultEntryEditCommand,
    VaultEntryListCommand,
    VaultEntryListResult,
    View,
    ViewWriteCommand,
    WaitingApproval,
    Workbench,
    WorkbenchCommandRunCommand,
    WorkbenchCommandsSetCommand,
    WorkbenchCreateCommand,
    WorkbenchCreateResult,
    WorkbenchDeleteCommand,
    WorkbenchDirectoryListCommand,
    WorkbenchFileReadCommand,
    WorkbenchGetCommand,
    WorkbenchListCommand,
    WorkbenchListResult,
    WorkbenchLoginStartCommand,
    WorkbenchOperationResult,
    WorkbenchPicksGetCommand,
    WorkbenchPicksResult,
    WorkbenchPicksSetCommand,
    WorkbenchRecreateCommand,
    WorkbenchRemoveCommand,
    WorkbenchRestoreCommand,
    WorkbenchSecretsSetCommand,
    WorkbenchStartCommand,
    WorkbenchStopCommand,
    WorkbenchUpdateCommand,
)
from kinby.core.clock import utc_now
from kinby.core.contract_server import CONTROL_TOKEN_VARIABLE
from kinby.core.errors import (
    AdoptionBlocked,
    ConversionFailed,
    FactoryCheckFailed,
    FactoryInUse,
    FactoryNotFound,
    FactoryRunNotFound,
    HomeInstanceKept,
    ImageNotPrepared,
    InstanceInUse,
    InstanceRefused,
    InstanceUnavailable,
    IntakeFailed,
    InvalidSetup,
    LifecycleOperationInFlight,
    LifecycleOperationNotFound,
    LineBusy,
    LineNotFound,
    LoginNotFound,
    ManagedInstanceNotFound,
    NameTaken,
    NotAnIntake,
    PermissionDenied,
    WorkbenchInUse,
    WorkbenchNotFound,
    WorkspaceReadFailed,
)
from kinby.core.routing import Dispatcher
from kinby.factories import SHIPPED_FACTORIES, ships_recipe
from kinby.factories.check import login_declaration_problems
from kinby.factories.file import (
    Engine,
    FactoryFile,
    InstanceName,
    RecipeName,
    WorkbenchAgentStep,
    WorkbenchFactory,
    WorkbenchIntake,
)
from kinby.frontmatter import render_frontmatter_value
from kinby.hub.access import HubAccess, new_control_token
from kinby.hub.adoption import INSTANCE_MOUNT, blocker, preflight, previous_manager
from kinby.hub.approvals import HomeApprovals
from kinby.hub.browser import BrowserLimits, BrowserRuntime
from kinby.hub.browser_profiles import BrowserProfiles
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
from kinby.hub.playwright_browser import PlaywrightBrowser
from kinby.hub.recovery import recover_lifecycle
from kinby.hub.registry import (
    UNFINISHED,
    FactoryMember,
    HubRegistry,
    ManagedInstance,
    WorkbenchSetup,
)
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
from kinby.hub.takeovers import Takeovers
from kinby.hub.template_sync import sync_template
from kinby.hub.usage import Uncounted, run_usage, summed_usage
from kinby.hub.vault import Vault
from kinby.instance import (
    PLACEHOLDER_MODEL,
    Instance,
    Manifest,
    ManifestError,
    ModelName,
    Tools,
    api_key_variable,
    init_from_template,
    init_instance,
    inspect_instance,
)
from kinby.instance.code import declared_secrets, exposed_tools, packages
from kinby.instance.config_changes import file_hash
from kinby.instance.layout import ENV_NAME, SYSTEM_NAME
from kinby.instance.manifest import repository_commands
from kinby.instance.read_only_tools import read_only_tools
from kinby.instance.skills_lock import lock_hash, read_lock
from kinby.library import HOME_SKILLS, SHIPPED_SKILLS
from kinby.memory import GraphStore
from kinby.plugins.defaults import TOOLS as DEFAULT_TOOLS
from kinby.plugins.hub import (
    HUB_SCOPES_VARIABLE,
    HUB_URL_VARIABLE,
    ChannelMute,
    ChannelUnmute,
    InstanceAsk,
    LibraryMove,
    RelayedWrite,
    home_write,
    hub_write,
    requested_write,
)
from kinby.plugins.intake import run_line_intake
from kinby.plugins.mcp import failed_mcp_servers
from kinby.setup import (
    API_KEY_FIELD,
    BEHAVIOR_PROMPT_FIELD,
    MODEL_FIELD,
    value_problem,
    with_search_key,
)
from kinby.workbench import FACTORIES_MOUNT

_logger = logging.getLogger(__name__)

type _WorkspaceRead = (
    WorkbenchFileReadCommand | WorkbenchDirectoryListCommand | WorkbenchCommandRunCommand
)

#: Fills a record's directory as its creation writes it, and names the logins it declares.
type _Write = Callable[[UUID, Path, ImageArtifact], Awaitable[Sequence[SubscriptionLogin]]]

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
#: What a workbench's container runs in place of an instance's serve mode.
_WORKBENCH_COMMAND = ("workbench", "--listen", f"{_INSTANCE_HOST}:{_INSTANCE_PORT}")
_FACTORIES_DIR = "factories"
_HOME_NAME = "home"
_HOME_PERSONA_NAME = "Kinby"
#: How long a login waits for the user to sign in before its code counts as expired.
LOGIN_SECONDS = 15 * 60
#: The title of the notification each kind of instance notice raises.
_NOTICE_TITLES = {
    "revision_behind": "Revision behind",
    "template_behind": "Template behind",
    "hub_route_missing": "Hub route missing",
    "mcp_server_failed": "MCP server failed",
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
    failed_servers: Mapping[str, str],
) -> dict[str, InstanceNotice]:
    """The hub route the instance's environment lacks, the kinby revision the hub moved past, the
    template the factory moved past, and the MCP servers its last turn ran without, if any, each by
    its kind and the value it reports.

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
    for server, message in failed_servers.items():
        notices[f"mcp_server_failed:{server}:{message}"] = McpServerFailed(
            message=message, server=server
        )
    return notices


def _selection(record: ManagedInstance, revision: str) -> ImageSelection:
    """The image a record's container runs: the revision, and the recipe of the instance's
    template or of the workbench."""
    if record.workbench is not None:
        return ImageSelection(revision, record.workbench.recipe)
    recipe = record.factory.recipe if record.factory is not None else None
    return ImageSelection(revision, recipe)


def _instance_name(command: InstanceCreateCommand) -> str:
    """The name the request gives, or ``{factory}-{role}`` for one that picks a factory role."""
    if command.manifest_id is not None:
        return command.manifest_id
    if command.factory_role is None:
        raise InvalidSetup({"manifest_id": "Name the instance, or pick a factory role."})
    return f"{command.factory_role.factory}-{command.factory_role.name}"


def _quoted(names: Iterable[str]) -> str:
    return ", ".join(f'"{name}"' for name in names)


def _named(runs: Iterable[FactoryRun]) -> str:
    return "; ".join(
        f"{run.factory} for "
        + ", ".join(f"{name} {value}" for name, value in run.work_item.items())
        + f" ({run.run_id})"
        for run in runs
    )


def _image(recipe: RecipeName | None) -> str:
    return "kinby's base image" if recipe is None else f'image recipe "{recipe}"'


@dataclass(frozen=True)
class _Holding:
    """What a workbench holds, by name, or will once a line setup's answers land on it."""

    secrets: Collection[str] = ()
    values: Collection[str] = ()
    commands: Collection[str] = ()
    signed_in: Collection[str] = ()
    browser: bool = False

    def answered(self, command: LineSetupCommand) -> _Holding:
        """Blank text answers no secret, as a creation reads it."""
        typed = (
            name for name, value in command.secrets.items() if value.get_secret_value().strip()
        )
        return replace(
            self,
            secrets={*self.secrets, *typed, *command.hub_secrets},
            values={*self.values, *command.values},
            commands={*self.commands, *command.commands},
        )


#: What a new workbench needs beside what its factory requires, named as a line setup takes it.
_NEW_WORKBENCH_FIELDS = (
    SetupField(
        name="name",
        label="Name",
        description="The workbench's name, short like a folder's.",
        kind=SetupFieldKind.CONFIG,
        type=SetupFieldType.TEXT,
        required=True,
    ),
    SetupField(
        name="workspace_source",
        label="Repository",
        description="The git URL its workspace is cloned from. Without one it keeps a plain "
        "folder.",
        kind=SetupFieldKind.CONFIG,
        type=SetupFieldType.URL,
        required=False,
    ),
)


def _asked(
    factory: WorkbenchFactory, model: str | None, holding: _Holding, *, new: bool
) -> LineSetupDescription:
    """What setting *factory* up on a workbench that holds *holding* asks for: each secret, named
    value, list of repository commands and login the factory requires and it does not hold, and
    the browser when it has none. A field with a default is held, and a login only once it signed
    in. A *new* workbench is asked for its own fields and every field the factory declares."""
    requires = factory.requires
    declared = {field.name for field in requires.secrets}
    secrets = [
        *(field.setup_field(SetupFieldKind.SECRET) for field in requires.secrets),
        *(
            SetupField(
                name=key,
                label="API key",
                description="The key of the model provider the kinby agent steps call.",
                kind=SetupFieldKind.SECRET,
                type=SetupFieldType.TEXT,
                required=True,
            )
            for key in _model_keys(factory, model)
            if key not in declared
        ),
    ]
    values = [field.setup_field(SetupFieldKind.CONFIG) for field in requires.values]
    return LineSetupDescription(
        workbench=list(_NEW_WORKBENCH_FIELDS) if new else [],
        secrets=[
            field
            for field in secrets
            if new or (field.required and field.name not in holding.secrets)
        ],
        values=[
            field
            for field in values
            if new
            or (field.required and field.default is None and field.name not in holding.values)
        ],
        commands=[name for name in requires.commands if name not in holding.commands],
        logins=[login for login in requires.logins if login.id not in holding.signed_in],
        browser=requires.browser and not holding.browser,
        suggested_picks=list(requires.suggested_picks),
    )


def _lacking(asked: LineSetupDescription) -> list[str]:
    return [
        *(f'secret "{field.name}"' for field in asked.secrets),
        *(f'value "{field.name}"' for field in asked.values),
        *(f'command list "{name}"' for name in asked.commands),
        *(f'login "{login.id}"' for login in asked.logins),
        *(["a browser"] if asked.browser else []),
    ]


#: What a line's intake routine says to its model, which never runs: its code step returns nothing.
_INTAKE_PROMPT = (
    "The intake hands the line its work itself, so there is nothing for you to do. Stop."
)


def _intake_routine(factory: FactoryName, workbench: str, intake: WorkbenchIntake) -> str:
    """The ROUTINE.md of the line's intake routine, fired as the factory's intake declares. Its
    code step has the hub run the intake on the workbench."""
    fields = {
        "description": f'Hand line "{factory}" on workbench "{workbench}" the work items its '
        "intake finds.",
        "mode": PermissionMode.FULL_ACCESS.value,
        "run": run_line_intake.name,
    }
    if intake.schedule is not None:
        fields["schedule"] = intake.schedule
    lines = [f"{key}: {render_frontmatter_value(value)}" for key, value in fields.items()]
    delivery = intake.delivery
    if delivery is not None:
        signal = {
            "auth": delivery.auth.value,
            "secret": delivery.secret,
            "signature_header": delivery.signature_header,
            "delivery_header": delivery.delivery_header,
        }
        lines += [
            "signal:",
            *(
                f"  {key}: {render_frontmatter_value(value)}"
                for key, value in signal.items()
                if value
            ),
        ]
    return "\n".join(["---", *lines, "---", _INTAKE_PROMPT, ""])


def _model_keys(factory: WorkbenchFactory, line_model: str | None) -> list[str]:
    """The key each of the factory's agent steps on the kinby engine needs for its model, or for
    *line_model* when it names none, in the order the steps come."""
    models = (
        step.model or line_model
        for step in factory.steps
        if isinstance(step, WorkbenchAgentStep) and step.engine is Engine.KINBY
    )
    return list(dict.fromkeys(api_key_variable(model) for model in models if model is not None))


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
class _InstanceFiles:
    """The instance secrets and the manifest of one instance, read once for one answer."""

    secrets: dict[str, str]
    #: None when the manifest cannot be read.
    manifest: Manifest | None


@dataclass(frozen=True)
class ObservedProcess:
    """What the container runtime reports for one instance, its detail redacted."""

    process: ProcessState
    readiness: Readiness
    detail: str


_BROWSER_LIMITS = BrowserLimits()


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
        browser: BrowserRuntime | None = None,
        browser_limits: BrowserLimits = _BROWSER_LIMITS,
    ) -> None:
        """*private_url* is where the hub's instances reach it on their private network, and
        *public_url* where the user reaches its web app. *channel_kinds* stand in for the kinds
        installed packages expose. Browser-on instances get browsers on the service *browser*
        runs, and without it none."""
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
            # Links and channel routes append a path that starts with a slash.
            public_url = public_url.rstrip("/") if public_url is not None else None
            self.factories = FactoryStore(self.directory / _FACTORIES_DIR, shipped_factories)
            self.library = Library(
                self.directory / "skills",
                self.directory / "mcp-servers",
                shipped_skills,
                self.factories,
                self.registry,
            )
            self.library.receive()
            self.secrets = HubSecrets(self.directory / "hub-secrets.json")
            self.browser_profiles = BrowserProfiles(self.directory / "browser-profiles")
            self.vault = Vault(self.directory / "vault")
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
                self.registry,
                self.factories,
                self._run_step,
                self._tell_stop,
                self.outbox,
                self._remove_run_folder,
                self._write_view,
            )
            self.replies = Replies(
                self.registry,
                self.channels,
                self._start_turn,
                self._answer_approval,
                self.runs.answer_stop,
                self.runs.answer_ask,
                clock,
                sleep,
            )
            self._control = control if control is not None else HttpInstanceControl()
            self._browsers = (
                PlaywrightBrowser(
                    browser, browser_limits, self.browser_profiles, self.vault, self._is_workbench
                )
                if browser is not None
                else None
            )
            self._takeovers = (
                Takeovers(self._browsers, self._instance_browser_on, self._resume_takeover)
                if self._browsers is not None
                else None
            )
            self._locks: dict[UUID, asyncio.Lock] = {}
            self._hub_secrets_lock = asyncio.Lock()
            #: Held while a line, a workbench or a factory role's instance is added, and while a
            #: factory is removed, so no removal misses a dependent added during it, and no
            #: workbench takes the name of the one a line setup creates while its owner takes the
            #: intake routine.
            self._factory_dependents = asyncio.Lock()
            self._stopping: dict[UUID, PendingStop] = {}
            #: The drain each operation is waiting on, whichever operation it is.
            self._draining: dict[UUID, PendingStop] = {}
            self._tasks: set[asyncio.Task[None]] = set()
            self.dispatcher = Dispatcher()
            self._serve_hub(self.dispatcher)
        except BaseException:
            self._directory_lock.close()
            raise

    def close(self) -> None:
        """Release this directory so another process can own it."""
        self.registry.close()
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
        dispatcher.register(WORKBENCH_CREATE, self.create_workbench)
        dispatcher.register(WORKBENCH_LIST, self.list_workbenches)
        dispatcher.register(WORKBENCH_GET, self.get_workbench)
        dispatcher.register(WORKBENCH_START, self.start_workbench)
        dispatcher.register(WORKBENCH_STOP, self.stop_workbench)
        dispatcher.register(WORKBENCH_DELETE, self.delete_workbench)
        dispatcher.register(WORKBENCH_RECREATE, self.recreate_workbench)
        dispatcher.register(WORKBENCH_UPDATE, self.update_workbench)
        dispatcher.register(WORKBENCH_REMOVE, self.remove_workbench)
        dispatcher.register(WORKBENCH_RESTORE, self.restore_workbench)
        dispatcher.register(WORKBENCH_SECRETS_SET, self.set_workbench_secrets)
        dispatcher.register(WORKBENCH_LOGIN_START, self.start_workbench_login)
        dispatcher.register(WORKBENCH_COMMANDS_SET, self.set_workbench_commands)
        dispatcher.register(WORKBENCH_PICKS_GET, self.workbench_picks)
        dispatcher.register(WORKBENCH_PICKS_SET, self.set_workbench_picks)
        dispatcher.register(LINE_SETUP, self.set_up_line)
        dispatcher.register(LINE_SETUP_DESCRIBE, self.describe_line_setup)
        dispatcher.register(LINE_LIST, self.list_lines)
        dispatcher.register(LINE_GET, self.get_line)
        dispatcher.register(LINE_UPDATE, self.update_line)
        dispatcher.register(LINE_OWNER_SET, self.set_line_owner)
        dispatcher.register(INSTANCE_CONVERT, self.convert_instance)
        dispatcher.register(LINE_REMOVE, self.remove_line)
        dispatcher.register(LINE_HAND_IN, self.hand_in)
        dispatcher.register(OPERATION_GET, self.operation)
        dispatcher.register(IMAGE_PREPARE, self.prepare_image)
        dispatcher.register(IMAGE_DESCRIBE, self.describe_image)
        dispatcher.register(FACTORY_LIST, self.factories.list)
        dispatcher.register(FACTORY_GET, self.factories.get)
        dispatcher.register(FACTORY_CHECK, self.factories.check)
        dispatcher.register(FACTORY_EDIT, self.edit_factory)
        dispatcher.register(FACTORY_REMOVE, self.remove_factory)
        dispatcher.register(FACTORY_DESCRIBE, self.factories.describe)
        dispatcher.register(FACTORY_RUN_LIST, self.runs.list)
        dispatcher.register(FACTORY_RUN_GET, self.runs.get)
        dispatcher.register(FACTORY_RUN_TRANSCRIPT, self.run_transcript)
        dispatcher.register(FACTORY_RUN_RETRY, self.runs.retry)
        dispatcher.register(FACTORY_RUN_SEND_BACK, self.runs.send_back)
        dispatcher.register(FACTORY_RUN_APPROVE, self.runs.approve)
        dispatcher.register(FACTORY_RUN_ANSWER, self.runs.answer)
        dispatcher.register(FACTORY_RUN_TAKEOVER_FINISH, self.runs.finish_takeover)
        dispatcher.register(FACTORY_RUN_CANCEL, self.runs.cancel)
        dispatcher.register_subscription(FACTORY_RUN_SUBSCRIBE, self.runs.subscribe)
        dispatcher.register(LIBRARY_LIST, self.library.list)
        dispatcher.register(LIBRARY_SKILL_READ, self.library.read_skill)
        dispatcher.register(LIBRARY_SKILL_FILE_READ, self.library.read_skill_file)
        dispatcher.register(LIBRARY_SKILL_WRITE, self.library.write_skill)
        dispatcher.register(LIBRARY_SKILL_DELETE, self.library.delete_skill)
        dispatcher.register(LIBRARY_SKILL_CHECK, self.library.check)
        dispatcher.register(LIBRARY_SKILL_MOVE, self.library.move)
        dispatcher.register(LIBRARY_GIT_PREVIEW, self.library.preview)
        dispatcher.register(LIBRARY_GIT_ADD, self.library.add)
        dispatcher.register(LIBRARY_MCP_SERVER_READ, self.library.read_mcp_server)
        dispatcher.register(LIBRARY_MCP_SERVER_WRITE, self.library.write_mcp_server)
        dispatcher.register(LIBRARY_MCP_SERVER_DELETE, self.library.delete_mcp_server)
        dispatcher.register(INSTANCE_PICKS_GET, self.picks)
        dispatcher.register(INSTANCE_PICKS_SET, self.set_picks)
        dispatcher.register(INSTANCE_TOOLSET_GET, self.toolset)
        dispatcher.register(HUB_SECRET_LIST, self.list_hub_secrets)
        dispatcher.register(HUB_SECRET_SET, self.set_hub_secret)
        dispatcher.register(HUB_SECRET_DELETE, self.delete_hub_secret)
        dispatcher.register(BROWSER_PROFILE_LIST, self.list_browser_profiles)
        dispatcher.register(BROWSER_PROFILE_RENAME, self.rename_browser_profile)
        dispatcher.register(BROWSER_PROFILE_DELETE, self.delete_browser_profile)
        dispatcher.register(BROWSER_VAULT_LIST, self.list_vault_entries)
        dispatcher.register(BROWSER_VAULT_ADD, self.add_vault_entry)
        dispatcher.register(BROWSER_VAULT_EDIT, self.edit_vault_entry)
        dispatcher.register(BROWSER_VAULT_DELETE, self.delete_vault_entry)
        if self._takeovers is not None:
            dispatcher.register_subscription(THREAD_TAKEOVER_START, self._takeovers.watch_turn)
            dispatcher.register(THREAD_TAKEOVER_INPUT, self._takeovers.drive_turn)
            dispatcher.register(THREAD_TAKEOVER_FINISH, self._takeovers.finish_turn)
            dispatcher.register_subscription(BROWSER_TAKEOVER_START, self._takeovers.start)
            dispatcher.register(BROWSER_TAKEOVER_INPUT, self._takeovers.send_input)
            dispatcher.register(BROWSER_TAKEOVER_FINISH, self._takeovers.finish)
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
        return self._scopes(record, self._manifest(record))

    def _scopes(self, record: ManagedInstance, manifest: Manifest | None) -> frozenset[Scope]:
        if record.workbench is not None:
            browser = record.workbench.browser and self._browsers is not None
            return BROWSER_SCOPES if browser else frozenset()
        return (
            NOTIFICATION_SCOPES
            | LINE_SCOPES
            | (INTAKE_SCOPES if record.factory is not None else frozenset())
            | (HOME_SCOPES if record.home else frozenset())
            | (BROWSER_SCOPES if self._browser_on(manifest) else frozenset())
        )

    def _browser_on(self, manifest: Manifest | None) -> bool:
        """Whether the hub lends browsers and the instance's manifest turns the browser on."""
        return self._browsers is not None and manifest is not None and manifest.browser

    def _instance_browser_on(self, instance_id: UUID) -> bool:
        """Whether the instance's manifest, or the workbench's record, turns the browser on."""
        record = self._active_instance(instance_id)
        return Scope.BROWSER_OPEN in self._scopes(record, self._manifest(record))

    def _is_workbench(self, instance_id: UUID) -> bool:
        record = self.registry.instance(instance_id)
        return record is not None and record.workbench is not None

    async def _resume_takeover(
        self, instance_id: UUID, command: ThreadTakeoverResumeCommand
    ) -> AcceptedResult:
        resumed = await self._relayed(
            instance_id, THREAD_TAKEOVER_RESUME.name, command.model_dump(mode="json")
        )
        return AcceptedResult.model_validate(resumed)

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
        if Scope.LINE_INTAKE in scopes:
            dispatcher.register(LINE_INTAKE, partial(self.line_intake, instance_id))
        if Scope.LINE_OWNER in scopes:
            dispatcher.register(LINE_OWNED_LIST, partial(self.owned_lines, instance_id))
            dispatcher.register(LINE_OWNED_HAND_IN, partial(self.owned_hand_in, instance_id))
            for owned, workspace in (
                (WORKBENCH_OWNED_FILE_READ, WORKSPACE_FILE_READ),
                (WORKBENCH_OWNED_DIRECTORY_LIST, WORKSPACE_DIRECTORY_LIST),
                (WORKBENCH_OWNED_COMMAND_RUN, WORKSPACE_COMMAND_RUN),
            ):
                dispatcher.register(
                    owned, partial(self.read_owned_workspace, instance_id, workspace)
                )
            dispatcher.register(
                WORKBENCH_OWNED_COMMANDS_SET,
                partial(self.set_owned_workbench_commands, instance_id),
            )
        if Scope.BROWSER_OPEN in scopes:
            browsers = self._browsers
            assert browsers is not None
            dispatcher.register(BROWSER_OPEN, partial(browsers.open, instance_id))
            dispatcher.register(BROWSER_RUN, partial(browsers.run, instance_id))
            dispatcher.register(BROWSER_SNAPSHOT, partial(browsers.snapshot, instance_id))
            dispatcher.register(BROWSER_TEXT, partial(browsers.text, instance_id))
            dispatcher.register(BROWSER_SCREENSHOT, partial(browsers.screenshot, instance_id))
            dispatcher.register(BROWSER_PROFILE_SAVE, partial(browsers.save_profile, instance_id))
            dispatcher.register(BROWSER_CLOSE, partial(browsers.close, instance_id))
            dispatcher.register(BROWSER_PROFILES, partial(self.own_browser_profiles, instance_id))
            dispatcher.register(BROWSER_VAULT, partial(self.own_vault_entries, instance_id))
        if Scope.HUB_HOME in scopes:
            self._serve_hub(dispatcher)
            dispatcher.register(INSTANCE_RELAY_READ, self.relay_read)
            dispatcher.register(HUB_WRITE, partial(self.write_for_home, instance_id))
        return scopes, dispatcher

    def home_approvals(self, instance_id: UUID) -> HomeApprovals | None:
        """What the hub witnesses on the instance's relay: the home instance's approvals only."""
        record = self.registry.instance(instance_id)
        return self.approvals if record is not None and record.home else None

    async def own_browser_profiles(
        self, instance_id: UUID, command: BrowserProfilesCommand
    ) -> BrowserProfileListResult:
        return self.browser_profiles.list(instance_id)

    async def list_browser_profiles(
        self, command: BrowserProfileListCommand
    ) -> BrowserProfileListResult:
        return self.browser_profiles.list(self._active_instance(command.instance_id).instance_id)

    async def rename_browser_profile(
        self, command: BrowserProfileRenameCommand
    ) -> BrowserProfileListResult:
        return self.browser_profiles.rename(
            self._active_instance(command.instance_id).instance_id, command.name, command.new_name
        )

    async def delete_browser_profile(
        self, command: BrowserProfileDeleteCommand
    ) -> BrowserProfileListResult:
        return self.browser_profiles.delete(
            self._active_instance(command.instance_id).instance_id, command.name
        )

    async def own_vault_entries(
        self, instance_id: UUID, command: BrowserVaultCommand
    ) -> VaultEntryListResult:
        return self.vault.list(instance_id)

    async def list_vault_entries(self, command: VaultEntryListCommand) -> VaultEntryListResult:
        return self.vault.list(self._active_instance(command.instance_id).instance_id)

    async def add_vault_entry(self, command: VaultEntryAddCommand) -> VaultEntryListResult:
        self._active_instance(command.instance_id)
        return self.vault.add(command)

    async def edit_vault_entry(self, command: VaultEntryEditCommand) -> VaultEntryListResult:
        self._active_instance(command.instance_id)
        return self.vault.edit(command)

    async def delete_vault_entry(self, command: VaultEntryDeleteCommand) -> VaultEntryListResult:
        return self.vault.delete(
            self._active_instance(command.instance_id).instance_id, command.name
        )

    async def write_for_home(self, home: UUID, command: HubWriteCommand) -> HubWriteResult:
        """Run a hub_write operation once, for the approval the hub saw the user give that call."""
        write = home_write(command.tool, command.arguments)
        approved = self.approvals.approved(command.approval_id)
        if approved.name != hub_write.name or requested_write(approved.arguments) != write:
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
            case ImagePrepareCommand():
                result = await self.prepare_image(write, requester=home)
            case LibrarySkillWriteCommand():
                result = await self.library.write_skill(write)
            case LibraryGitAddCommand():
                result = await self.library.add(write)
            case LibraryMove():
                result = await self._move(write)
            case LibraryMcpServerWriteCommand():
                result = await self.library.write_mcp_server(write)
            case FactoryEditCommand():
                result = await self.edit_factory(write)
            case LineSetupCommand():
                result = await self.set_up_line(write)
            case InstanceConvertCommand():
                result = await self.convert_instance(write)
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

    async def remove_factory(self, command: FactoryRemoveCommand) -> FactoryListResult:
        """Refuse while a line runs the factory, or an instance holds one of its roles, and name
        each of them."""
        async with self._factory_dependents:
            workbenches = [
                self._workbench_name(line.workbench_id)
                for line in self.registry.lines()
                if line.factory == command.name
            ]
            holders = [
                record.manifest_id
                for record in self.registry.factory_members(command.name)
                if self._held(record)
            ]
            if workbenches:
                raise FactoryInUse(
                    f'Factory "{command.name}" has lines on workbench {_quoted(workbenches)}. '
                    "Remove them before removing the factory."
                )
            if holders:
                raise FactoryInUse(
                    f'Factory "{command.name}" has instances {", ".join(holders)}. '
                    "Remove them before removing the factory."
                )
            return await self.factories.remove(command)

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

    async def _remove_run_folder(
        self, workbench_id: UUID, command: RunFolderRemoveCommand
    ) -> RunFolderRemoveResult:
        endpoint = await self._endpoint(self._active_workbench(workbench_id))
        return await self._control.remove_run_folder(endpoint, command)

    async def _write_view(self, instance_id: UUID, view: View) -> None:
        command = ViewWriteCommand(id=view.id, title=view.title, spec=view.spec, data=view.data)
        await self._relayed(instance_id, VIEW_WRITE.name, command.model_dump(mode="json"))

    async def run_transcript(self, command: FactoryRunTranscriptCommand) -> FactoryRunTranscript:
        """Read one page of the attempt's transcript from the workbench of the run's line."""
        run = self.registry.run(command.run_id)
        line_id = run.line_id if run is not None else None
        line = next((line for line in self.registry.lines() if line.line_id == line_id), None)
        if line is None or not any(
            (attempt.step, attempt.attempt) == (command.step, command.attempt)
            for attempt in self.registry.attempts(command.run_id)
        ):
            raise FactoryRunNotFound(
                f'Factory run "{command.run_id}" has no attempt {command.attempt} at step '
                f'"{command.step}" on a line.'
            )
        endpoint = await self._endpoint(self._active_workbench(line.workbench_id))
        return await self._control.read_transcript(endpoint, command)

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
        async with self._factory_dependents:
            return await self._create_instance(command, requester)

    async def _create_instance(
        self, command: InstanceCreateCommand, requester: UUID | None
    ) -> LifecycleOperationResult:
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
        accepted = self._begin_instance_create(
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

    def _name_taken(self, manifest_id: str, *, workbench: bool = False) -> bool:
        """Whether an instance holds the name, or a workbench for *workbench*. Instances and
        workbenches name themselves apart."""
        return any(
            record.manifest_id == manifest_id
            and (record.workbench is not None) == workbench
            and self._held(record)
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
        workbench: WorkbenchSetup | None = None,
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
            workbench=workbench,
        )

    def _begin_instance_create(
        self,
        record: ManagedInstance,
        model: str,
        config: Mapping[str, SetupValue],
        secrets: dict[str, str],
        template: InstanceTemplate | None = None,
        picks: Sequence[SkillName] = (),
        requester: UUID | None = None,
    ) -> LifecycleOperationResult:
        async def write(
            operation_id: UUID, staging: Path, artifact: ImageArtifact
        ) -> Sequence[SubscriptionLogin]:
            """Write the instance from its template or its image, and copy in its picks."""
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
            return declared.logins

        operation_id = self._begin_create(record, write, secrets.values(), requester)
        return LifecycleOperationResult(operation_id=operation_id, instance_id=record.instance_id)

    def _begin_create(
        self,
        record: ManagedInstance,
        write: _Write,
        secrets: Collection[str],
        requester: UUID | None = None,
    ) -> UUID:
        operation_id = uuid4()
        self.registry.begin_create(record, operation_id, requester)
        self._schedule(self._collecting_after(self._create(operation_id, record, write, secrets)))
        return operation_id

    async def _create(
        self, operation_id: UUID, record: ManagedInstance, write: _Write, secrets: Collection[str]
    ) -> None:
        """Prepare the image, have *write* fill the record's directory, and publish it, with a
        volume for each login *write* names. A failure says none of the *secrets*."""
        staging = record.path.with_name(f"{record.path.name}.creating")
        unmounted: tuple[StorageItem, ...] = ()
        try:
            self._record(operation_id, "image", "Preparing the selected image.")
            artifact = (
                await self._images.build(_selection(record, record.requested_revision))
            ).artifact
            logins = await write(operation_id, staging, artifact)
            self._record(
                operation_id, "publish", "Creating the container and listing the instance."
            )
            storage = self._storage(
                record.instance_id, record.path, logins, workbench=record.workbench is not None
            )
            if record.path.exists():
                raise FileExistsError(f"Instance directory already exists: {record.path}")
            staging.replace(record.path)
            unmounted = storage
            self._write_hub_url(record)
            self.registry.record_preparation(record.instance_id, artifact, storage)
            await self._runtime.create(self._container(record, artifact.image_id, storage))
            unmounted = ()
            self.registry.seed_logins(record.instance_id, [login.id for login in logins])
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
                self._redact(str(exc) or type(exc).__name__, secrets),
            )

    def _container(
        self, record: ManagedInstance, image: str, storage: tuple[StorageItem, ...]
    ) -> InstanceSpec:
        """The container the hub runs for a record: an instance serves, a workbench runs the
        workbench runtime, which clones its source into its workspace at its first start."""
        command: tuple[str, ...] = ("serve",)
        if record.workbench is not None:
            source = record.workbench.workspace_source
            command = (*_WORKBENCH_COMMAND, *(("--workspace-source", source) if source else ()))
        return InstanceSpec(
            instance_id=record.runtime_id,
            image=image,
            storage=storage,
            command=command,
            env=self._environment(record.path),
            port=_INSTANCE_PORT,
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

    def _hub_route(self, record: ManagedInstance, manifest: Manifest | None) -> dict[str, str]:
        """The hub route, and the scopes an instance holds there, as its environment carries them:
        none unless the registry grants it a scope and the hub has an address."""
        scopes = self._scopes(record, manifest)
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
        if route := self._hub_route(record, self._manifest(record)):
            self._replace_secrets(record.path, route)

    async def prepare_image(
        self, command: ImagePrepareCommand, requester: UUID | None = None
    ) -> ImagePrepareResult:
        """Build or reuse kinby's base image before any instance exists, and describe it.

        *requester* is the instance that asked for it through the hub, if one did."""
        operation_id = uuid4()
        opened = self.registry.begin_preparation(operation_id, "Preparation queued.", requester)
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
        """What the prepared image declares, as its preparation stored it, and the key web search
        needs on a vanilla instance. It never builds."""
        return with_search_key(self._prepared_description(), Tools().search_key)

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
        kind = "Instance" if record.workbench is None else "Workbench"
        raise LoginNotFound(f'{kind} "{record.instance_id}" declares no login "{login_id}".')

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
        API key needs the instance's model, so only then is its manifest read. A workbench names
        its secrets by variable already."""
        if record.workbench is not None or API_KEY_FIELD.name not in fields:
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
        return self.library.pick(
            inspect_instance(record.path), command.skills, command.hash, command.mcp_servers
        )

    async def toolset(self, command: InstanceToolsetGetCommand) -> InstanceToolsetResult:
        """What the instance can do outside its workspace, read from its files, so a stopped
        instance answers too."""
        record = self._active_instance(command.instance_id)
        manifest = inspect_instance(record.path).manifest
        tools = manifest.tools
        variable = tools.search.key_variable
        try:
            authored, held = exposed_tools(record.path), packages(record.path)
        except ValueError:
            # code_run reports a malformed project to the agent; the toolset reads it as empty.
            authored, held = (), ()
        read_only = read_only_tools(record.path)
        return InstanceToolsetResult(
            default_tools=[
                DefaultTool(name=each.name, on=tools.offers(each.name)) for each in DEFAULT_TOOLS
            ],
            search=SearchSetup(
                backend=tools.search,
                variable=variable,
                is_set=bool(self._environment(record.path).get(variable, "").strip()),
            ),
            mcp_servers=self.library.picks(record.path).mcp_servers,
            packages=list(held),
            authored_tools=[
                AuthoredTool(name=name, read_only=name in read_only) for name in authored
            ],
            browser=self._browser_on(manifest),
        )

    async def stop(
        self, command: InstanceStopCommand, requester: UUID | None = None
    ) -> LifecycleOperationResult:
        """Stop the instance gracefully, or escalate the stop already running to a force stop.

        A force stop also escalates the drain any other operation is waiting on, which then
        carries on as it would after a forced stop. That includes an adoption's, whose instance
        the hub does not operate yet.
        """
        draining = self._draining.get(command.instance_id)
        if command.force and draining is not None:
            draining.force.set()
            return LifecycleOperationResult(
                operation_id=draining.operation_id,
                instance_id=command.instance_id,
            )
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

    def _revalidate(self, operation_id: UUID, record: ManagedInstance) -> Instance | None:
        """Revalidate what a container is built on. No revision is resolved here. A workbench has
        no instance to read."""
        self._record(
            operation_id,
            "validate",
            f"Revalidating the retained configuration of instance {record.instance_id}.",
        )
        if record.workbench is None:
            instance = inspect_instance(record.path)
        elif record.path.is_dir():
            instance = None
        else:
            raise ValueError(f"The workbench directory {record.path} is missing.")
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
        await self._close_browsers(record.instance_id)
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
        await self._runtime.create(self._container(record, image, record.storage))

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
        lock = read_lock(record.path)
        if taken or lock.skills or lock.mcp_servers:
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
        workbench = record.workbench is not None
        if self._name_taken(record.manifest_id, workbench=workbench):
            raise NameTaken(
                f'{"A workbench" if workbench else "An instance"} named "{record.manifest_id}" '
                "exists already. Remove it before restoring this one."
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
        instance = self._revalidate(operation_id, record)
        if instance is not None and instance.manifest.id != record.manifest_id:
            raise ValueError(
                f'{record.path} now holds instance "{instance.manifest.id}", '
                f'not "{record.manifest_id}".'
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
        """Revalidate the preview, then delete each target."""
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
        await self._delete_storage(operation_id, record, targets)
        return targets

    async def _delete_storage(
        self, operation_id: UUID, record: ManagedInstance, targets: InstanceDeletePreviewResult
    ) -> None:
        """Delete each target and release it from the inventory, then mark the record deleted.

        A target leaves the inventory only once it is gone. A retry therefore picks up
        a target that failed, and never reaches for one that was already deleted, even
        when something unrelated appears in its place later.
        """
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
                    await self._close_browsers(instance_id)
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

    async def _close_browsers(self, instance_id: UUID) -> None:
        """A stopped instance cannot close its turns' browsers, so the hub closes them."""
        if self._browsers is not None:
            await self._browsers.close_instance(instance_id)

    async def _take_down(self, record: ManagedInstance, pending: PendingStop) -> None:
        """Wait for the instance's own drain, then take its container down and watch it go."""
        operation_id = pending.operation_id
        endpoint = await self._endpoint(record)
        self._draining[record.instance_id] = pending
        try:
            state = await self._drain(operation_id, endpoint, pending)
        finally:
            del self._draining[record.instance_id]
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
        return self._begin_instance_create(record, PLACEHOLDER_MODEL, {}, {}, picks=picks)

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
        factories = {
            name: self.runs.factory(name)
            for name in {record.factory.factory for record in records if record.factory}
        }
        return InstanceListResult(
            instances=[
                await self._summary(
                    record,
                    hub_revision,
                    served,
                    factories[record.factory.factory] if record.factory else None,
                )
                for record in records
            ]
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
        factory: FactoryFile | None,
    ) -> InstanceSummary:
        """*factory* is the file of the factory the instance holds a role of."""
        files = self._files(record)
        observed = await self._observed(record, files)
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
            setup_pending=self._setup(record, files, factory).pending,
            home=record.home,
            factory_role=(
                FactoryRole(factory=record.factory.factory, name=record.factory.name)
                if record.factory is not None
                else None
            ),
            notices=[*self._notices_of(record, files, hub_revision, served).values()],
            needs_human=self.runs.needing_human(record, factory),
        )

    def _notices_of(
        self,
        record: ManagedInstance,
        files: _InstanceFiles,
        hub_revision: str,
        served: Mapping[tuple[FactoryName, InstanceName], InstanceTemplate],
    ) -> dict[str, InstanceNotice]:
        manifest = files.manifest
        return _notices(
            record,
            hub_revision,
            served,
            self._hub_route(record, manifest),
            files.secrets,
            {} if manifest is None else failed_mcp_servers(manifest.state_dir),
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
            notices = self._notices_of(record, self._files(record), hub_revision, served)
            for key, notice in notices.items():
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
        files = self._files(record)
        observed = await self._observed(record, files)
        factory = self.runs.factory(record.factory.factory) if record.factory else None
        return InstanceStatusResult(
            instance_id=record.instance_id,
            process=observed.process,
            readiness=observed.readiness,
            setup=self._setup(record, files, factory),
            home=record.home,
            detail=observed.detail,
            active_operation_id=self.registry.active_operation(record.instance_id),
            recreate_reasons=[*await self._recreate_reasons(record, observed.process)],
            repository_commands=[*self.runs.held_commands(record)],
            browser=self._browser_on(files.manifest),
        )

    async def _recreate_reasons(
        self, record: ManagedInstance, process: ProcessState
    ) -> tuple[RecreateReason, ...]:
        """Secrets replaced since the container was created."""
        if process in {ProcessState.MISSING, ProcessState.UNAVAILABLE}:
            return ()
        return (RecreateReason.SECRETS,) if await self._secrets_replaced(record) else ()

    async def _observed(self, record: ManagedInstance, files: _InstanceFiles) -> ObservedProcess:
        """Read the runtime once. A runtime that cannot be read reports the instance unavailable."""
        secrets = files.secrets.values()
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
        """What the instance asks for: its template's as created, or its image's. A workbench
        declares only the logins it was created with."""
        if record.workbench is not None:
            return DeclaredSetup(setup_fields=[], logins=list(record.workbench.logins))
        if record.factory is not None:
            return record.factory.setup
        return self.registry.description()

    @classmethod
    def _files(cls, record: ManagedInstance) -> _InstanceFiles:
        return _InstanceFiles(cls._environment(record.path), cls._manifest(record))

    @staticmethod
    def _manifest(record: ManagedInstance) -> Manifest | None:
        try:
            return inspect_instance(record.path).manifest
        except ManifestError:
            return None

    @classmethod
    def _model(cls, record: ManagedInstance) -> ModelName | None:
        manifest = cls._manifest(record)
        return None if manifest is None else manifest.models.main

    @staticmethod
    def _code_secrets(record: ManagedInstance) -> tuple[str, ...]:
        try:
            return declared_secrets(record.path)
        except ValueError:
            # code_run reports a malformed project to the agent; setup reads it as declaring none.
            return ()

    def _setup(
        self, record: ManagedInstance, files: _InstanceFiles, factory: FactoryFile | None
    ) -> InstanceSetup:
        """The logins and secrets the instance's stored descriptor declares, how they stand, and
        the lists of repository commands it is missing from those *factory* names."""
        description = self._declared_setup(record)
        declared = description.logins if description is not None else []
        running = {
            login.id: running[0]
            for login in declared
            if (running := self.registry.running_login(record.instance_id, login.id))
        }
        manifest = files.manifest
        return instance_setup(
            description,
            logins=self.registry.login_states(record.instance_id),
            running=running,
            secrets=files.secrets,
            links=self.registry.secret_links(record.instance_id),
            model=manifest.models.main if manifest is not None else None,
            search_key=manifest.tools.search_key if manifest is not None else None,
            missing_commands=self.runs.missing_commands(record, factory),
            code_secrets=self._code_secrets(record),
        )

    async def logs(self, command: InstanceLogsCommand) -> InstanceLogsResult:
        record = self._active_instance(command.instance_id)
        chunks = [chunk async for chunk in self._runtime.logs(record.runtime_id, tail=command.tail)]
        text = b"".join(chunks).decode(errors="replace")
        return InstanceLogsResult(
            instance_id=record.instance_id,
            text=self._redact(text, self._environment(record.path).values()),
        )

    async def create_workbench(self, command: WorkbenchCreateCommand) -> WorkbenchCreateResult:
        """Accept a workbench only once its name is free, kinby ships its recipe, each secret
        names a variable, the hub keeps each hub secret it links and each login can sign in. Its
        image builds and its container is created in the background, with a volume for each
        login."""
        async with self._factory_dependents:
            return await self._create_workbench(command)

    async def _create_workbench(self, command: WorkbenchCreateCommand) -> WorkbenchCreateResult:
        secrets = {name: value.get_secret_value() for name, value in command.secrets.items()}
        errors = self._secret_errors(secrets, command.hub_secrets)
        if self._name_taken(command.name, workbench=True):
            errors["name"] = f'A workbench named "{command.name}" exists already.'
        if command.recipe is not None and not ships_recipe(command.recipe):
            errors["recipe"] = f'kinby does not ship image recipe "{command.recipe}".'
        if problems := list(login_declaration_problems(command.logins)):
            errors["logins"] = " ".join(problems)
        if errors:
            raise InvalidSetup(errors)
        record = self._new_record(
            command.name,
            workbench=WorkbenchSetup(
                recipe=command.recipe,
                workspace_source=command.workspace_source,
                values=command.values,
                logins=tuple(command.logins),
                browser=command.browser,
            ),
        )
        secrets = self._with_hub_secrets(secrets, command.hub_secrets)
        return WorkbenchCreateResult(
            operation_id=self._begin_workbench(record, secrets, command.hub_secrets),
            workbench_id=record.instance_id,
            workbench=await self._workbench(record, secrets),
        )

    def _secret_errors(
        self, secrets: Mapping[str, str], hub_secrets: Mapping[str, HubSecretName]
    ) -> dict[str, str]:
        """What is wrong with each variable's hub secret, and the first variable no environment
        variable can be named."""
        errors = link_errors(secrets, hub_secrets, {}, self.secrets.values())
        invalid = [
            name for name in {**secrets, **hub_secrets} if ENVIRONMENT_NAME.fullmatch(name) is None
        ]
        if invalid:
            errors["secrets"] = f'Invalid environment variable name: "{invalid[0]}".'
        return errors

    def _with_hub_secrets(
        self, secrets: dict[str, str], hub_secrets: Mapping[str, HubSecretName]
    ) -> dict[str, str]:
        kept = self.secrets.values()
        return secrets | {variable: kept[name] for variable, name in hub_secrets.items()}

    def _begin_workbench(
        self,
        record: ManagedInstance,
        secrets: dict[str, str],
        hub_secrets: Mapping[str, HubSecretName],
    ) -> UUID:
        """Begin creating the workbench with *secrets*, linked to *hub_secrets*: its image builds
        and its container is created in the background, with a volume for each login."""
        assert record.workbench is not None
        logins = record.workbench.logins

        async def write(
            operation_id: UUID, staging: Path, artifact: ImageArtifact
        ) -> Sequence[SubscriptionLogin]:
            self._record(operation_id, "initialize", "Writing the workbench's secrets.")
            staging.mkdir()
            self._write_secrets(
                staging / ".env", secrets | {CONTROL_TOKEN_VARIABLE: new_control_token()}
            )
            return logins

        operation_id = self._begin_create(record, write, secrets.values())
        self.registry.link_secrets(record.instance_id, hub_secrets)
        return operation_id

    async def list_workbenches(self, command: WorkbenchListCommand) -> WorkbenchListResult:
        return WorkbenchListResult(
            workbenches=[
                await self._workbench(record, self._environment(record.path))
                for record in self.registry.listed_workbenches(removed=command.removed)
            ]
        )

    async def get_workbench(self, command: WorkbenchGetCommand) -> Workbench:
        record = self._active_workbench(command.workbench_id)
        return await self._workbench(record, self._environment(record.path))

    async def start_workbench(self, command: WorkbenchStartCommand) -> WorkbenchOperationResult:
        self._active_workbench(command.workbench_id)
        started = await self.start(InstanceStartCommand(instance_id=command.workbench_id))
        return WorkbenchOperationResult(
            operation_id=started.operation_id, workbench_id=command.workbench_id
        )

    async def stop_workbench(self, command: WorkbenchStopCommand) -> WorkbenchOperationResult:
        self._active_workbench(command.workbench_id)
        stopping = await self.stop(
            InstanceStopCommand(instance_id=command.workbench_id, force=command.force)
        )
        return WorkbenchOperationResult(
            operation_id=stopping.operation_id, workbench_id=command.workbench_id
        )

    async def recreate_workbench(
        self, command: WorkbenchRecreateCommand
    ) -> WorkbenchOperationResult:
        self._active_workbench(command.workbench_id)
        recreating = await self.recreate(InstanceRecreateCommand(instance_id=command.workbench_id))
        return WorkbenchOperationResult(
            operation_id=recreating.operation_id, workbench_id=command.workbench_id
        )

    async def update_workbench(self, command: WorkbenchUpdateCommand) -> WorkbenchOperationResult:
        """Move the workbench onto the image its recipe builds at the revision."""
        self._active_workbench(command.workbench_id)
        updating = await self.update(
            InstanceUpdateCommand(instance_id=command.workbench_id, revision=command.revision)
        )
        return WorkbenchOperationResult(
            operation_id=updating.operation_id, workbench_id=command.workbench_id
        )

    async def remove_workbench(self, command: WorkbenchRemoveCommand) -> WorkbenchOperationResult:
        self._active_workbench(command.workbench_id)
        removing = await self.remove(InstanceRemoveCommand(instance_id=command.workbench_id))
        return WorkbenchOperationResult(
            operation_id=removing.operation_id, workbench_id=command.workbench_id
        )

    async def restore_workbench(self, command: WorkbenchRestoreCommand) -> WorkbenchOperationResult:
        self._removed_workbench(command.workbench_id)
        restoring = await self.restore(InstanceRestoreCommand(instance_id=command.workbench_id))
        return WorkbenchOperationResult(
            operation_id=restoring.operation_id, workbench_id=command.workbench_id
        )

    async def set_workbench_secrets(
        self, command: WorkbenchSecretsSetCommand
    ) -> WorkbenchOperationResult:
        """Replace or link the workbench's secrets. A running workbench applies them once it is
        recreated."""
        self._active_workbench(command.workbench_id)
        replacing = await self.set_secrets(
            InstanceSecretsSetCommand(
                instance_id=command.workbench_id,
                secrets=command.secrets,
                hub_secrets=command.hub_secrets,
            )
        )
        return WorkbenchOperationResult(
            operation_id=replacing.operation_id, workbench_id=command.workbench_id
        )

    async def set_workbench_commands(self, command: WorkbenchCommandsSetCommand) -> Workbench:
        self._active_workbench(command.workbench_id)
        self.registry.set_workbench_commands(command.workbench_id, command.name, command.commands)
        record = self._active_workbench(command.workbench_id)
        return await self._workbench(record, self._environment(record.path))

    async def start_workbench_login(
        self, command: WorkbenchLoginStartCommand
    ) -> WorkbenchOperationResult:
        """Sign one of the workbench's subscription logins in, or return the one running."""
        self._active_workbench(command.workbench_id)
        started = await self.start_login(
            InstanceLoginStartCommand(instance_id=command.workbench_id, login_id=command.login_id)
        )
        return WorkbenchOperationResult(
            operation_id=started.operation_id, workbench_id=command.workbench_id
        )

    async def workbench_picks(self, command: WorkbenchPicksGetCommand) -> WorkbenchPicksResult:
        picks = self.library.picks(self._active_workbench(command.workbench_id).path)
        return WorkbenchPicksResult(picks=picks.picks, hash=picks.hash)

    async def set_workbench_picks(self, command: WorkbenchPicksSetCommand) -> WorkbenchPicksResult:
        """Change the workbench's picks in its directory, which its container mounts, as an
        instance's are."""
        record = self._active_workbench(command.workbench_id)
        picks = self.library.pick(record.path, command.skills, command.hash)
        return WorkbenchPicksResult(picks=picks.picks, hash=picks.hash)

    async def delete_workbench(self, command: WorkbenchDeleteCommand) -> WorkbenchOperationResult:
        """Drain the workbench and remove its container, then delete its directory and named
        volumes, in one operation. A removed workbench has only its storage left, and a deletion
        that failed part way resumes where it stopped.

        Refuse while a line on it has unfinished runs, and name each of them.
        """
        record = self._kept_workbench(command.workbench_id)
        unfinished = [
            run
            for line in self.registry.lines()
            if line.workbench_id == record.instance_id
            for run in self.registry.runs(line.factory, UNFINISHED, line_id=line.line_id)
        ]
        if unfinished:
            raise WorkbenchInUse(
                f'Workbench "{record.manifest_id}" has unfinished runs: {_named(unfinished)}. '
                "Finish or cancel them before deleting it."
            )
        record = self._claimed(record)
        operation_id = uuid4()
        self.registry.record_operation(
            operation_id, record.instance_id, OperationKind.DELETE, "Deletion queued."
        )
        # A force stop escalates this drain the way it escalates a stop's.
        pending = PendingStop(operation_id, asyncio.Event())
        self._stopping[record.instance_id] = pending
        self._schedule(self._collecting_after(self._delete_workbench(record.instance_id, pending)))
        return WorkbenchOperationResult(operation_id=operation_id, workbench_id=record.instance_id)

    async def _delete_workbench(self, workbench_id: UUID, pending: PendingStop) -> None:
        operation_id = pending.operation_id
        try:
            try:
                async with self._locks.setdefault(workbench_id, asyncio.Lock()):
                    record = self._kept_workbench(workbench_id)
                    try:
                        if record.active:
                            await self._take_away(record, pending)
                        await self._delete_storage(
                            operation_id, record, self._deletion_targets(record)
                        )
                    except Exception as exc:
                        self._fail(operation_id, record, str(exc) or type(exc).__name__)
                        return
                    self.registry.finish_operation(
                        operation_id,
                        OperationState.SUCCEEDED,
                        "Workbench deleted. Its directory and named volumes are gone.",
                    )
            except asyncio.CancelledError:
                self._fail_if_unfinished(operation_id)
                raise
        finally:
            self._stopping.pop(workbench_id, None)

    def _active_workbench(self, workbench_id: UUID) -> ManagedInstance:
        record = self.registry.instance(workbench_id)
        if record is None or not record.active or record.workbench is None:
            raise WorkbenchNotFound(f'Workbench "{workbench_id}" was not found.')
        return record

    def _removed_workbench(self, workbench_id: UUID) -> ManagedInstance:
        record = self.registry.instance(workbench_id)
        if (
            record is None
            or record.intended_state is not IntendedState.REMOVED
            or record.workbench is None
        ):
            raise WorkbenchNotFound(f'Removed workbench "{workbench_id}" was not found.')
        return record

    def _kept_workbench(self, workbench_id: UUID) -> ManagedInstance:
        """An active workbench, or a removed one whose storage the hub retains."""
        record = self.registry.instance(workbench_id)
        if (
            record is None
            or record.workbench is None
            or not (record.active or record.intended_state is IntendedState.REMOVED)
        ):
            raise WorkbenchNotFound(f'Workbench "{workbench_id}" was not found.')
        return record

    async def _workbench(self, record: ManagedInstance, environment: dict[str, str]) -> Workbench:
        """The workbench's record, with the names of the secrets *environment* holds, and how
        each of its logins stands. The variables of its hub route are no secrets of its own."""
        assert record.workbench is not None
        observed = await self._observed(record, _InstanceFiles(environment, None))
        states = self.registry.login_states(record.instance_id)
        own = environment.keys() - {CONTROL_TOKEN_VARIABLE, HUB_URL_VARIABLE, HUB_SCOPES_VARIABLE}
        return Workbench(
            workbench_id=record.instance_id,
            name=record.manifest_id,
            recipe=record.workbench.recipe,
            workspace_source=record.workbench.workspace_source,
            values=record.workbench.values,
            secrets=sorted(own),
            hub_secrets=self.registry.secret_links(record.instance_id),
            intended_state=record.intended_state,
            process=observed.process,
            detail=observed.detail,
            recreate_reasons=[*await self._recreate_reasons(record, observed.process)],
            repository_commands=[
                RepositoryCommandList(name=name, commands=commands)
                for name, commands in sorted(record.workbench.commands.items())
            ],
            logins=[
                LoginSetup(
                    id=login.id,
                    label=login.label,
                    description=login.description,
                    state=states.get(login.id, LoginState.PENDING),
                    operation_id=(
                        running[0]
                        if (running := self.registry.running_login(record.instance_id, login.id))
                        else None
                    ),
                )
                for login in record.workbench.logins
            ],
            browser=record.workbench.browser,
        )

    async def describe_line_setup(self, command: LineSetupDescribeCommand) -> LineSetupDescription:
        """What setting the factory up on the chosen workbench asks for, or with none, what the
        factory and a new workbench ask for. A workbench the factory cannot run on is refused."""
        factory = await self._workbench_factory(command.factory)
        if command.workbench_id is None:
            return _asked(factory, command.model, self._new_holding(), new=True)
        record = self._active_workbench(command.workbench_id)
        if errors := self._misfit(command.factory, factory, record):
            raise InvalidSetup(errors)
        return _asked(factory, command.model, self._holding(record), new=False)

    async def _workbench_factory(self, name: FactoryName) -> WorkbenchFactory:
        factory = await self.factories.checked(name)
        if isinstance(factory, FactoryFile):
            raise InvalidSetup(
                {
                    "factory": f'Factory "{name}" declares instances, so its steps run in them. '
                    "Only a factory that runs on a workbench is set up on one."
                }
            )
        return factory

    def _misfit(
        self, name: FactoryName, factory: WorkbenchFactory, record: ManagedInstance
    ) -> dict[str, str]:
        """Why the factory is no new line on the workbench, whatever the setup answers."""
        assert record.workbench is not None
        if any(
            line.factory == name and line.workbench_id == record.instance_id
            for line in self.registry.lines()
        ):
            return {
                "factory": f'Factory "{name}" is set up on workbench "{record.manifest_id}" '
                "already."
            }
        if factory.requires.image != record.workbench.recipe:
            return {
                "factory": f'Factory "{name}" requires {_image(factory.requires.image)}, '
                f'and workbench "{record.manifest_id}" runs {_image(record.workbench.recipe)}.'
            }
        return {}

    def _holding(self, record: ManagedInstance) -> _Holding:
        """What the workbench holds. It has a browser when it was created with one and the hub
        runs the browser service."""
        assert record.workbench is not None
        return _Holding(
            secrets=self._environment(record.path).keys(),
            values=record.workbench.values.keys(),
            commands=record.workbench.commands.keys(),
            signed_in=[
                login_id
                for login_id, state in self.registry.login_states(record.instance_id).items()
                if state is LoginState.SIGNED_IN
            ],
            browser=Scope.BROWSER_OPEN in self._scopes(record, None),
        )

    def _new_holding(self) -> _Holding:
        """What a new workbench holds: a browser, if the hub runs the browser service."""
        return _Holding(browser=self._browsers is not None)

    async def set_up_line(self, command: LineSetupCommand) -> LineSetupResult:
        """Accept a line only once the factory passes its check and runs on a workbench, the
        workbench holds all it requires on its own image once the setup's answers land, the
        answers are sound, and the owner is an instance. The home instance owns it unless the
        command names another. Then create the workbench when the command describes a new one,
        or store the answers on the chosen one."""
        async with self._factory_dependents:
            return await self._set_up_line(command)

    async def _set_up_line(self, command: LineSetupCommand) -> LineSetupResult:
        factory = await self._workbench_factory(command.factory)
        secrets = {name: value.get_secret_value() for name, value in command.secrets.items()}
        errors = self._secret_errors(secrets, command.hub_secrets)
        fields = {
            field.name: field.setup_field(SetupFieldKind.CONFIG)
            for field in factory.requires.values
        }
        for name, value in command.values.items():
            if name in fields and (problem := value_problem(fields[name], value)) is not None:
                errors[name] = f"{fields[name].label} {problem}."
        if command.workbench is None:
            assert command.workbench_id is not None
            record = self._active_workbench(command.workbench_id)
            errors |= self._misfit(command.factory, factory, record)
            holding, field = self._holding(record), "workbench_id"
        else:
            record = self._new_record(
                command.workbench.name,
                workbench=WorkbenchSetup(
                    recipe=factory.requires.image,
                    workspace_source=command.workbench.workspace_source,
                    values=dict(command.values),
                    commands=dict(command.commands),
                    browser=factory.requires.browser,
                ),
            )
            if self._name_taken(record.manifest_id, workbench=True):
                errors["workbench.name"] = (
                    f'A workbench named "{record.manifest_id}" exists already.'
                )
            holding, field = self._new_holding(), "workbench"
        lacking = _lacking(_asked(factory, command.model, holding.answered(command), new=False))
        if lacking:
            errors[field] = (
                f'Workbench "{record.manifest_id}" lacks what factory "{command.factory}" '
                f"requires: {', '.join(lacking)}."
            )
        owner_id = command.owner_id or next(
            (
                instance.instance_id
                for instance in self.registry.managed_instances()
                if instance.home
            ),
            None,
        )
        owner = None if owner_id is None else self.registry.instance(owner_id)
        if owner is None or not owner.active or owner.workbench is not None:
            errors["owner_id"] = f'Instance "{owner_id}" was not found.'
        if errors:
            raise InvalidSetup(errors)
        assert owner is not None
        intake = None
        if factory.intake is not None:
            intake = RoutineName(f"{command.factory}-{record.manifest_id}")
            routine = _intake_routine(command.factory, record.manifest_id, factory.intake)
            await self._write_intake(owner, intake, routine)
        if command.workbench is None:
            operation_id = await self._store_answers(record, command)
        else:
            secrets = self._with_hub_secrets(secrets, command.hub_secrets)
            operation_id = self._begin_workbench(record, secrets, command.hub_secrets)
        line = Line(
            line_id=uuid4(),
            factory=command.factory,
            workbench_id=record.instance_id,
            owner_id=owner.instance_id,
            model=command.model,
            usd_per_day=command.usd_per_day,
            intake=intake,
        )
        self.registry.add_line(line)
        return LineSetupResult(line=line, operation_id=operation_id)

    async def _store_answers(
        self, record: ManagedInstance, command: LineSetupCommand
    ) -> UUID | None:
        """Store the setup's values and lists of repository commands on the workbench, and start
        the replacement of its secrets, whose operation this returns."""
        self.registry.set_workbench_values(record.instance_id, command.values)
        for name, commands in command.commands.items():
            self.registry.set_workbench_commands(record.instance_id, name, commands)
        if not command.secrets and not command.hub_secrets:
            return None
        replacing = await self.set_secrets(
            InstanceSecretsSetCommand(
                instance_id=record.instance_id,
                secrets=command.secrets,
                hub_secrets=command.hub_secrets,
            )
        )
        return replacing.operation_id

    async def _write_intake(self, owner: ManagedInstance, name: RoutineName, content: str) -> None:
        """Create the line's intake routine in its owner, or refuse the setup with why the owner
        did not take it."""
        command = RoutineWriteCommand(name=name, content=content, hash=None)
        try:
            await self._relayed(
                owner.instance_id, ROUTINE_WRITE.name, command.model_dump(mode="json")
            )
        except (InstanceRefused, InstanceUnavailable) as exc:
            why = exc.error.explained if isinstance(exc, InstanceRefused) else str(exc)
            raise InvalidSetup(
                {
                    "owner_id": f'Instance "{owner.manifest_id}" did not take the intake routine '
                    f'"{name}": {why}'
                }
            ) from exc

    async def _remove_intake(self, line: Line) -> None:
        """Delete the line's intake routine from its owner. One already gone, or gone with its
        owner, needs nothing."""
        if line.intake is None:
            return
        read = RoutineReadCommand(name=line.intake)
        try:
            held = RoutineFile.model_validate(
                await self._relayed(line.owner_id, ROUTINE_READ.name, read.model_dump(mode="json"))
            )
            delete = RoutineDeleteCommand(name=line.intake, hash=held.hash)
            await self._relayed(line.owner_id, ROUTINE_DELETE.name, delete.model_dump(mode="json"))
        except ManagedInstanceNotFound:
            return
        except InstanceRefused as exc:
            if exc.error.code is not ErrorCode.NOT_FOUND:
                raise

    async def list_lines(self, command: LineListCommand) -> LineListResult:
        return LineListResult(lines=self.registry.lines())

    async def get_line(self, command: LineGetCommand) -> Line:
        line = next(
            (line for line in self.registry.lines() if line.line_id == command.line_id), None
        )
        if line is None:
            raise LineNotFound(f'Line "{command.line_id}" was not found.')
        return line

    async def update_line(self, command: LineUpdateCommand) -> Line:
        """Replace the line's model and daily budget. A run's next step takes them."""
        line = await self.get_line(LineGetCommand(line_id=command.line_id))
        updated = line.model_copy(
            update={"model": command.model, "usd_per_day": command.usd_per_day}
        )
        self.registry.update_line(updated)
        return updated

    async def set_line_owner(self, command: LineOwnerSetCommand) -> Line:
        """Move the line, its runs, its intake routine and the memory marked with its workbench to
        another instance, so the new owner hears the runs' stops and hands the line work.

        Each part moved is put back when a later one fails, so a change that fails leaves them all
        with the old owner. An old owner that keeps another line on the workbench keeps a copy of
        the memory, since it holds that line's lessons too and nothing tells them apart.
        """
        async with self._factory_dependents:
            line = await self.get_line(LineGetCommand(line_id=command.line_id))
            owner = self.registry.instance(command.owner_id)
            old = self.registry.instance(line.owner_id)
            if owner is None or not owner.active or owner.workbench is not None:
                raise InvalidSetup({"owner_id": f'Instance "{command.owner_id}" was not found.'})
            if owner.instance_id == line.owner_id:
                return line
            assert old is not None
            self._refuse_running(line)
            moved = line.model_copy(update={"owner_id": owner.instance_id})
            source, target = GraphStore(old.path), GraphStore(owner.path)
            memory = source.marked(self._workbench_name(line.workbench_id))
            async with AsyncExitStack() as undo:
                for node in target.take(memory):
                    undo.callback(target.prune, node)
                if line.intake is not None:
                    read = RoutineReadCommand(name=line.intake)
                    held = RoutineFile.model_validate(
                        await self._relayed(
                            old.instance_id, ROUTINE_READ.name, read.model_dump(mode="json")
                        )
                    )
                    await self._write_intake(owner, line.intake, held.content)
                    undo.push_async_callback(self._remove_intake, moved)
                    await self._remove_intake(line)
                    undo.push_async_callback(self._write_intake, old, line.intake, held.content)
                self._refuse_running(line)
                if not any(
                    other.line_id != line.line_id
                    and (other.owner_id, other.workbench_id) == (old.instance_id, line.workbench_id)
                    for other in self.registry.lines()
                ):
                    undo.callback(source.take, memory)
                    for node in memory:
                        source.prune(node)
                self.registry.set_line_owner(line.line_id, owner.instance_id)
                undo.pop_all()
            return moved

    def _refuse_running(self, line: Line) -> None:
        """A step that ended part way through the change would write its views and tell its stop,
        whose recap the owner remembers, to the old owner after the memory left it."""
        running = self.registry.runs(
            line.factory, (FactoryRunStatus.RUNNING,), line_id=line.line_id
        )
        if running:
            raise LineBusy(
                f'Factory run "{running[0].run_id}" of the line is running step '
                f'"{running[0].step}". Change the owner once the step ends.'
            )

    async def convert_instance(self, command: InstanceConvertCommand) -> Line:
        """Convert the stopped instance into a workbench and a line of its role's factory on it,
        owned by the home instance, whose intake takes the deliveries to the instance's signal
        path. The workbench keeps the instance's id, directory, volumes, secrets, logins, picks and
        repository commands. Its memory moves to the home instance, marked with the workbench.

        Each part done is undone when a later one fails, so a conversion that fails leaves the
        instance as it was.
        """
        async with (
            self._factory_dependents,
            self._locks.setdefault(command.instance_id, asyncio.Lock()),
        ):
            record = self._not_signing_in(self._claimed(self._active_instance(command.instance_id)))
            member = record.factory
            if member is None:
                raise InvalidSetup(
                    {"instance_id": f'Instance "{record.manifest_id}" picked no factory role.'}
                )
            self._refuse_in_use(record, member)
            factory = await self._workbench_factory(member.factory)
            if factory.requires.image != member.recipe:
                raise InvalidSetup(
                    {
                        "factory": f'Factory "{member.factory}" requires '
                        f'{_image(factory.requires.image)}, and instance "{record.manifest_id}" '
                        f"runs {_image(member.recipe)}."
                    }
                )
            if self._name_taken(record.manifest_id, workbench=True):
                raise InvalidSetup(
                    {"instance_id": f'A workbench named "{record.manifest_id}" exists already.'}
                )
            home = next((other for other in self.registry.managed_instances() if other.home), None)
            if home is None or not home.active:
                raise InvalidSetup({"owner_id": "The hub has no home instance to own the line."})
            manifest = inspect_instance(record.path).manifest
            workbench = replace(
                record,
                factory=None,
                storage=(*self._factories_mount(), *record.storage),
                workbench=WorkbenchSetup(
                    recipe=member.recipe,
                    workspace_source=manifest.workspace.source,
                    values={},
                    commands=repository_commands(record.path),
                    logins=tuple(member.setup.logins),
                    browser=factory.requires.browser,
                ),
            )
            model = manifest.models.main
            lacking = _lacking(_asked(factory, model, self._holding(workbench), new=False))
            if lacking:
                raise InvalidSetup(
                    {
                        "instance_id": f'Instance "{record.manifest_id}" lacks what factory '
                        f'"{member.factory}" requires: {", ".join(lacking)}.'
                    }
                )
            line = Line(
                line_id=uuid4(),
                factory=member.factory,
                workbench_id=record.instance_id,
                owner_id=home.instance_id,
                model=model,
                usd_per_day=manifest.budgets.usd_per_day,
                intake=(
                    None
                    if factory.intake is None
                    else RoutineName(f"{member.factory}-{record.manifest_id}")
                ),
            )
            await self._convert(record, workbench, line, factory.intake, home)
            return line

    def _refuse_in_use(self, record: ManagedInstance, member: FactoryMember) -> None:
        """A running instance, or one a run or a line needs, is not converted, so nothing is cut
        short or left with an owner that is no instance."""
        if record.intended_state is not IntendedState.STOPPED:
            raise InstanceInUse(
                f'Instance "{record.manifest_id}" is running. Stop it before converting it.'
            )
        unfinished = [
            run
            for run in self.registry.runs(member.factory, UNFINISHED)
            if run.line_id is None and self.runs.holder(run, member.name) == record.instance_id
        ]
        if unfinished:
            raise InstanceInUse(
                f'Instance "{record.manifest_id}" has unfinished runs: {_named(unfinished)}. '
                "Finish or cancel them before converting it."
            )
        owned = [
            f'"{line.factory}" on "{self._workbench_name(line.workbench_id)}"'
            for line in self.registry.lines()
            if line.owner_id == record.instance_id
        ]
        if owned:
            raise InstanceInUse(
                f'Instance "{record.manifest_id}" owns the lines {", ".join(owned)}. '
                "Give them another owner before converting it."
            )

    async def _convert(
        self,
        record: ManagedInstance,
        workbench: ManagedInstance,
        line: Line,
        intake: WorkbenchIntake | None,
        home: ManagedInstance,
    ) -> None:
        """Move the memory and the intake to *home*, replace the instance's container with the
        workbench's, and record the conversion, undoing each part done when a later one fails."""
        source, target = GraphStore(record.path), GraphStore(home.path)
        memory = source.as_marked(record.manifest_id)
        environment = record.path / ENV_NAME
        held = environment.read_text(encoding="utf-8")
        assert record.image_id is not None
        container = self._container(record, record.image_id, record.storage)
        async with AsyncExitStack() as undo:
            for node in target.take(memory):
                undo.callback(target.prune, node)
            if line.intake is not None and intake is not None:
                routine = _intake_routine(line.factory, record.manifest_id, intake)
                await self._write_intake(home, line.intake, routine)
                undo.push_async_callback(self._remove_intake, line)
            try:
                undo.callback(environment.write_text, held, encoding="utf-8")
                # Its hub route granted an instance's scopes, which a workbench holds none of.
                self._write_secrets(
                    environment,
                    {
                        name: value
                        for name, value in self._environment(record.path).items()
                        if name not in {HUB_URL_VARIABLE, HUB_SCOPES_VARIABLE}
                    },
                )
                if (await self._runtime.status(record.runtime_id)).state != "absent":
                    await self._runtime.remove(record.runtime_id)
                    undo.push_async_callback(self._runtime.create, container)
                self._write_hub_url(workbench)
                await self._runtime.create(
                    self._container(workbench, record.image_id, workbench.storage)
                )
                undo.push_async_callback(self._runtime.remove, record.runtime_id)
                self.registry.convert_instance(workbench, line)
            except Exception as exc:
                raise ConversionFailed(
                    f'Instance "{record.manifest_id}" was not converted: '
                    f"{str(exc) or type(exc).__name__}"
                ) from exc
            undo.pop_all()
        for node in memory:
            source.prune(node)

    async def remove_line(self, command: LineRemoveCommand) -> LineListResult:
        """Remove the line and its intake routine. Its workbench keeps its clone, its secrets and
        its command lists. An owner that cannot delete the routine keeps the line."""
        line = await self.get_line(LineGetCommand(line_id=command.line_id))
        await self._remove_intake(line)
        self.registry.remove_line(command.line_id)
        return await self.list_lines(LineListCommand())

    async def hand_in(self, command: LineHandInCommand) -> FactoryRun:
        """Start a run on the line's workbench, or return the line's unfinished run of the same
        work item."""
        line = await self.get_line(LineGetCommand(line_id=command.line_id))
        return await self.runs.hand_in(line, command.work_item)

    async def owned_lines(
        self, instance_id: UUID, command: LineOwnedListCommand
    ) -> LineOwnedListResult:
        """The lines the instance owns, each with its unfinished runs."""
        return LineOwnedListResult(
            lines=[
                OwnedLine(
                    line=line,
                    workbench=self._workbench_name(line.workbench_id),
                    runs=self.registry.runs(line.factory, UNFINISHED, line_id=line.line_id),
                )
                for line in self.registry.lines()
                if line.owner_id == instance_id
            ]
        )

    async def owned_hand_in(self, instance_id: UUID, command: LineHandInCommand) -> FactoryRun:
        """Hand a work item to a line the instance owns, as hand_in does."""
        line = next(
            (line for line in self.registry.lines() if line.line_id == command.line_id), None
        )
        if line is None or line.owner_id != instance_id:
            raise PermissionDenied(f'Line "{command.line_id}" is no line of this instance.')
        return await self.runs.hand_in(line, command.work_item)

    async def read_owned_workspace[Command: _WorkspaceRead, Result: ContractModel](
        self, instance_id: UUID, method: Method[Command, Result], command: Command
    ) -> Result:
        """Answer the instance's read of the workspace of a workbench one of its lines is on, on
        that workbench."""
        self._owns_a_line_on(instance_id, command.workbench_id)
        try:
            endpoint = await self._endpoint(self._active_workbench(command.workbench_id))
            return await self._control.read_workspace(endpoint, method, command)
        except (ControlUnreachable, IncompatibleLifecycleEndpoint) as exc:
            raise WorkspaceReadFailed(
                f'Workbench "{self._workbench_name(command.workbench_id)}" answered no read: {exc}'
            ) from exc

    async def set_owned_workbench_commands(
        self, instance_id: UUID, command: WorkbenchCommandsSetCommand
    ) -> Workbench:
        """Set a list of repository commands of a workbench one of the instance's lines is on."""
        self._owns_a_line_on(instance_id, command.workbench_id)
        return await self.set_workbench_commands(command)

    def _owns_a_line_on(self, instance_id: UUID, workbench_id: UUID) -> None:
        if not any(
            (line.owner_id, line.workbench_id) == (instance_id, workbench_id)
            for line in self.registry.lines()
        ):
            raise PermissionDenied(f'Workbench "{workbench_id}" carries no line of this instance.')

    async def line_intake(self, instance_id: UUID, command: LineIntakeCommand) -> LineIntakeResult:
        """Run the intake of the instance's line whose intake is this routine on the line's
        workbench, where its secrets are, and hand the line each work item it finds."""
        line = next(
            (
                line
                for line in self.registry.lines()
                if (line.owner_id, line.intake) == (instance_id, command.routine)
            ),
            None,
        )
        factory = None if line is None else self.factories.file(line.factory)
        if line is None or not isinstance(factory, WorkbenchFactory) or factory.intake is None:
            raise NotAnIntake(f'Routine "{command.routine}" of this instance is no line\'s intake.')
        intake = IntakeRunCommand(
            factory=line.factory, call=factory.intake.call, signal=command.signal
        )
        try:
            endpoint = await self._endpoint(self._active_workbench(line.workbench_id))
            found = await self._control.run_intake(endpoint, intake)
        except (ControlUnreachable, IncompatibleLifecycleEndpoint) as exc:
            raise IntakeFailed(
                f'The intake of line "{line.factory}" did not run on workbench '
                f'"{self._workbench_name(line.workbench_id)}": {exc}'
            ) from exc
        return LineIntakeResult(
            runs=[await self.runs.hand_in(line, work_item) for work_item in found.work_items]
        )

    def _workbench_name(self, workbench_id: UUID) -> str:
        record = self.registry.instance(workbench_id)
        return str(workbench_id) if record is None else record.manifest_id

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

    async def signal_endpoint(
        self, routine: RoutineName, instance_id: UUID | None = None
    ) -> tuple[InstanceEndpoint, RoutineName] | InstanceUnreachable:
        """Reach where a delivery to the instance's signal path goes, or without one, to the
        public path of the instance that kept the webhook URL it was registered with: the
        instance's routine, or the intake of the line it was converted into."""
        target = self.registry.signal_alias() if instance_id is None else instance_id
        if target is None:
            return InstanceUnreachable.MISSING
        line_id = self.registry.signal_line(target)
        if line_id is not None:
            return await self.intake_endpoint(line_id)
        endpoint = await self.endpoint(target)
        return endpoint if isinstance(endpoint, InstanceUnreachable) else (endpoint, routine)

    async def intake_endpoint(
        self, line_id: UUID
    ) -> tuple[InstanceEndpoint, RoutineName] | InstanceUnreachable:
        """Reach the owner of the line, and the routine its deliveries go to there."""
        line = next((line for line in self.registry.lines() if line.line_id == line_id), None)
        if line is None or line.intake is None:
            return InstanceUnreachable.MISSING
        endpoint = await self.endpoint(line.owner_id)
        return endpoint if isinstance(endpoint, InstanceUnreachable) else (endpoint, line.intake)

    def signal_accepted(self, instance_id: UUID, signal: Signal) -> None:
        """Wake the parked waits of the instance's factory that the signal matches."""
        self.runs.signal_accepted(instance_id, signal)

    async def stats_summary(self, command: StatsGetCommand) -> StatsSummaryResult:
        """Ask every instance at once, and keep none of the answers (ADR 0063)."""
        return summed_usage(await self._answers(command))

    async def stats_runs(self, command: StatsRunsCommand) -> StatsRunsResult:
        """Join the runs the instance handed in, or the line holds, with their tokens in every
        instance, which are asked at once, and with what their attempts on a workbench spent.
        Keep none of the instances' answers (ADR 0063)."""
        if command.instance_id is not None:
            self._active_instance(command.instance_id)
        if command.line_id is not None:
            await self.get_line(LineGetCommand(line_id=command.line_id))
        runs = self.registry.runs(
            None,
            None,
            command.instance_id,
            line_id=command.line_id,
            since=command.since,
            until=command.until,
        )
        answers = await self._answers(StatsGetCommand(since=command.since, until=command.until))
        return run_usage(
            [(run, self.runs.rework(run), self.registry.attempts(run.run_id)) for run in runs],
            answers,
        )

    async def _answers(self, command: StatsGetCommand) -> dict[UUID, StatsGetResult | Uncounted]:
        records = [
            record
            for record in self.registry.managed_instances()
            if record.active and record.workbench is None
        ]
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
        *,
        workbench: bool = False,
    ) -> tuple[StorageItem, ...]:
        """What a created instance owns: its directory on the Docker host, its workspace
        volume, and one volume per declared login. A workbench also mounts the hub's factory
        folders read only, so each step runs its factory's tools as they are then.

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
            # First, as the inventory reads it back, ordered by destination.
            *(self._factories_mount() if workbench else ()),
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

    def _factories_mount(self) -> tuple[StorageItem]:
        (self.directory / _FACTORIES_DIR).mkdir(exist_ok=True)
        return (
            StorageItem(
                kind=StorageKind.BIND,
                source=str(self._docker_host_directory / _FACTORIES_DIR),
                destination=str(FACTORIES_MOUNT),
                writable=False,
            ),
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
