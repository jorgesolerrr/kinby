"""Tie each wire method name to the command it takes and the result it returns."""

from __future__ import annotations

from dataclasses import dataclass

from kinby.contracts.models import (
    AcceptedResult,
    ContractModel,
    Event,
    Scope,
    ThreadCreateCommand,
    ThreadCreateResult,
    ThreadListCommand,
    ThreadListResult,
    ThreadSubscribeCommand,
    ThreadTurnStartCommand,
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
THREAD_TURN_START = Method(
    "thread.turn.start", Scope.THREAD_OPERATE, ThreadTurnStartCommand, AcceptedResult
)
THREAD_SUBSCRIBE = Subscription(
    "thread.subscribe", Scope.THREAD_READ, ThreadSubscribeCommand, Event
)
