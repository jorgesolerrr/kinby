"""Keep CLI commands dependent on the contract call shape, not core handlers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Mapping
from typing import Any

from pydantic import BaseModel

from kinby.contracts import Scope

Dispatch = Callable[
    [str, Mapping[str, Any], Collection[Scope]],
    Awaitable[BaseModel],
]


class ContractClient:
    def __init__(self, dispatch: Dispatch, scopes: Collection[Scope]) -> None:
        self._dispatch = dispatch
        self._scopes = frozenset(scopes)

    async def call(self, method: str, payload: Mapping[str, Any]) -> BaseModel:
        return await self._dispatch(method, payload, self._scopes)
