"""The hub's browser route: one browser for one turn of a browser-on instance, a cap on browsers
at once, and a browser service that runs only while it is needed (ADR 0113)."""

from __future__ import annotations

import asyncio
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from kinby.contracts import BrowserToken, ErrorCode
from kinby.core.errors import CoreError


@dataclass(frozen=True)
class BrowserLimits:
    #: How many browsers run at once.
    cap: int = 3
    #: How long a request waits for a browser to come free.
    wait_seconds: float = 60
    #: How long the service runs with no browser open before the hub stops it.
    idle_seconds: float = 300
    #: How long a token waits to be connected with before its browser goes back.
    connect_seconds: float = 60


class BrowserRuntime(Protocol):
    """Where the browser service runs."""

    async def start(self) -> str:
        """Start the browser service, and return the address its browser server listens at."""

    async def stop(self) -> None: ...


class BrowserCapReached(CoreError):
    code = ErrorCode.RESOURCE_EXHAUSTED
    retryable = True


@dataclass(frozen=True)
class BrowserConnection:
    """A token connected with: the service it relays to, and how its browser goes back."""

    address: str
    close: Callable[[], None]


class HubBrowsers:
    """Lend browsers on the service *runtime* runs, at most *limits.cap* at once."""

    def __init__(self, runtime: BrowserRuntime, limits: BrowserLimits) -> None:
        self._runtime = runtime
        self._limits = limits
        self._free = asyncio.Semaphore(limits.cap)
        self._lent = 0
        #: The instance each token not connected with yet was lent to.
        self._tokens: dict[BrowserToken, UUID] = {}
        self._address: str | None = None
        self._starting = asyncio.Lock()
        self._idle: asyncio.Task[None] | None = None

    async def lend(self, instance_id: UUID) -> BrowserToken:
        """A token that connects *instance_id* to a browser once, when one comes free in time."""
        try:
            async with asyncio.timeout(self._limits.wait_seconds):
                await self._free.acquire()
        except TimeoutError as exc:
            raise BrowserCapReached(
                f"The hub's browser cap of {self._limits.cap} is reached, and no browser came "
                f"free within {self._limits.wait_seconds:g} seconds. Try again later."
            ) from exc
        self._lent += 1
        if self._idle is not None:
            self._idle.cancel()
            self._idle = None
        try:
            async with self._starting:
                if self._address is None:
                    self._address = await self._runtime.start()
        except BaseException:
            self._give_back()
            raise
        token = BrowserToken(secrets.token_urlsafe(32))
        self._tokens[token] = instance_id
        asyncio.get_running_loop().call_later(self._limits.connect_seconds, self._expire, token)
        return token

    def connect(self, instance_id: UUID, token: str) -> BrowserConnection | None:
        """Spend *token*, when it was lent to *instance_id*. A token lent to another instance
        stays unspent."""
        lent = next((t for t in self._tokens if hmac.compare_digest(t, token)), None)
        if lent is None or self._tokens[lent] != instance_id or self._address is None:
            return None
        del self._tokens[lent]
        return BrowserConnection(self._address, self._give_back)

    def _expire(self, token: BrowserToken) -> None:
        if self._tokens.pop(token, None) is not None:
            self._give_back()

    def _give_back(self) -> None:
        self._lent -= 1
        self._free.release()
        if self._lent == 0:
            self._idle = asyncio.create_task(self._stop_when_idle())

    async def _stop_when_idle(self) -> None:
        await asyncio.sleep(self._limits.idle_seconds)
        # Past here, a browser asked for waits for the stop and starts the service anew.
        self._idle = None
        async with self._starting:
            if self._lent == 0 and self._address is not None:
                self._address = None
                await self._runtime.stop()
