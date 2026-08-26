"""Put scope and payload checks on the one path every client call crosses."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar, cast

from pydantic import BaseModel, ValidationError

from kinby.contracts import (
    ErrorCode,
    ErrorEnvelope,
    Scope,
    ThreadCreateCommand,
    ThreadListCommand,
    ThreadSubscribeCommand,
)
from kinby.core.events import EventLog
from kinby.core.threads import ThreadStore

Handler = Callable[[BaseModel], Awaitable[BaseModel]]
SubscriptionHandler = Callable[[BaseModel], AsyncIterator[BaseModel]]
Command = TypeVar("Command", bound=BaseModel)


@dataclass(frozen=True)
class Route:
    scope: Scope
    command: type[BaseModel]
    handler: Handler


@dataclass(frozen=True)
class SubscriptionRoute:
    scope: Scope
    command: type[BaseModel]
    handler: SubscriptionHandler


class Dispatcher:
    def __init__(self) -> None:
        self._routes: dict[str, Route] = {}
        self._subscription_routes: dict[str, SubscriptionRoute] = {}

    def register(
        self,
        method: str,
        scope: Scope,
        command: type[Command],
        handler: Callable[[Command], Awaitable[BaseModel]],
    ) -> None:
        self._routes[method] = Route(scope, command, cast(Handler, handler))

    def register_subscription(
        self,
        method: str,
        scope: Scope,
        command: type[Command],
        handler: Callable[[Command], AsyncIterator[BaseModel]],
    ) -> None:
        self._subscription_routes[method] = SubscriptionRoute(
            scope,
            command,
            cast(SubscriptionHandler, handler),
        )

    async def dispatch(
        self,
        method: str,
        payload: Mapping[str, Any],
        scopes: Collection[Scope],
    ) -> BaseModel:
        route = self._routes.get(method)
        if route is None:
            return ErrorEnvelope(
                code=ErrorCode.NOT_FOUND,
                message=f'Method "{method}" was not found.',
                retryable=False,
            )
        if route.scope not in scopes:
            return ErrorEnvelope(
                code=ErrorCode.PERMISSION_DENIED,
                message=f'Missing required scope "{route.scope.value}".',
                retryable=False,
            )
        try:
            command = route.command.model_validate(payload)
        except ValidationError as exc:
            return ErrorEnvelope(
                code=ErrorCode.INVALID_ARGUMENT,
                message=f'Invalid payload for "{method}": {exc}',
                retryable=False,
            )
        try:
            return await route.handler(command)
        except Exception:
            return ErrorEnvelope(
                code=ErrorCode.INTERNAL,
                message="The method failed unexpectedly.",
                retryable=False,
            )

    async def subscribe(
        self,
        method: str,
        payload: Mapping[str, Any],
        scopes: Collection[Scope],
    ) -> AsyncGenerator[BaseModel, None]:
        route = self._subscription_routes.get(method)
        if route is None:
            yield ErrorEnvelope(
                code=ErrorCode.NOT_FOUND,
                message=f'Method "{method}" was not found.',
                retryable=False,
            )
            return
        if route.scope not in scopes:
            yield ErrorEnvelope(
                code=ErrorCode.PERMISSION_DENIED,
                message=f'Missing required scope "{route.scope.value}".',
                retryable=False,
            )
            return
        try:
            command = route.command.model_validate(payload)
        except ValidationError as exc:
            yield ErrorEnvelope(
                code=ErrorCode.INVALID_ARGUMENT,
                message=f'Invalid payload for "{method}": {exc}',
                retryable=False,
            )
            return
        try:
            async for event in route.handler(command):
                yield event
        except Exception:
            yield ErrorEnvelope(
                code=ErrorCode.INTERNAL,
                message="The subscription failed unexpectedly.",
                retryable=False,
            )


def build_dispatcher(state_dir: Path) -> Dispatcher:
    store = ThreadStore(state_dir)
    event_log = EventLog(state_dir)
    dispatcher = Dispatcher()

    async def create_thread(command: ThreadCreateCommand) -> BaseModel:
        return store.create(command.title)

    async def list_threads(command: ThreadListCommand) -> BaseModel:
        return store.list()

    async def subscribe_to_thread(
        command: ThreadSubscribeCommand,
    ) -> AsyncIterator[BaseModel]:
        async for event in event_log.subscribe(command.thread_id, command.after_sequence):
            yield event

    dispatcher.register(
        "thread.create",
        Scope.THREAD_OPERATE,
        ThreadCreateCommand,
        create_thread,
    )
    dispatcher.register(
        "thread.list",
        Scope.THREAD_READ,
        ThreadListCommand,
        list_threads,
    )
    dispatcher.register_subscription(
        "thread.subscribe",
        Scope.THREAD_READ,
        ThreadSubscribeCommand,
        subscribe_to_thread,
    )
    return dispatcher
