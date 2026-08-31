"""Tie each wire method name to the command it takes and the result it returns."""

from __future__ import annotations

from dataclasses import dataclass

from kinby.contracts.models import (
    AcceptedResult,
    ContractModel,
    Event,
    Scope,
    ThreadApprovalRespondCommand,
    ThreadCreateCommand,
    ThreadCreateResult,
    ThreadListCommand,
    ThreadListResult,
    ThreadModeSetCommand,
    ThreadSubscribeCommand,
    ThreadTurnInterruptCommand,
    ThreadTurnStartCommand,
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
THREAD_TURN_INTERRUPT = Method(
    "thread.turn.interrupt",
    Scope.THREAD_OPERATE,
    ThreadTurnInterruptCommand,
    AcceptedResult,
)
THREAD_APPROVAL_RESPOND = Method(
    "thread.approval.respond",
    Scope.THREAD_OPERATE,
    ThreadApprovalRespondCommand,
    AcceptedResult,
)
USAGE_GET = Method("usage.get", Scope.INSTANCE_READ, UsageGetCommand, UsageGetResult)
THREAD_SUBSCRIBE = Subscription(
    "thread.subscribe", Scope.THREAD_READ, ThreadSubscribeCommand, Event
)
