"""Keep CLI commands dependent on the contract call shape, not core handlers."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable, Collection, Mapping

from pydantic import BaseModel

from kinby.contracts import Scope

Dispatch = Callable[
    [str, Mapping[str, object], Collection[Scope]],
    Awaitable[BaseModel],
]
Subscribe = Callable[
    [str, Mapping[str, object], Collection[Scope]],
    AsyncGenerator[BaseModel, None],
]


class ContractClient:
    def __init__(
        self,
        dispatch: Dispatch,
        subscribe: Subscribe,
        scopes: Collection[Scope],
    ) -> None:
        self._dispatch = dispatch
        self._subscribe = subscribe
        self._scopes = frozenset(scopes)

    async def call(self, method: str, payload: Mapping[str, object]) -> BaseModel:
        return await self._dispatch(method, payload, self._scopes)

    def subscribe(
        self,
        method: str,
        payload: Mapping[str, object],
    ) -> AsyncGenerator[BaseModel, None]:
        return self._subscribe(method, payload, self._scopes)
