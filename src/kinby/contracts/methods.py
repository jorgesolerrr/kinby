"""Tie each wire method name to the command it takes and the result it returns."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from kinby.contracts.models import (
    AcceptedResult,
    ContractModel,
    Event,
    InstanceCreateCommand,
    InstanceListCommand,
    InstanceListResult,
    InstanceLogsCommand,
    InstanceLogsResult,
    InstanceProbeCommand,
    InstanceProbeResult,
    InstanceStartCommand,
    InstanceStatusCommand,
    InstanceStatusResult,
    LifecycleOperationResult,
    OperationGetCommand,
    OperationGetResult,
    RoutineListCommand,
    RoutineListResult,
    RoutineRunCommand,
    Scope,
    StatsGetCommand,
    StatsGetResult,
    ThreadApprovalRespondCommand,
    ThreadCreateCommand,
    ThreadCreateResult,
    ThreadListCommand,
    ThreadListResult,
    ThreadModeSetCommand,
    ThreadSubscribeCommand,
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
    UsageGetCommand,
    UsageGetResult,
)


@dataclass(frozen=True)
class Method[Command: ContractModel, Result: ContractModel]:
    """A request/response method: one command in, one result out."""

    name: str
    scope: Scope
    command: type[Command]
    result: type[Result]


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
USAGE_GET = Method("usage.get", Scope.INSTANCE_READ, UsageGetCommand, UsageGetResult)
STATS_GET = Method("stats.get", Scope.INSTANCE_READ, StatsGetCommand, StatsGetResult)
THREAD_SUBSCRIBE = Subscription(
    "thread.subscribe", Scope.THREAD_READ, ThreadSubscribeCommand, Event
)

INSTANCE_PROBE = Method(
    "instance.probe", Scope.INSTANCE_LIFECYCLE, InstanceProbeCommand, InstanceProbeResult
)

ROUTINE_LIST = Method("routine.list", Scope.INSTANCE_READ, RoutineListCommand, RoutineListResult)
ROUTINE_RUN = Method("routine.run", Scope.INSTANCE_ADMIN, RoutineRunCommand, AcceptedResult)

INSTANCE_CREATE = Method(
    "instance.create", Scope.HUB_ADMIN, InstanceCreateCommand, LifecycleOperationResult
)
INSTANCE_START = Method(
    "instance.start", Scope.HUB_ADMIN, InstanceStartCommand, LifecycleOperationResult
)
INSTANCE_LIST = Method("instance.list", Scope.HUB_READ, InstanceListCommand, InstanceListResult)
INSTANCE_STATUS = Method(
    "instance.status", Scope.HUB_READ, InstanceStatusCommand, InstanceStatusResult
)
INSTANCE_LOGS = Method("instance.logs", Scope.HUB_READ, InstanceLogsCommand, InstanceLogsResult)
OPERATION_GET = Method("operation.get", Scope.HUB_READ, OperationGetCommand, OperationGetResult)

_METHODS = (
    THREAD_CREATE,
    THREAD_LIST,
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
    USAGE_GET,
    STATS_GET,
    INSTANCE_PROBE,
    ROUTINE_LIST,
    ROUTINE_RUN,
    INSTANCE_CREATE,
    INSTANCE_START,
    INSTANCE_LIST,
    INSTANCE_STATUS,
    INSTANCE_LOGS,
    OPERATION_GET,
)
_SUBSCRIPTIONS = (THREAD_SUBSCRIBE,)

#: What a result or an item arrives as, by wire name, so a client off the socket parses it once.
RESULT_MODELS: Mapping[str, type[ContractModel]] = {
    method.name: method.result for method in _METHODS
} | {subscription.name: subscription.item for subscription in _SUBSCRIPTIONS}
