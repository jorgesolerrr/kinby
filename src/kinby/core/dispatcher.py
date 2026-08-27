"""Put scope and payload checks on the one path every client call crosses."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable, Collection, Mapping
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar, cast

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
SubscriptionHandler = Callable[[BaseModel], AsyncGenerator[BaseModel, None]]
Command = TypeVar("Command", bound=BaseModel)
RouteHandler = TypeVar("RouteHandler")


@dataclass(frozen=True)
class Route(Generic[RouteHandler]):
    scope: Scope
    command: type[BaseModel]
    handler: RouteHandler


class Dispatcher:
    def __init__(self) -> None:
        self._routes: dict[str, Route[Handler]] = {}
        self._subscription_routes: dict[str, Route[SubscriptionHandler]] = {}

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
        handler: Callable[[Command], AsyncGenerator[BaseModel, None]],
    ) -> None:
        self._subscription_routes[method] = Route(
            scope,
            command,
            cast(SubscriptionHandler, handler),
        )

    @staticmethod
    def _validate_call(
        routes: Mapping[str, Route[RouteHandler]],
        method: str,
        payload: Mapping[str, object],
        scopes: Collection[Scope],
    ) -> tuple[Route[RouteHandler], BaseModel] | ErrorEnvelope:
        route = routes.get(method)
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
        return route, command

    async def dispatch(
        self,
        method: str,
        payload: Mapping[str, object],
        scopes: Collection[Scope],
    ) -> BaseModel:
        call = self._validate_call(self._routes, method, payload, scopes)
        if isinstance(call, ErrorEnvelope):
            return call
        route, command = call
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
        payload: Mapping[str, object],
        scopes: Collection[Scope],
    ) -> AsyncGenerator[BaseModel, None]:
        call = self._validate_call(self._subscription_routes, method, payload, scopes)
        if isinstance(call, ErrorEnvelope):
            yield call
            return
        route, command = call
        try:
            async with aclosing(route.handler(command)) as subscription:
                async for event in subscription:
                    yield event
        except Exception:
            yield ErrorEnvelope(
                code=ErrorCode.INTERNAL,
                message="The subscription failed unexpectedly.",
                retryable=False,
            )


def build_dispatcher(
    state_dir: Path,
    *,
    event_log: EventLog | None = None,
) -> Dispatcher:
    store = ThreadStore(state_dir)
    event_log = event_log or EventLog(state_dir)
    dispatcher = Dispatcher()

    async def create_thread(command: ThreadCreateCommand) -> BaseModel:
        return store.create(command.title)

    async def list_threads(command: ThreadListCommand) -> BaseModel:
        return store.list()

    def subscribe_to_thread(
        command: ThreadSubscribeCommand,
    ) -> AsyncGenerator[BaseModel, None]:
        return event_log.subscribe(command.thread_id, command.after_sequence)

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
