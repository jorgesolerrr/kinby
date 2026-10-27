"""Tie each wire method name to the command it takes and the result it returns."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from kinby.contracts.models import (
    AcceptedResult,
    BrowserOpenCommand,
    BrowserOpenResult,
    BrowserProfile,
    BrowserProfileDeleteCommand,
    BrowserProfileListCommand,
    BrowserProfileListResult,
    BrowserProfileRenameCommand,
    BrowserProfileSaveCommand,
    BrowserProfilesCommand,
    BrowserTakeoverFinishCommand,
    BrowserTakeoverInputCommand,
    BrowserTakeoverStartCommand,
    Channel,
    ChannelCreateCommand,
    ChannelDeleteCommand,
    ChannelKindsCommand,
    ChannelKindsResult,
    ChannelListCommand,
    ChannelListResult,
    ChannelMuteCommand,
    ChannelTestCommand,
    ChannelUpdateCommand,
    ConfigHistoryCommand,
    ConfigHistoryResult,
    ContractModel,
    DeclaredSetup,
    Event,
    FactoryCheckCommand,
    FactoryCheckResult,
    FactoryDescribeCommand,
    FactoryDescription,
    FactoryEditCommand,
    FactoryGetCommand,
    FactoryListCommand,
    FactoryListResult,
    FactoryResult,
    FactoryRun,
    FactoryRunApproveCommand,
    FactoryRunCancelCommand,
    FactoryRunDetail,
    FactoryRunGetCommand,
    FactoryRunIntakeCommand,
    FactoryRunListCommand,
    FactoryRunListResult,
    FactoryRunRetryCommand,
    FactoryRunSendBackCommand,
    FactoryRunSubscribeCommand,
    HubSecretDeleteCommand,
    HubSecretDeleteResult,
    HubSecretListCommand,
    HubSecretListResult,
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
    InstanceDrainCommand,
    InstanceDrainResult,
    InstanceListCommand,
    InstanceListResult,
    InstanceLoginStartCommand,
    InstanceLogsCommand,
    InstanceLogsResult,
    InstancePicksGetCommand,
    InstancePicksResult,
    InstancePicksSetCommand,
    InstanceProbeCommand,
    InstanceProbeResult,
    InstanceRecreateCommand,
    InstanceRelayReadCommand,
    InstanceRelayReadResult,
    InstanceRemoveCommand,
    InstanceRestoreCommand,
    InstanceSecretsSetCommand,
    InstanceStartCommand,
    InstanceStatusCommand,
    InstanceStatusResult,
    InstanceStopCommand,
    InstanceUpdateCommand,
    LibraryGitAddCommand,
    LibraryGitAddResult,
    LibraryGitPreviewCommand,
    LibraryGitPreviewResult,
    LibraryListCommand,
    LibraryListResult,
    LibraryMcpServerDeleteCommand,
    LibraryMcpServerReadCommand,
    LibraryMcpServerWriteCommand,
    LibrarySkillCheckCommand,
    LibrarySkillCheckResult,
    LibrarySkillDeleteCommand,
    LibrarySkillMoveCommand,
    LibrarySkillReadCommand,
    LibrarySkillWriteCommand,
    LifecycleOperationResult,
    ManifestGetCommand,
    ManifestResult,
    ManifestSetCommand,
    McpServerResult,
    MemoryAddCommand,
    MemoryCorrectCommand,
    MemoryForgetCommand,
    MemoryForgetResult,
    MemoryListCommand,
    MemoryListResult,
    MemoryOpenCommand,
    MemoryOpenResult,
    MemoryWriteResult,
    NotificationListCommand,
    NotificationListResult,
    NotificationRaiseCommand,
    NotificationRaiseResult,
    NotificationReply,
    NotificationReplyCommand,
    OperationGetCommand,
    OperationGetResult,
    PermissionsGetCommand,
    PermissionsResult,
    PermissionsSetCommand,
    ProfileGetCommand,
    ProfileResult,
    ProfileSetCommand,
    PromptGetCommand,
    PromptResult,
    PromptSetCommand,
    RoutineDeleteCommand,
    RoutineDeleteResult,
    RoutineFile,
    RoutineGrantListCommand,
    RoutineGrantListResult,
    RoutineGrantRevokeCommand,
    RoutineListCommand,
    RoutineListResult,
    RoutineReadCommand,
    RoutineRenameCommand,
    RoutineRunCommand,
    RoutineSetEnabledCommand,
    RoutineWriteCommand,
    RunStop,
    Scope,
    SkillCustomizeCommand,
    SkillDeleteCommand,
    SkillListCommand,
    SkillListResult,
    SkillReadCommand,
    SkillResult,
    SkillWriteCommand,
    StatsGetCommand,
    StatsGetResult,
    StatsRunsCommand,
    StatsRunsResult,
    StatsSummaryResult,
    StepResult,
    StepRunCommand,
    TakeoverFrame,
    ThreadApprovalRespondCommand,
    ThreadArchiveCommand,
    ThreadAskCommand,
    ThreadCreateCommand,
    ThreadCreateResult,
    ThreadListCommand,
    ThreadListResult,
    ThreadModeSetCommand,
    ThreadRenameCommand,
    ThreadSubscribeCommand,
    ThreadSummary,
    ThreadTakeoverFinishCommand,
    ThreadTakeoverInputCommand,
    ThreadTakeoverInputResult,
    ThreadTakeoverStartCommand,
    ThreadTurnDiffCommand,
    ThreadTurnDiffResult,
    ThreadTurnInterruptCommand,
    ThreadTurnListCommand,
    ThreadTurnListResult,
    ThreadTurnRateCommand,
    ThreadTurnRevertCommand,
    ThreadTurnRevertPreviewCommand,
    ThreadTurnRevertPreviewResult,
    ThreadTurnStartCommand,
    ThreadTurnTargetListCommand,
    ThreadTurnTargetListResult,
    ThreadUnarchiveCommand,
    ToolListCommand,
    ToolListResult,
    ToolReadOnlySetCommand,
    UsageGetCommand,
    UsageGetResult,
    View,
    ViewDeleteCommand,
    ViewGetCommand,
    ViewListCommand,
    ViewListResult,
    ViewSubscribeCommand,
)


@dataclass(frozen=True)
class Method[Command: ContractModel, Result: ContractModel]:
    """A request/response method: one command in, one result out."""

    name: str
    scope: Scope
    command: type[Command]
    result: type[Result]
    #: A second scope that reaches this method, for a token that holds no other.
    alternative_scope: Scope | None = None


@dataclass(frozen=True)
class Subscription[Command: ContractModel, Item: ContractModel]:
    """A streaming method: one command in, a stream of items out."""

    name: str
    scope: Scope
    command: type[Command]
    item: type[Item]


THREAD_CREATE = Method(
    "thread.create", Scope.THREAD_OPERATE, ThreadCreateCommand, ThreadCreateResult
)
THREAD_LIST = Method("thread.list", Scope.THREAD_READ, ThreadListCommand, ThreadListResult)
THREAD_RENAME = Method("thread.rename", Scope.THREAD_OPERATE, ThreadRenameCommand, ThreadSummary)
THREAD_ARCHIVE = Method("thread.archive", Scope.THREAD_OPERATE, ThreadArchiveCommand, ThreadSummary)
THREAD_UNARCHIVE = Method(
    "thread.unarchive", Scope.THREAD_OPERATE, ThreadUnarchiveCommand, ThreadSummary
)
THREAD_MODE_SET = Method(
    "thread.mode.set", Scope.THREAD_ADMIN, ThreadModeSetCommand, AcceptedResult
)
THREAD_TURN_START = Method(
    "thread.turn.start", Scope.THREAD_OPERATE, ThreadTurnStartCommand, AcceptedResult
)
THREAD_TURN_DIFF = Method(
    "thread.turn.diff", Scope.THREAD_READ, ThreadTurnDiffCommand, ThreadTurnDiffResult
)
THREAD_TURN_REVERT = Method(
    "thread.turn.revert", Scope.THREAD_OPERATE, ThreadTurnRevertCommand, AcceptedResult
)
THREAD_TURN_REVERT_PREVIEW = Method(
    "thread.turn.revert.preview",
    Scope.THREAD_READ,
    ThreadTurnRevertPreviewCommand,
    ThreadTurnRevertPreviewResult,
)
THREAD_TURN_LIST = Method(
    "thread.turn.list", Scope.THREAD_READ, ThreadTurnListCommand, ThreadTurnListResult
)
THREAD_TURN_TARGET_LIST = Method(
    "thread.turn.target.list",
    Scope.THREAD_READ,
    ThreadTurnTargetListCommand,
    ThreadTurnTargetListResult,
)
THREAD_TURN_INTERRUPT = Method(
    "thread.turn.interrupt",
    Scope.THREAD_OPERATE,
    ThreadTurnInterruptCommand,
    AcceptedResult,
)
THREAD_TURN_RATE = Method(
    "thread.turn.rate", Scope.THREAD_RATE, ThreadTurnRateCommand, AcceptedResult
)
THREAD_APPROVAL_RESPOND = Method(
    "thread.approval.respond",
    Scope.THREAD_OPERATE,
    ThreadApprovalRespondCommand,
    AcceptedResult,
)
THREAD_TAKEOVER_START = Subscription(
    "thread.takeover.start", Scope.THREAD_OPERATE, ThreadTakeoverStartCommand, TakeoverFrame
)
THREAD_TAKEOVER_INPUT = Method(
    "thread.takeover.input",
    Scope.THREAD_OPERATE,
    ThreadTakeoverInputCommand,
    ThreadTakeoverInputResult,
)
THREAD_TAKEOVER_FINISH = Method(
    "thread.takeover.finish", Scope.THREAD_OPERATE, ThreadTakeoverFinishCommand, AcceptedResult
)
BROWSER_TAKEOVER_START = Subscription(
    "browser.takeover.start", Scope.INSTANCE_ADMIN, BrowserTakeoverStartCommand, TakeoverFrame
)
BROWSER_TAKEOVER_INPUT = Method(
    "browser.takeover.input",
    Scope.INSTANCE_ADMIN,
    BrowserTakeoverInputCommand,
    ThreadTakeoverInputResult,
)
BROWSER_TAKEOVER_FINISH = Method(
    "browser.takeover.finish", Scope.INSTANCE_ADMIN, BrowserTakeoverFinishCommand, BrowserProfile
)
USAGE_GET = Method("usage.get", Scope.INSTANCE_READ, UsageGetCommand, UsageGetResult)
STATS_GET = Method("stats.get", Scope.INSTANCE_READ, StatsGetCommand, StatsGetResult)
THREAD_SUBSCRIBE = Subscription(
    "thread.subscribe", Scope.THREAD_READ, ThreadSubscribeCommand, Event
)

INSTANCE_PROBE = Method(
    "instance.probe", Scope.INSTANCE_LIFECYCLE, InstanceProbeCommand, InstanceProbeResult
)
INSTANCE_DRAIN = Method(
    "instance.drain", Scope.INSTANCE_LIFECYCLE, InstanceDrainCommand, InstanceDrainResult
)
#: The hub runs one step of a factory run in the instance, as it would run a turn.
STEP_RUN = Method("step.run", Scope.INSTANCE_LIFECYCLE, StepRunCommand, StepResult)
#: The hub tells the instance that handed a factory run in that the run stopped.
RUN_STOPPED = Method("run.stopped", Scope.INSTANCE_LIFECYCLE, RunStop, AcceptedResult)
#: The hub starts a turn on a new thread for the home instance, which is that turn's origin.
THREAD_ASK = Method("thread.ask", Scope.INSTANCE_LIFECYCLE, ThreadAskCommand, AcceptedResult)

ROUTINE_LIST = Method("routine.list", Scope.INSTANCE_READ, RoutineListCommand, RoutineListResult)
ROUTINE_RUN = Method("routine.run", Scope.INSTANCE_ADMIN, RoutineRunCommand, AcceptedResult)
ROUTINE_READ = Method("routine.read", Scope.INSTANCE_READ, RoutineReadCommand, RoutineFile)
ROUTINE_WRITE = Method("routine.write", Scope.INSTANCE_ADMIN, RoutineWriteCommand, RoutineFile)
ROUTINE_SET_ENABLED = Method(
    "routine.set_enabled", Scope.INSTANCE_ADMIN, RoutineSetEnabledCommand, RoutineFile
)
ROUTINE_DELETE = Method(
    "routine.delete", Scope.INSTANCE_ADMIN, RoutineDeleteCommand, RoutineDeleteResult
)
ROUTINE_RENAME = Method("routine.rename", Scope.INSTANCE_ADMIN, RoutineRenameCommand, RoutineFile)
ROUTINE_GRANT_LIST = Method(
    "routine.grant.list", Scope.INSTANCE_READ, RoutineGrantListCommand, RoutineGrantListResult
)
#: Answers with the routine's grants that remain.
ROUTINE_GRANT_REVOKE = Method(
    "routine.grant.revoke", Scope.INSTANCE_ADMIN, RoutineGrantRevokeCommand, RoutineGrantListResult
)
PROMPT_GET = Method("prompt.get", Scope.INSTANCE_READ, PromptGetCommand, PromptResult)
PROMPT_SET = Method("prompt.set", Scope.INSTANCE_ADMIN, PromptSetCommand, PromptResult)
MANIFEST_GET = Method("manifest.get", Scope.INSTANCE_READ, ManifestGetCommand, ManifestResult)
MANIFEST_SET = Method("manifest.set", Scope.INSTANCE_ADMIN, ManifestSetCommand, ManifestResult)
PERMISSIONS_GET = Method(
    "permissions.get", Scope.INSTANCE_READ, PermissionsGetCommand, PermissionsResult
)
PERMISSIONS_SET = Method(
    "permissions.set", Scope.INSTANCE_ADMIN, PermissionsSetCommand, PermissionsResult
)
MEMORY_LIST = Method("memory.list", Scope.INSTANCE_READ, MemoryListCommand, MemoryListResult)
MEMORY_OPEN = Method("memory.open", Scope.INSTANCE_READ, MemoryOpenCommand, MemoryOpenResult)
MEMORY_ADD = Method("memory.add", Scope.INSTANCE_ADMIN, MemoryAddCommand, MemoryWriteResult)
MEMORY_CORRECT = Method(
    "memory.correct", Scope.INSTANCE_ADMIN, MemoryCorrectCommand, MemoryWriteResult
)
MEMORY_FORGET = Method(
    "memory.forget", Scope.INSTANCE_ADMIN, MemoryForgetCommand, MemoryForgetResult
)
PROFILE_GET = Method("profile.get", Scope.INSTANCE_READ, ProfileGetCommand, ProfileResult)
PROFILE_SET = Method("profile.set", Scope.INSTANCE_ADMIN, ProfileSetCommand, ProfileResult)
CONFIG_HISTORY = Method(
    "config.history", Scope.INSTANCE_READ, ConfigHistoryCommand, ConfigHistoryResult
)
SKILL_LIST = Method("skill.list", Scope.INSTANCE_READ, SkillListCommand, SkillListResult)
SKILL_READ = Method("skill.read", Scope.INSTANCE_READ, SkillReadCommand, SkillResult)
SKILL_WRITE = Method("skill.write", Scope.INSTANCE_ADMIN, SkillWriteCommand, SkillResult)
SKILL_CUSTOMIZE = Method(
    "skill.customize", Scope.INSTANCE_ADMIN, SkillCustomizeCommand, SkillResult
)
SKILL_DELETE = Method("skill.delete", Scope.INSTANCE_ADMIN, SkillDeleteCommand, SkillListResult)
TOOL_LIST = Method("tool.list", Scope.INSTANCE_READ, ToolListCommand, ToolListResult)
#: Answers with the instance's tools.
TOOL_READ_ONLY_SET = Method(
    "tool.read_only.set", Scope.INSTANCE_ADMIN, ToolReadOnlySetCommand, ToolListResult
)
VIEW_LIST = Method("view.list", Scope.INSTANCE_READ, ViewListCommand, ViewListResult)
VIEW_GET = Method("view.get", Scope.INSTANCE_READ, ViewGetCommand, View)
VIEW_SUBSCRIBE = Subscription("view.subscribe", Scope.INSTANCE_READ, ViewSubscribeCommand, View)
VIEW_DELETE = Method("view.delete", Scope.INSTANCE_ADMIN, ViewDeleteCommand, ViewListResult)

INSTANCE_CREATE = Method(
    "instance.create", Scope.HUB_ADMIN, InstanceCreateCommand, LifecycleOperationResult
)
INSTANCE_START = Method(
    "instance.start", Scope.HUB_ADMIN, InstanceStartCommand, LifecycleOperationResult
)
INSTANCE_STOP = Method(
    "instance.stop", Scope.HUB_ADMIN, InstanceStopCommand, LifecycleOperationResult
)
INSTANCE_RECREATE = Method(
    "instance.recreate", Scope.HUB_ADMIN, InstanceRecreateCommand, LifecycleOperationResult
)
INSTANCE_REMOVE = Method(
    "instance.remove", Scope.HUB_ADMIN, InstanceRemoveCommand, LifecycleOperationResult
)
INSTANCE_RESTORE = Method(
    "instance.restore", Scope.HUB_ADMIN, InstanceRestoreCommand, LifecycleOperationResult
)
INSTANCE_DELETE_PREVIEW = Method(
    "instance.delete.preview",
    Scope.HUB_READ,
    InstanceDeletePreviewCommand,
    InstanceDeletePreviewResult,
)
INSTANCE_DELETE = Method(
    "instance.delete", Scope.HUB_ADMIN, InstanceDeleteCommand, LifecycleOperationResult
)
INSTANCE_UPDATE = Method(
    "instance.update", Scope.HUB_UPDATE, InstanceUpdateCommand, LifecycleOperationResult
)
INSTANCE_SECRETS_SET = Method(
    "instance.secrets.set", Scope.HUB_ADMIN, InstanceSecretsSetCommand, LifecycleOperationResult
)
INSTANCE_ADOPT_PREVIEW = Method(
    "instance.adopt.preview",
    Scope.HUB_READ,
    InstanceAdoptPreviewCommand,
    InstanceAdoptPreviewResult,
)
INSTANCE_ADOPT = Method(
    "instance.adopt", Scope.HUB_ADMIN, InstanceAdoptCommand, LifecycleOperationResult
)
INSTANCE_LIST = Method("instance.list", Scope.HUB_READ, InstanceListCommand, InstanceListResult)
INSTANCE_STATUS = Method(
    "instance.status", Scope.HUB_READ, InstanceStatusCommand, InstanceStatusResult
)
INSTANCE_LOGS = Method("instance.logs", Scope.HUB_READ, InstanceLogsCommand, InstanceLogsResult)
INSTANCE_RELAY_READ = Method(
    "instance.relay.read", Scope.HUB_HOME, InstanceRelayReadCommand, InstanceRelayReadResult
)
HUB_WRITE = Method("hub.write", Scope.HUB_HOME, HubWriteCommand, HubWriteResult)
INSTANCE_LOGIN_START = Method(
    "instance.login.start", Scope.HUB_ADMIN, InstanceLoginStartCommand, LifecycleOperationResult
)
OPERATION_GET = Method(
    "operation.get",
    Scope.HUB_READ,
    OperationGetCommand,
    OperationGetResult,
    # The update token follows the update it started and reads nothing else (ADR 0057).
    alternative_scope=Scope.HUB_UPDATE,
)
IMAGE_PREPARE = Method("image.prepare", Scope.HUB_ADMIN, ImagePrepareCommand, ImagePrepareResult)
IMAGE_DESCRIBE = Method("image.describe", Scope.HUB_READ, ImageDescribeCommand, DeclaredSetup)
FACTORY_LIST = Method("factory.list", Scope.HUB_READ, FactoryListCommand, FactoryListResult)
FACTORY_GET = Method("factory.get", Scope.HUB_READ, FactoryGetCommand, FactoryResult)
FACTORY_EDIT = Method("factory.edit", Scope.HUB_ADMIN, FactoryEditCommand, FactoryResult)
FACTORY_CHECK = Method("factory.check", Scope.HUB_READ, FactoryCheckCommand, FactoryCheckResult)
FACTORY_DESCRIBE = Method(
    "factory.describe", Scope.HUB_READ, FactoryDescribeCommand, FactoryDescription
)
FACTORY_RUN_INTAKE = Method(
    "factory.run.intake", Scope.FACTORY_INTAKE, FactoryRunIntakeCommand, FactoryRun
)
FACTORY_RUN_LIST = Method(
    "factory.run.list", Scope.HUB_READ, FactoryRunListCommand, FactoryRunListResult
)
FACTORY_RUN_GET = Method("factory.run.get", Scope.HUB_READ, FactoryRunGetCommand, FactoryRunDetail)
FACTORY_RUN_RETRY = Method("factory.run.retry", Scope.HUB_ADMIN, FactoryRunRetryCommand, FactoryRun)
FACTORY_RUN_SEND_BACK = Method(
    "factory.run.send_back", Scope.HUB_ADMIN, FactoryRunSendBackCommand, FactoryRun
)
FACTORY_RUN_APPROVE = Method(
    "factory.run.approve", Scope.HUB_ADMIN, FactoryRunApproveCommand, FactoryRun
)
FACTORY_RUN_CANCEL = Method(
    "factory.run.cancel", Scope.HUB_ADMIN, FactoryRunCancelCommand, FactoryRun
)
FACTORY_RUN_SUBSCRIBE = Subscription(
    "factory.run.subscribe", Scope.HUB_READ, FactoryRunSubscribeCommand, FactoryRun
)
LIBRARY_LIST = Method("library.list", Scope.HUB_READ, LibraryListCommand, LibraryListResult)
LIBRARY_SKILL_READ = Method(
    "library.skill.read", Scope.HUB_READ, LibrarySkillReadCommand, SkillResult
)
LIBRARY_SKILL_WRITE = Method(
    "library.skill.write", Scope.HUB_ADMIN, LibrarySkillWriteCommand, SkillResult
)
LIBRARY_SKILL_DELETE = Method(
    "library.skill.delete", Scope.HUB_ADMIN, LibrarySkillDeleteCommand, LibraryListResult
)
LIBRARY_SKILL_CHECK = Method(
    "library.skill.check", Scope.HUB_READ, LibrarySkillCheckCommand, LibrarySkillCheckResult
)
LIBRARY_SKILL_MOVE = Method(
    "library.skill.move", Scope.HUB_ADMIN, LibrarySkillMoveCommand, LibraryListResult
)
#: The hub fetches whatever the address names, a path on the hub included. Only the user and the
#: home instance hold hub:read.
LIBRARY_GIT_PREVIEW = Method(
    "library.git.preview", Scope.HUB_READ, LibraryGitPreviewCommand, LibraryGitPreviewResult
)
LIBRARY_GIT_ADD = Method(
    "library.git.add", Scope.HUB_ADMIN, LibraryGitAddCommand, LibraryGitAddResult
)
LIBRARY_MCP_SERVER_READ = Method(
    "library.mcp_server.read", Scope.HUB_READ, LibraryMcpServerReadCommand, McpServerResult
)
LIBRARY_MCP_SERVER_WRITE = Method(
    "library.mcp_server.write", Scope.HUB_ADMIN, LibraryMcpServerWriteCommand, McpServerResult
)
LIBRARY_MCP_SERVER_DELETE = Method(
    "library.mcp_server.delete", Scope.HUB_ADMIN, LibraryMcpServerDeleteCommand, LibraryListResult
)
INSTANCE_PICKS_GET = Method(
    "instance.picks.get", Scope.HUB_READ, InstancePicksGetCommand, InstancePicksResult
)
INSTANCE_PICKS_SET = Method(
    "instance.picks.set", Scope.HUB_ADMIN, InstancePicksSetCommand, InstancePicksResult
)
HUB_SECRET_LIST = Method(
    "hub.secret.list", Scope.HUB_READ, HubSecretListCommand, HubSecretListResult
)
HUB_SECRET_SET = Method("hub.secret.set", Scope.HUB_ADMIN, HubSecretSetCommand, HubSecretSetResult)
HUB_SECRET_DELETE = Method(
    "hub.secret.delete", Scope.HUB_ADMIN, HubSecretDeleteCommand, HubSecretDeleteResult
)
#: The same time range and bucket size as stats.get, asked of every running instance.
STATS_SUMMARY = Method("stats.summary", Scope.HUB_READ, StatsGetCommand, StatsSummaryResult)
STATS_RUNS = Method("stats.runs", Scope.HUB_READ, StatsRunsCommand, StatsRunsResult)
CHANNEL_KINDS = Method("channel.kinds", Scope.HUB_ADMIN, ChannelKindsCommand, ChannelKindsResult)
CHANNEL_LIST = Method("channel.list", Scope.HUB_READ, ChannelListCommand, ChannelListResult)
CHANNEL_CREATE = Method("channel.create", Scope.HUB_ADMIN, ChannelCreateCommand, Channel)
CHANNEL_UPDATE = Method("channel.update", Scope.HUB_ADMIN, ChannelUpdateCommand, Channel)
CHANNEL_DELETE = Method("channel.delete", Scope.HUB_ADMIN, ChannelDeleteCommand, ChannelListResult)
CHANNEL_TEST = Method("channel.test", Scope.HUB_ADMIN, ChannelTestCommand, Channel)
CHANNEL_MUTE = Method("channel.mute", Scope.HUB_ADMIN, ChannelMuteCommand, Channel)
CHANNEL_UNMUTE = Method("channel.unmute", Scope.HUB_ADMIN, ChannelMuteCommand, Channel)
NOTIFICATION_RAISE = Method(
    "notification.raise",
    Scope.NOTIFICATION_RAISE,
    NotificationRaiseCommand,
    NotificationRaiseResult,
)
BROWSER_OPEN = Method("browser.open", Scope.BROWSER_OPEN, BrowserOpenCommand, BrowserOpenResult)
BROWSER_PROFILES = Method(
    "browser.profiles", Scope.BROWSER_OPEN, BrowserProfilesCommand, BrowserProfileListResult
)
BROWSER_PROFILE_SAVE = Method(
    "browser.profile.save", Scope.BROWSER_OPEN, BrowserProfileSaveCommand, BrowserProfile
)
BROWSER_PROFILE_LIST = Method(
    "browser.profile.list", Scope.HUB_READ, BrowserProfileListCommand, BrowserProfileListResult
)
#: Answers with the instance's profiles.
BROWSER_PROFILE_RENAME = Method(
    "browser.profile.rename",
    Scope.HUB_ADMIN,
    BrowserProfileRenameCommand,
    BrowserProfileListResult,
)
#: Answers with the instance's profiles.
BROWSER_PROFILE_DELETE = Method(
    "browser.profile.delete",
    Scope.HUB_ADMIN,
    BrowserProfileDeleteCommand,
    BrowserProfileListResult,
)
NOTIFICATION_LIST = Method(
    "notification.list", Scope.HUB_ADMIN, NotificationListCommand, NotificationListResult
)
NOTIFICATION_REPLY = Method(
    "notification.reply", Scope.HUB_ADMIN, NotificationReplyCommand, NotificationReply
)

METHODS = (
    THREAD_CREATE,
    THREAD_LIST,
    THREAD_RENAME,
    THREAD_ARCHIVE,
    THREAD_UNARCHIVE,
    THREAD_MODE_SET,
    THREAD_TURN_START,
    THREAD_TURN_DIFF,
    THREAD_TURN_REVERT,
    THREAD_TURN_REVERT_PREVIEW,
    THREAD_TURN_LIST,
    THREAD_TURN_TARGET_LIST,
    THREAD_TURN_INTERRUPT,
    THREAD_TURN_RATE,
    THREAD_APPROVAL_RESPOND,
    THREAD_TAKEOVER_INPUT,
    THREAD_TAKEOVER_FINISH,
    BROWSER_TAKEOVER_INPUT,
    BROWSER_TAKEOVER_FINISH,
    USAGE_GET,
    STATS_GET,
    INSTANCE_PROBE,
    INSTANCE_DRAIN,
    STEP_RUN,
    RUN_STOPPED,
    THREAD_ASK,
    ROUTINE_LIST,
    ROUTINE_RUN,
    ROUTINE_READ,
    ROUTINE_WRITE,
    ROUTINE_SET_ENABLED,
    ROUTINE_DELETE,
    ROUTINE_RENAME,
    ROUTINE_GRANT_LIST,
    ROUTINE_GRANT_REVOKE,
    PROMPT_GET,
    PROMPT_SET,
    MANIFEST_GET,
    MANIFEST_SET,
    PERMISSIONS_GET,
    PERMISSIONS_SET,
    CONFIG_HISTORY,
    MEMORY_LIST,
    MEMORY_OPEN,
    MEMORY_ADD,
    MEMORY_CORRECT,
    MEMORY_FORGET,
    PROFILE_GET,
    PROFILE_SET,
    SKILL_LIST,
    SKILL_READ,
    SKILL_WRITE,
    SKILL_CUSTOMIZE,
    SKILL_DELETE,
    TOOL_LIST,
    TOOL_READ_ONLY_SET,
    VIEW_LIST,
    VIEW_GET,
    VIEW_DELETE,
    INSTANCE_CREATE,
    INSTANCE_START,
    INSTANCE_STOP,
    INSTANCE_RECREATE,
    INSTANCE_REMOVE,
    INSTANCE_RESTORE,
    INSTANCE_DELETE_PREVIEW,
    INSTANCE_DELETE,
    INSTANCE_UPDATE,
    INSTANCE_SECRETS_SET,
    INSTANCE_ADOPT_PREVIEW,
    INSTANCE_ADOPT,
    INSTANCE_LIST,
    INSTANCE_STATUS,
    INSTANCE_LOGS,
    INSTANCE_RELAY_READ,
    HUB_WRITE,
    INSTANCE_LOGIN_START,
    OPERATION_GET,
    IMAGE_PREPARE,
    IMAGE_DESCRIBE,
    FACTORY_LIST,
    FACTORY_GET,
    FACTORY_CHECK,
    FACTORY_EDIT,
    FACTORY_DESCRIBE,
    FACTORY_RUN_INTAKE,
    FACTORY_RUN_LIST,
    FACTORY_RUN_GET,
    FACTORY_RUN_RETRY,
    FACTORY_RUN_SEND_BACK,
    FACTORY_RUN_APPROVE,
    FACTORY_RUN_CANCEL,
    LIBRARY_LIST,
    LIBRARY_SKILL_READ,
    LIBRARY_SKILL_WRITE,
    LIBRARY_SKILL_DELETE,
    LIBRARY_SKILL_CHECK,
    LIBRARY_SKILL_MOVE,
    LIBRARY_GIT_PREVIEW,
    LIBRARY_GIT_ADD,
    LIBRARY_MCP_SERVER_READ,
    LIBRARY_MCP_SERVER_WRITE,
    LIBRARY_MCP_SERVER_DELETE,
    INSTANCE_PICKS_GET,
    INSTANCE_PICKS_SET,
    HUB_SECRET_LIST,
    HUB_SECRET_SET,
    HUB_SECRET_DELETE,
    STATS_SUMMARY,
    STATS_RUNS,
    CHANNEL_KINDS,
    CHANNEL_LIST,
    CHANNEL_CREATE,
    CHANNEL_UPDATE,
    CHANNEL_DELETE,
    CHANNEL_TEST,
    CHANNEL_MUTE,
    CHANNEL_UNMUTE,
    NOTIFICATION_RAISE,
    BROWSER_OPEN,
    BROWSER_PROFILES,
    BROWSER_PROFILE_SAVE,
    BROWSER_PROFILE_LIST,
    BROWSER_PROFILE_RENAME,
    BROWSER_PROFILE_DELETE,
    NOTIFICATION_LIST,
    NOTIFICATION_REPLY,
)
SUBSCRIPTIONS = (
    THREAD_SUBSCRIBE,
    FACTORY_RUN_SUBSCRIBE,
    VIEW_SUBSCRIBE,
    THREAD_TAKEOVER_START,
    BROWSER_TAKEOVER_START,
)

#: The methods of an instance the hub relays to the home instance: reads only.
RELAYED_READS = frozenset(
    method.name
    for method in (
        MEMORY_LIST,
        MEMORY_OPEN,
        THREAD_LIST,
        THREAD_TURN_LIST,
        ROUTINE_LIST,
        ROUTINE_READ,
        USAGE_GET,
        STATS_GET,
        PROMPT_GET,
        MANIFEST_GET,
        PERMISSIONS_GET,
        CONFIG_HISTORY,
    )
)

#: What a result or an item arrives as, by wire name, so a client off the socket parses it once.
RESULT_MODELS: Mapping[str, type[ContractModel]] = {
    method.name: method.result for method in METHODS
} | {subscription.name: subscription.item for subscription in SUBSCRIPTIONS}
