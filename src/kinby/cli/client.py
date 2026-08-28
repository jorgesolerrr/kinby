"""Keep CLI commands dependent on the contract call shape, not core handlers."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable, Collection, Mapping

from kinby.contracts import ContractModel, ErrorCode, ErrorEnvelope, Method, Scope, Subscription

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
    AsyncGenerator[ContractModel],
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
    ) -> AsyncGenerator[Item | ErrorEnvelope]:
        stream = self._subscribe(subscription.name, command.model_dump(), self._scopes)
        async for item in stream:
            if isinstance(item, (subscription.item, ErrorEnvelope)):
                yield item
            else:
                yield UNEXPECTED_RESULT
