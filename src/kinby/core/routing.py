"""Put scope and payload checks on the one path every client call crosses, the instance's and
the hub's alike. It loads nothing of the instance core, so the hub stays without it."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable, Collection, Mapping
from contextlib import aclosing
from dataclasses import dataclass
from typing import cast

from pydantic import ValidationError

from kinby.contracts import (
    INSTANCE_DRAIN,
    STEP_RUN,
    Capability,
    ContractModel,
    ErrorCode,
    ErrorEnvelope,
    Method,
    Scope,
    Stream,
    Subscription,
)
from kinby.core.errors import CoreError

Handler = Callable[[ContractModel], Awaitable[ContractModel]]
SubscriptionHandler = Callable[[ContractModel], Awaitable[Stream[ContractModel]]]
_SUBSCRIPTION_FAILED = ErrorEnvelope(
    code=ErrorCode.INTERNAL,
    message="The subscription failed unexpectedly.",
    retryable=False,
)


async def _guarded(items: AsyncGenerator[ContractModel]) -> AsyncGenerator[ContractModel]:
    """Turn a failure mid-stream into a last item, so a subscriber always sees why it ended."""
    try:
        async with aclosing(items):
            async for item in items:
                yield item
    except Exception:
        yield _SUBSCRIPTION_FAILED


@dataclass(frozen=True)
class Route[RouteHandler]:
    scope: Scope
    command: type[ContractModel]
    handler: RouteHandler
    alternative_scope: Scope | None = None

    def admits(self, scopes: Collection[Scope]) -> bool:
        return self.scope in scopes or self.alternative_scope in scopes


class Dispatcher:
    def __init__(self) -> None:
        self._routes: dict[str, Route[Handler]] = {}
        self._subscription_routes: dict[str, Route[SubscriptionHandler]] = {}

    def register[Command: ContractModel, Result: ContractModel](
        self,
        method: Method[Command, Result],
        handler: Callable[[Command], Awaitable[Result]],
    ) -> None:
        self._routes[method.name] = Route(
            method.scope,
            method.command,
            cast(Handler, handler),
            method.alternative_scope,
        )

    def register_subscription[Command: ContractModel, Item: ContractModel](
        self,
        subscription: Subscription[Command, Item],
        handler: Callable[[Command], Awaitable[Stream[Item]]],
    ) -> None:
        self._subscription_routes[subscription.name] = Route(
            subscription.scope,
            subscription.command,
            cast(SubscriptionHandler, handler),
        )

    @staticmethod
    def _validate_call[RouteHandler](
        routes: Mapping[str, Route[RouteHandler]],
        method: str,
        payload: Mapping[str, object],
        scopes: Collection[Scope],
    ) -> tuple[Route[RouteHandler], ContractModel] | ErrorEnvelope:
        route = routes.get(method)
        if route is None:
            return ErrorEnvelope(
                code=ErrorCode.NOT_FOUND,
                message=f'Method "{method}" was not found.',
                retryable=False,
            )
        if not route.admits(scopes):
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
    ) -> ContractModel:
        call = self._validate_call(self._routes, method, payload, scopes)
        if isinstance(call, ErrorEnvelope):
            return call
        route, command = call
        try:
            return await route.handler(command)
        except CoreError as exc:
            return exc.envelope()
        except Exception:
            return ErrorEnvelope(
                code=ErrorCode.INTERNAL,
                message="The method failed unexpectedly.",
                retryable=False,
            )

    def handles[Command: ContractModel, Result: ContractModel](
        self,
        method: Method[Command, Result],
    ) -> bool:
        return method.name in self._routes

    async def subscribe(
        self,
        method: str,
        payload: Mapping[str, object],
        scopes: Collection[Scope],
    ) -> Stream[ContractModel] | ErrorEnvelope:
        call = self._validate_call(self._subscription_routes, method, payload, scopes)
        if isinstance(call, ErrorEnvelope):
            return call
        route, command = call
        try:
            stream = await route.handler(command)
        except CoreError as exc:
            return exc.envelope()
        except Exception:
            return _SUBSCRIPTION_FAILED
        return Stream(stream.head_sequence, _guarded(stream.items), _close=stream._close)


def instance_capabilities(dispatcher: Dispatcher) -> list[Capability]:
    """What a hub may ask of this instance: the socket, plus the lifecycle methods it serves."""
    return [
        Capability.WS,
        *([Capability.DRAIN] if dispatcher.handles(INSTANCE_DRAIN) else []),
        *([Capability.STEP_ONCE] if dispatcher.handles(STEP_RUN) else []),
    ]
