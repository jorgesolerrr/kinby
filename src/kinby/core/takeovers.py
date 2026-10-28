"""What every takeover shares, and the takeovers the user starts from the instance page with no
turn, to sign in and save the browser as a profile (ADR 0113)."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncGenerator
from contextlib import aclosing
from uuid import UUID

from kinby.contracts import (
    BrowserProfile,
    BrowserProfileName,
    BrowserTakeoverFinishCommand,
    BrowserTakeoverInputCommand,
    BrowserTakeoverStartCommand,
    Stream,
    TakeoverFrame,
    ThreadTakeoverInputResult,
)
from kinby.core.errors import BrowserNotOpen, BrowserProfileNotSaved, TakeoverNotFound
from kinby.plugins.browser import BrowserService, Navigate
from kinby.plugins.defaults.web import guard_url
from kinby.plugins.errors import exception_message


async def takeover_frames(screencast: AsyncGenerator[bytes]) -> AsyncGenerator[TakeoverFrame]:
    """The page of a takeover, frame by frame."""
    async with aclosing(screencast):
        async for image in screencast:
            yield TakeoverFrame(data=base64.b64encode(image).decode())


async def save_profile(
    browser: BrowserService, turn: UUID, name: BrowserProfileName
) -> BrowserProfile:
    """Save the browser the user signed in on as the profile *name*."""
    try:
        return await browser.save_profile(turn, name)
    except Exception as exc:
        raise BrowserProfileNotSaved(
            f"The profile was not saved: {exception_message(exc)}"
        ) from exc


class ProfileTakeovers:
    """Each takeover's browser stays open while the user watches it, until they save it."""

    def __init__(self, browser: BrowserService) -> None:
        self._browser = browser
        self._open: set[UUID] = set()

    async def start(self, command: BrowserTakeoverStartCommand) -> Stream[TakeoverFrame]:
        """Open a browser at the command's URL, and stream its page until the watching stops."""
        takeover_id = command.takeover_id
        self._open.add(takeover_id)
        try:
            await guard_url(command.url)
            await self._browser.open(takeover_id)
            await self._browser.run(takeover_id, Navigate(command.url))
        except Exception as exc:
            await self._close(takeover_id)
            raise BrowserNotOpen(f"The browser did not open: {exception_message(exc)}") from exc
        except asyncio.CancelledError:
            # The user left before the page loaded, so nothing watches the browser to close it.
            await self._close(takeover_id)
            raise
        return Stream(0, self._watched(takeover_id))

    async def send_input(self, command: BrowserTakeoverInputCommand) -> ThreadTakeoverInputResult:
        self._require(command.takeover_id)
        await self._browser.send(command.takeover_id, command.input)
        return ThreadTakeoverInputResult()

    async def finish(self, command: BrowserTakeoverFinishCommand) -> BrowserProfile:
        """Save the takeover's browser as a profile, then close it. A save that fails leaves it
        open, so the user's sign-in is not lost."""
        self._require(command.takeover_id)
        saved = await save_profile(self._browser, command.takeover_id, command.profile)
        await self._close(command.takeover_id)
        return saved

    async def _watched(self, takeover_id: UUID) -> AsyncGenerator[TakeoverFrame]:
        try:
            async for frame in takeover_frames(self._browser.screencast(takeover_id)):
                yield frame
        finally:
            await self._close(takeover_id)

    def _require(self, takeover_id: UUID) -> None:
        if takeover_id not in self._open:
            raise TakeoverNotFound(f'Takeover "{takeover_id}" was not found.')

    async def _close(self, takeover_id: UUID) -> None:
        if takeover_id in self._open:
            self._open.discard(takeover_id)
            await self._browser.close(takeover_id)
