"""Put scope and payload checks on the one path every client call crosses."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Mapping
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
)
from kinby.core.threads import ThreadStore

Handler = Callable[[BaseModel], Awaitable[BaseModel]]
Command = TypeVar("Command", bound=BaseModel)


@dataclass(frozen=True)
class Route:
    scope: Scope
    command: type[BaseModel]
    handler: Handler


class Dispatcher:
    def __init__(self) -> None:
        self._routes: dict[str, Route] = {}

    def register(
        self,
        method: str,
        scope: Scope,
        command: type[Command],
        handler: Callable[[Command], Awaitable[BaseModel]],
    ) -> None:
        self._routes[method] = Route(scope, command, cast(Handler, handler))

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


def build_dispatcher(state_dir: Path) -> Dispatcher:
    store = ThreadStore(state_dir)
    dispatcher = Dispatcher()

    async def create_thread(command: ThreadCreateCommand) -> BaseModel:
        return store.create(command.title)

    async def list_threads(command: ThreadListCommand) -> BaseModel:
        return store.list()

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
    return dispatcher
