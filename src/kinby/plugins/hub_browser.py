"""The browser service as the hub drives it: each turn's page lives on the hub, which the instance
reaches only through the hub's browser methods (ADR 0114)."""

from __future__ import annotations

import asyncio
import base64
from uuid import UUID

from kinby.contracts import (
    BROWSER_CLOSE,
    BROWSER_FILE_BYTES,
    BROWSER_OPEN,
    BROWSER_PROFILE_SAVE,
    BROWSER_PROFILES,
    BROWSER_RUN,
    BROWSER_SCREENSHOT,
    BROWSER_SNAPSHOT,
    BROWSER_TEXT,
    BrowserClick,
    BrowserCloseCommand,
    BrowserFile,
    BrowserNavigate,
    BrowserOpenCommand,
    BrowserPageAction,
    BrowserPress,
    BrowserProfile,
    BrowserProfileListResult,
    BrowserProfileName,
    BrowserProfileSaveCommand,
    BrowserProfilesCommand,
    BrowserRunCommand,
    BrowserScreenshotCommand,
    BrowserScroll,
    BrowserSelect,
    BrowserSnapshotCommand,
    BrowserTextCommand,
    BrowserType,
    BrowserUpload,
    BrowserWait,
    ContractModel,
    Method,
)
from kinby.plugins.browser import (
    BrowserAction,
    Click,
    Download,
    Navigate,
    Page,
    PageRead,
    Press,
    Scroll,
    Select,
    Type,
    Upload,
    Wait,
)
from kinby.plugins.hub import call_hub


class HubBrowser:
    """A BrowserService whose contexts the hub keeps, one for each of this instance's turns."""

    def __init__(self) -> None:
        #: Every turn ends by closing its browser, which most turns never opened. A turn counts
        #: from the open's call, since the hub finishes an open whose caller went away.
        self._opened: set[UUID] = set()

    async def open(self, turn: UUID, profile: BrowserProfileName | None = None) -> None:
        # An open that failed may still leave the turn a page, which the hub opens for a takeover's
        # live view, so the turn's close goes to the hub all the same.
        self._opened.add(turn)
        await _call(BROWSER_OPEN, BrowserOpenCommand(turn_id=turn, profile=profile))

    async def run(self, turn: UUID, action: BrowserAction) -> Page:
        page = await _call(BROWSER_RUN, BrowserRunCommand(turn_id=turn, action=await _sent(action)))
        return Page(
            url=page.url,
            title=page.title,
            downloads=tuple(
                Download(file.name, base64.b64decode(file.content)) for file in page.downloads
            ),
        )

    async def snapshot(self, turn: UUID, *, interactive: bool, max_chars: int) -> PageRead:
        command = BrowserSnapshotCommand(turn_id=turn, interactive=interactive, max_chars=max_chars)
        read = await _call(BROWSER_SNAPSHOT, command)
        return PageRead(url=read.url, text=read.text)

    async def text(self, turn: UUID, ref: str | None) -> PageRead:
        read = await _call(BROWSER_TEXT, BrowserTextCommand(turn_id=turn, ref=ref))
        return PageRead(url=read.url, text=read.text)

    async def screenshot(self, turn: UUID) -> bytes:
        shot = await _call(BROWSER_SCREENSHOT, BrowserScreenshotCommand(turn_id=turn))
        return base64.b64decode(shot.image)

    async def profiles(self) -> BrowserProfileListResult:
        return await _call(BROWSER_PROFILES, BrowserProfilesCommand())

    async def save_profile(self, turn: UUID, name: BrowserProfileName) -> BrowserProfile:
        return await _call(BROWSER_PROFILE_SAVE, BrowserProfileSaveCommand(turn_id=turn, name=name))

    async def close(self, turn: UUID) -> None:
        if turn in self._opened:
            self._opened.discard(turn)
            await _call(BROWSER_CLOSE, BrowserCloseCommand(turn_id=turn))


async def _call[Command: ContractModel, Result: ContractModel](
    method: Method[Command, Result], command: Command
) -> Result:
    result = await call_hub(method.name, command.model_dump(mode="json"), "the browser")
    return method.result.model_validate(result)


async def _sent(action: BrowserAction) -> BrowserPageAction:
    """*action* as the hub takes it, an upload with its file's content."""
    match action:
        case Navigate(url=url):
            return BrowserNavigate(url=url)
        case Scroll(direction=direction, ref=ref):
            return BrowserScroll(direction=direction, ref=ref)
        case Wait(text=text, seconds=seconds):
            return BrowserWait(text=text, seconds=seconds)
        case Click(ref=ref):
            return BrowserClick(ref=ref)
        case Type(ref=ref, text=text):
            return BrowserType(ref=ref, text=text)
        case Select(ref=ref, value=value):
            return BrowserSelect(ref=ref, value=value)
        case Press(key=key):
            return BrowserPress(key=key)
        case Upload(ref=ref, path=path):
            if (size := (await asyncio.to_thread(path.stat)).st_size) > BROWSER_FILE_BYTES:
                raise ValueError(
                    f"{path.name} is {size:,} bytes, over the browser's cap of "
                    f"{BROWSER_FILE_BYTES:,}."
                )
            content = base64.b64encode(await asyncio.to_thread(path.read_bytes)).decode()
            return BrowserUpload(ref=ref, file=BrowserFile(name=path.name, content=content))
