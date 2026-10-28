"""The hub's browsers: a cap on browsers at once, and a browser service that runs only while one
is open (ADR 0114)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from kinby.contracts import ErrorCode
from kinby.core.errors import CoreError


@dataclass(frozen=True)
class BrowserLimits:
    #: How many browsers run at once.
    cap: int = 3
    #: How long a request waits for a browser to come free.
    wait_seconds: float = 60
    #: How long the service runs with no browser open before the hub stops it.
    idle_seconds: float = 300
    #: How long a turn's browser goes unused before the hub closes it. A turn parked on a
    #: takeover waits 15 minutes for the user, and keeps its browser for them.
    turn_idle_seconds: float = 20 * 60


class BrowserRuntime(Protocol):
    """Where the browser service runs."""

    async def start(self) -> str:
        """Start the browser service, and return the address the hub connects each browser at,
        with the options the service launches it with."""

    async def running(self) -> bool:
        """Whether the service started last still runs, or died since."""

    async def stop(self) -> None: ...


class BrowserCapReached(CoreError):
    code = ErrorCode.RESOURCE_EXHAUSTED
    retryable = True


class HubBrowsers:
    """Lend browsers on the service *runtime* runs, at most *limits.cap* at once."""

    def __init__(self, runtime: BrowserRuntime, limits: BrowserLimits) -> None:
        self._runtime = runtime
        self._limits = limits
        self._free = asyncio.Semaphore(limits.cap)
        self._lent = 0
        self._address: str | None = None
        self._starting = asyncio.Lock()
        self._idle: asyncio.Task[None] | None = None

    async def lend(self) -> str:
        """The address to connect one browser at, once one comes free in time. The browser goes
        back with give_back."""
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
                address = self._address
                if address is None or not await self._runtime.running():
                    address = self._address = await self._runtime.start()
        except BaseException:
            self.give_back()
            raise
        return address

    def give_back(self) -> None:
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
