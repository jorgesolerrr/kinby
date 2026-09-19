"""Keep CLI commands dependent on the contract call shape, not core handlers."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable, Collection, Mapping
from contextlib import aclosing

from kinby.contracts import (
    ContractModel,
    ErrorCode,
    ErrorEnvelope,
    Method,
    Scope,
    Stream,
    Subscription,
)

UNEXPECTED_RESULT = ErrorEnvelope(
    code=ErrorCode.INTERNAL,
    message="The method returned an unexpected result.",
    retryable=False,
)

Dispatch = Callable[
    [str, Mapping[str, object], Collection[Scope]],
    Awaitable[ContractModel],
]
Subscribe = Callable[
    [str, Mapping[str, object], Collection[Scope]],
    Awaitable[Stream[ContractModel] | ErrorEnvelope],
]


def format_error(error: ErrorEnvelope) -> str:
    return f"{error.code.value}: {error.message}"


class ContractClient:
    """Parse the wire's generic result into the type the method promises, once."""

    def __init__(
        self,
        dispatch: Dispatch,
        subscribe: Subscribe,
        scopes: Collection[Scope],
    ) -> None:
        self._dispatch = dispatch
        self._subscribe = subscribe
        self._scopes = frozenset(scopes)

    async def call[Command: ContractModel, Result: ContractModel](
        self,
        method: Method[Command, Result],
        command: Command,
    ) -> Result | ErrorEnvelope:
        result = await self._dispatch(method.name, command.model_dump(), self._scopes)
        if isinstance(result, (method.result, ErrorEnvelope)):
            return result
        return UNEXPECTED_RESULT

    async def subscribe[Command: ContractModel, Item: ContractModel](
        self,
        subscription: Subscription[Command, Item],
        command: Command,
    ) -> Stream[Item | ErrorEnvelope] | ErrorEnvelope:
        stream = await self._subscribe(subscription.name, command.model_dump(), self._scopes)
        if isinstance(stream, ErrorEnvelope):
            return stream
        return stream.carrying(self._items(subscription, stream.items))

    @staticmethod
    async def _items[Command: ContractModel, Item: ContractModel](
        subscription: Subscription[Command, Item],
        items: AsyncGenerator[ContractModel],
    ) -> AsyncGenerator[Item | ErrorEnvelope]:
        async with aclosing(items):
            async for item in items:
                if isinstance(item, (subscription.item, ErrorEnvelope)):
                    yield item
                else:
                    yield UNEXPECTED_RESULT
