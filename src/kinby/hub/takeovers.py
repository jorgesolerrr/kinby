"""The takeovers the user drives on the hub, which keeps the page: the page of a turn parked on a
takeover, and a page with no turn, opened to sign in and save as a profile (ADR 0114)."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from uuid import UUID

from kinby.contracts import (
    AcceptedResult,
    BrowserCloseCommand,
    BrowserNavigate,
    BrowserOpenCommand,
    BrowserProfile,
    BrowserProfileName,
    BrowserProfileSaveCommand,
    BrowserRunCommand,
    BrowserTakeoverFinishCommand,
    BrowserTakeoverInputCommand,
    BrowserTakeoverStartCommand,
    Stream,
    TakeoverFrame,
    ThreadTakeoverFinishCommand,
    ThreadTakeoverInputCommand,
    ThreadTakeoverInputResult,
    ThreadTakeoverResumeCommand,
    ThreadTakeoverStartCommand,
)
from kinby.core.errors import BrowserNotOpen, BrowserProfileNotSaved, TakeoverNotFound
from kinby.hub.playwright_browser import PlaywrightBrowser
from kinby.plugins.errors import exception_message

#: Tell the instance that the user finished the takeover its turn is parked on.
type Resume = Callable[[UUID, ThreadTakeoverResumeCommand], Awaitable[AcceptedResult]]
type _Takeover = tuple[UUID, UUID]


class Takeovers:
    """A turn's takeover drives the page its turn left open. A takeover with no turn keeps a page
    of its own open while the user watches it, until they save it as a profile, and opens one
    only for an instance whose browser is on."""

    def __init__(
        self, browsers: PlaywrightBrowser, browser_on: Callable[[UUID], bool], resume: Resume
    ) -> None:
        self._browsers = browsers
        self._browser_on = browser_on
        self._resume = resume
        self._open: set[_Takeover] = set()

    async def watch_turn(self, command: ThreadTakeoverStartCommand) -> Stream[TakeoverFrame]:
        return Stream(0, await self._browsers.screencast(command.instance_id, command.turn_id))

    async def drive_turn(self, command: ThreadTakeoverInputCommand) -> ThreadTakeoverInputResult:
        await self._browsers.send_input(command.instance_id, command.turn_id, command.input)
        return ThreadTakeoverInputResult()

    async def finish_turn(self, command: ThreadTakeoverFinishCommand) -> AcceptedResult:
        """Save the turn's page as the profile the command names, which the turn goes on with,
        then tell the instance, whose turn resumes."""
        if command.profile is not None:
            await self._save(command.instance_id, command.turn_id, command.profile)
        resumed = ThreadTakeoverResumeCommand(
            thread_id=command.thread_id, takeover_id=command.takeover_id
        )
        return await self._resume(command.instance_id, resumed)

    async def start(self, command: BrowserTakeoverStartCommand) -> Stream[TakeoverFrame]:
        """Open a page at the command's URL, and stream it until the watching stops."""
        instance_id, page = command.instance_id, command.takeover_id
        if not self._browser_on(instance_id):
            raise BrowserNotOpen(f'Instance "{instance_id}" has the browser off.')
        takeover = (instance_id, page)
        self._open.add(takeover)
        try:
            await self._browsers.open(instance_id, BrowserOpenCommand(turn_id=page))
            navigate = BrowserNavigate(url=command.url)
            await self._browsers.run(instance_id, BrowserRunCommand(turn_id=page, action=navigate))
            frames = await self._browsers.screencast(instance_id, page)
        except BaseException:
            # Nothing watches the page yet to close it, as when the user leaves while it loads.
            await self._close(takeover)
            raise
        return Stream(0, self._watched(takeover, frames))

    async def send_input(self, command: BrowserTakeoverInputCommand) -> ThreadTakeoverInputResult:
        self._require(command.instance_id, command.takeover_id)
        await self._browsers.send_input(command.instance_id, command.takeover_id, command.input)
        return ThreadTakeoverInputResult()

    async def finish(self, command: BrowserTakeoverFinishCommand) -> BrowserProfile:
        """Save the takeover's page as a profile, then close it. A save that fails leaves it
        open, so the user's sign-in is not lost."""
        takeover = self._require(command.instance_id, command.takeover_id)
        saved = await self._save(command.instance_id, command.takeover_id, command.profile)
        await self._close(takeover)
        return saved

    async def _save(
        self, instance_id: UUID, page: UUID, name: BrowserProfileName
    ) -> BrowserProfile:
        try:
            return await self._browsers.save_profile(
                instance_id, BrowserProfileSaveCommand(turn_id=page, name=name)
            )
        except Exception as exc:
            raise BrowserProfileNotSaved(
                f"The profile was not saved: {exception_message(exc)}"
            ) from exc

    async def _watched(
        self, takeover: _Takeover, frames: AsyncGenerator[TakeoverFrame]
    ) -> AsyncGenerator[TakeoverFrame]:
        try:
            async for frame in frames:
                yield frame
        finally:
            await self._close(takeover)

    def _require(self, instance_id: UUID, takeover_id: UUID) -> _Takeover:
        takeover = (instance_id, takeover_id)
        if takeover not in self._open:
            raise TakeoverNotFound(f'Takeover "{takeover_id}" was not found.')
        return takeover

    async def _close(self, takeover: _Takeover) -> None:
        if takeover in self._open:
            self._open.discard(takeover)
            instance_id, page = takeover
            await self._browsers.close(instance_id, BrowserCloseCommand(turn_id=page))
