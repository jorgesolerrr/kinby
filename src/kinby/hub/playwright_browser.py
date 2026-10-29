"""The hub's Playwright client: one context for each turn of each browser-on instance, on the
browser service the hub runs, which the instance reaches only through the hub's browser methods
(ADR 0114)."""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import re
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from urllib.parse import quote, quote_plus, urljoin, urlsplit
from uuid import UUID

from playwright.async_api import (
    Browser,
    BrowserContext,
    CDPSession,
    Locator,
    Playwright,
    async_playwright,
)
from playwright.async_api import Download as PlaywrightDownload
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page as PlaywrightPage
from playwright.async_api import StorageState as PlaywrightState
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pydantic import TypeAdapter

from kinby.contracts import (
    BROWSER_FILE_BYTES,
    BrowserClick,
    BrowserCloseCommand,
    BrowserCloseResult,
    BrowserFile,
    BrowserFillCredential,
    BrowserNavigate,
    BrowserOpenCommand,
    BrowserOpenResult,
    BrowserPage,
    BrowserPageAction,
    BrowserPageRead,
    BrowserPress,
    BrowserProfile,
    BrowserProfileName,
    BrowserProfileSaveCommand,
    BrowserRunCommand,
    BrowserScreenshot,
    BrowserScreenshotCommand,
    BrowserScroll,
    BrowserSelect,
    BrowserSnapshotCommand,
    BrowserTextCommand,
    BrowserType,
    BrowserUpload,
    BrowserWait,
    TakeoverFrame,
    TakeoverInput,
    TakeoverKey,
    TakeoverPointer,
    TakeoverWheel,
    VaultField,
)
from kinby.core.errors import BrowserActionFailed, BrowserNotOpen, VaultFillRefused
from kinby.hub.browser import BrowserLimits, BrowserRuntime, HubBrowsers
from kinby.hub.browser_profiles import BrowserProfiles, StorageState
from kinby.hub.vault import Vault
from kinby.plugins.defaults.web import REFUSED_HEADER, capped

_NAVIGATION_MS = 30_000
#: How sharp a takeover's live view is, out of 100.
_SCREENCAST_QUALITY = 70
#: How long a live view waits for the page to change before it gets the frame it has again.
_FRAME_SECONDS = 10
_ELEMENT_MS = 5_000
#: How long a click or a key press waits for a download it may have started.
_DOWNLOAD_SECONDS = 1
#: The keys a password field takes from browser_press: they move on or submit, and type nothing.
_PASSWORD_KEYS = frozenset({"Enter", "Tab", "Shift+Tab", "Escape"})
#: The roles of the elements an interactive snapshot keeps.
_INTERACTIVE = frozenset(
    {
        "button",
        "checkbox",
        "combobox",
        "link",
        "listbox",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "radio",
        "searchbox",
        "slider",
        "spinbutton",
        "switch",
        "tab",
        "textbox",
        "treeitem",
    }
)
_ROLE = re.compile(r"^\s*- (\w+)")
_HREF = re.compile(r'^(\s*- /url: )"?(.*?)"?$', re.MULTILINE)
_URL_LINE = "- /url: "
#: What Playwright's call log says when another element would take a click.
_COVERED = re.compile(r"- (.+) intercepts pointer events$", re.MULTILINE)
#: A ref in the page's own frame. A ref like f1e3 reaches into a child frame, which can be on
#: another site.
_FRAME_REF = re.compile(r"e\d+")
_PASSWORD_FIELD = "input[type=password i]"
#: What the turn reads in place of a vault value the page holds.
_HIDDEN = "[a vault value]"

type _Turn = tuple[UUID, UUID]
_STATE = TypeAdapter(StorageState)


@dataclass
class _Screencast:
    """A takeover's live view of *page*: its newest frame, and whether it changed since the last
    one the view got."""

    page: PlaywrightPage
    session: CDPSession
    latest: str | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def show(self, frame: str) -> None:
        self.latest = frame
        self.changed.set()

    async def next(self) -> str:
        with suppress(TimeoutError):
            await asyncio.wait_for(self.changed.wait(), _FRAME_SECONDS)
        self.changed.clear()
        if self.latest is None:
            raise BrowserActionFailed("The page showed no frame to watch.")
        return self.latest


@dataclass
class _Opened:
    playwright: Playwright
    browser: Browser
    context: BrowserContext
    profile: BrowserProfileName | None
    #: The downloads begun since the last action returned, which the next action returns.
    downloads: list[PlaywrightDownload] = field(default_factory=list)
    begun: asyncio.Event = field(default_factory=asyncio.Event)
    idle: asyncio.TimerHandle | None = None
    screencast: _Screencast | None = None
    #: The usernames and passwords the vault filled into the turn's pages, which the turn reads
    #: hidden.
    filled: set[str] = field(default_factory=set)

    def hidden(self, text: str) -> str:
        """*text* with each filled value hidden, as the page holds it and as a URL encodes it."""
        for value in self.filled:
            for written in {value, quote(value, safe=""), quote_plus(value)}:
                text = text.replace(written, _HIDDEN)
        return text

    def collect(self, download: PlaywrightDownload) -> None:
        self.downloads.append(download)
        self.begun.set()

    async def page(self) -> PlaywrightPage:
        """The turn's newest page, which a link that opens a new window makes."""
        return self.context.pages[-1] if self.context.pages else await self.context.new_page()


class PlaywrightBrowser:
    """A fresh context for each turn of each instance, on a browser the service *runtime* runs
    lends it, at most *limits.cap* at once. A context opens with one of the instance's
    *profiles*, and its state is saved back to the profile when it closes. A sign-in fills from
    the instance's *vault*, and the turn never reads a value it filled."""

    def __init__(
        self,
        runtime: BrowserRuntime,
        limits: BrowserLimits,
        profiles: BrowserProfiles,
        vault: Vault,
    ) -> None:
        self._browsers = HubBrowsers(runtime, limits)
        self._profiles = profiles
        self._vault = vault
        self._idle_seconds = limits.turn_idle_seconds
        self._opened: dict[_Turn, _Opened] = {}
        #: Each turn's open and close take turns, so a close that comes while the open is still
        #: connecting closes what it opens.
        self._turns: dict[_Turn, asyncio.Lock] = {}
        self._closing: set[asyncio.Task[None]] = set()

    async def open(self, instance_id: UUID, command: BrowserOpenCommand) -> BrowserOpenResult:
        turn = (instance_id, command.turn_id)
        async with self._turns.setdefault(turn, asyncio.Lock()):
            return await self._open(turn, command.profile)

    async def _open(self, turn: _Turn, profile: BrowserProfileName | None) -> BrowserOpenResult:
        if (opened := self._opened.get(turn)) is not None:
            if profile is not None and profile != opened.profile:
                raise BrowserActionFailed(
                    f'The browser is open already, without the profile "{profile}". Open a '
                    "profile with the turn's first browser call."
                )
            self._touch(turn, opened)
            return BrowserOpenResult()
        state = self._profiles.state(turn[0], profile) if profile is not None else None
        address = await self._browsers.lend()
        try:
            opened = await _connected(address, state, profile)
        except BaseException:
            self._browsers.give_back()
            raise
        self._opened[turn] = opened
        self._touch(turn, opened)
        return BrowserOpenResult()

    async def run(self, instance_id: UUID, command: BrowserRunCommand) -> BrowserPage:
        opened = self._turn(instance_id, command.turn_id)
        with _on_the_page(opened):
            page = await opened.page()
            if isinstance(command.action, BrowserFillCredential):
                await self._fill(instance_id, opened, page, command.action)
            else:
                await _run(page, command.action)
            # A download begins when its server answers, which can be after the action returns.
            if isinstance(command.action, BrowserClick | BrowserPress):
                with suppress(TimeoutError):
                    await asyncio.wait_for(opened.begun.wait(), _DOWNLOAD_SECONDS)
            downloads = [*opened.downloads]
            opened.downloads.clear()
            opened.begun.clear()
            files = await _downloaded(downloads)
            return BrowserPage(
                url=opened.hidden(page.url),
                title=opened.hidden(await page.title()),
                downloads=tuple(
                    file.model_copy(update={"name": opened.hidden(file.name)}) for file in files
                ),
            )

    async def snapshot(self, instance_id: UUID, command: BrowserSnapshotCommand) -> BrowserPageRead:
        opened = self._turn(instance_id, command.turn_id)
        with _on_the_page(opened):
            page = await opened.page()
            tree = _HREF.sub(
                lambda href: f"{href.group(1)}{urljoin(page.url, href.group(2))}",
                await page.aria_snapshot(mode="ai"),
            )
        # Hidden before the cut, so the cut cannot leave part of a value.
        tree = opened.hidden(tree)
        if command.interactive:
            tree = _interactive(tree)
        return BrowserPageRead(url=opened.hidden(page.url), text=capped(tree, command.max_chars))

    async def text(self, instance_id: UUID, command: BrowserTextCommand) -> BrowserPageRead:
        opened = self._turn(instance_id, command.turn_id)
        with _on_the_page(opened):
            page = await opened.page()
            element = await _element(page, command.ref) if command.ref else page.locator("body")
            text = await element.inner_text(timeout=_ELEMENT_MS)
            return BrowserPageRead(url=opened.hidden(page.url), text=opened.hidden(text))

    async def screenshot(
        self, instance_id: UUID, command: BrowserScreenshotCommand
    ) -> BrowserScreenshot:
        opened = self._turn(instance_id, command.turn_id)
        with _on_the_page(opened):
            image = await (await opened.page()).screenshot(type="png")
        return BrowserScreenshot(image=base64.b64encode(image).decode())

    async def screencast(self, instance_id: UUID, turn_id: UUID) -> AsyncGenerator[TakeoverFrame]:
        """The live view of the turn's page: a frame each time the page changes, until the
        caller stops reading or the turn's context closes."""
        opened = self._turn(instance_id, turn_id)
        with _on_the_page():
            page = await opened.page()
            # A tab the page opened since the last takeover is the one the user's input reaches.
            if opened.screencast is None or opened.screencast.page is not page:
                if opened.screencast is not None:
                    with suppress(PlaywrightError):
                        await opened.screencast.session.detach()
                opened.screencast = await _screencast(page)
        return self._frames(instance_id, turn_id, opened.screencast)

    async def send_input(self, instance_id: UUID, turn_id: UUID, user_input: TakeoverInput) -> None:
        """Pass the user's mouse or keyboard to the turn's page."""
        opened = self._turn(instance_id, turn_id)
        with _on_the_page():
            page = await opened.page()
            match user_input:
                case TakeoverPointer(action=action, x=x, y=y):
                    await page.mouse.move(x, y)
                    if action == "down":
                        await page.mouse.down()
                    elif action == "up":
                        await page.mouse.up()
                case TakeoverWheel(delta_x=delta_x, delta_y=delta_y):
                    await page.mouse.wheel(delta_x, delta_y)
                case TakeoverKey(key=key):
                    await page.keyboard.press(key)

    async def save_profile(
        self, instance_id: UUID, command: BrowserProfileSaveCommand
    ) -> BrowserProfile:
        """Save the turn's context as the instance's profile, for the site of its page. Its state
        is saved back to the profile when it closes."""
        opened = self._turn(instance_id, command.turn_id)
        with _on_the_page():
            page = await opened.page()
            state = await opened.context.storage_state()
        site = urlsplit(page.url).hostname or page.url
        saved = self._profiles.save(instance_id, command.name, site, _kept(state))
        opened.profile = command.name
        return saved

    async def close(self, instance_id: UUID, command: BrowserCloseCommand) -> BrowserCloseResult:
        await self._close((instance_id, command.turn_id))
        return BrowserCloseResult()

    async def close_instance(self, instance_id: UUID) -> None:
        """Close the contexts of the instance's turns, which a stopped instance cannot close."""
        for turn in [turn for turn in self._opened if turn[0] == instance_id]:
            await self._close(turn)

    async def _fill(
        self,
        instance_id: UUID,
        opened: _Opened,
        page: PlaywrightPage,
        action: BrowserFillCredential,
    ) -> None:
        """Fill the field with the vault value *action* names, on the entry's site only: a
        password into a password field, which a snapshot and a screenshot show masked, and a
        username or a code into any other field."""
        if not _FRAME_REF.fullmatch(action.ref):
            raise VaultFillRefused(
                f"The field with ref {action.ref} is in a frame of the page. A vault entry fills "
                "a field of the page itself."
            )
        element = await _element(page, action.ref)
        password_field = await element.and_(page.locator(_PASSWORD_FIELD)).count() > 0
        if password_field != (action.field == VaultField.PASSWORD):
            kind = "a password field" if password_field else "not a password field"
            raise VaultFillRefused(
                f"The field with ref {action.ref} is {kind}, so the vault fills no {action.field} "
                "into it."
            )
        value = self._vault.value(instance_id, action.name, action.field, page.url)
        # A TOTP code is used once, and its six digits could match unrelated text (ADR 0114).
        if action.field != VaultField.TOTP:
            opened.filled.add(value)
        try:
            await element.fill(value, timeout=_ELEMENT_MS)
        except PlaywrightError:
            # Playwright's message can quote what it filled.
            raise BrowserActionFailed(
                f"The vault could not fill the field with ref {action.ref}."
            ) from None

    async def _frames(
        self, instance_id: UUID, turn_id: UUID, screencast: _Screencast
    ) -> AsyncGenerator[TakeoverFrame]:
        while True:
            # A watched context is not idle, and one that closed ends the view.
            self._turn(instance_id, turn_id)
            yield TakeoverFrame(data=await screencast.next())

    def _turn(self, instance_id: UUID, turn_id: UUID) -> _Opened:
        """The context of the instance's turn. Another instance's turn of that id is not open."""
        turn = (instance_id, turn_id)
        if (opened := self._opened.get(turn)) is None:
            raise BrowserNotOpen(f'Turn "{turn_id}" has no browser open.')
        self._touch(turn, opened)
        return opened

    def _touch(self, turn: _Turn, opened: _Opened) -> None:
        """Push back when the turn's context closes for sitting idle."""
        if opened.idle is not None:
            opened.idle.cancel()
        opened.idle = asyncio.get_running_loop().call_later(
            self._idle_seconds, self._close_idle, turn
        )

    def _close_idle(self, turn: _Turn) -> None:
        closing = asyncio.create_task(self._close(turn))
        self._closing.add(closing)
        closing.add_done_callback(self._closing.discard)

    async def _close(self, turn: _Turn) -> None:
        async with self._turns.setdefault(turn, asyncio.Lock()):
            await self._closed(turn)
            self._turns.pop(turn, None)

    async def _closed(self, turn: _Turn) -> None:
        opened = self._opened.pop(turn, None)
        if opened is None:
            return
        if opened.idle is not None:
            opened.idle.cancel()
        try:
            # The turn has ended, so a browser service that already went away changes nothing.
            with suppress(PlaywrightError):
                if opened.profile is not None:
                    state = await opened.context.storage_state()
                    self._profiles.refresh(turn[0], opened.profile, _kept(state))
                await opened.context.close()
                await opened.browser.close()
            await opened.playwright.stop()
        finally:
            self._browsers.give_back()


async def _connected(
    address: str, state: StorageState | None, profile: BrowserProfileName | None
) -> _Opened:
    """A context on the browser at *address*, opened with the profile's *state*."""
    playwright = await async_playwright().start()
    try:
        browser = await playwright.chromium.connect(address)
        # The profile holds a state Playwright gave, which goes back to it unchanged.
        context = await browser.new_context(storage_state=cast("PlaywrightState | None", state))
        opened = _Opened(playwright, browser, context, profile)
        context.on("page", lambda page: page.on("download", opened.collect))
        await context.new_page()
    except PlaywrightError as exc:
        await playwright.stop()
        raise BrowserNotOpen(f"The browser did not open: {exc.message}") from exc
    except BaseException:
        await playwright.stop()
        raise
    return opened


def _kept(state: PlaywrightState) -> StorageState:
    """*state* as a profile keeps it."""
    return _STATE.validate_python(state)


@contextmanager
def _on_the_page(opened: _Opened | None = None) -> Iterator[None]:
    """Tell the turn what failed on its page, which the hub would otherwise hide, with the
    values the vault filled into *opened* hidden."""
    try:
        yield
    except PlaywrightError as exc:
        message = exc.message if opened is None else opened.hidden(exc.message)
        raise BrowserActionFailed(message) from exc


async def _run(page: PlaywrightPage, action: BrowserPageAction) -> None:
    match action:
        case BrowserNavigate(url=url):
            if not url.lower().startswith(("http://", "https://")):
                raise BrowserActionFailed("The browser opens only http and https URLs.")
            response = await page.goto(url, timeout=_NAVIGATION_MS)
            if response is not None and (refusal := response.headers.get(REFUSED_HEADER.lower())):
                raise BrowserActionFailed(refusal)
        case BrowserScroll(ref=str(ref)):
            await (await _element(page, ref)).scroll_into_view_if_needed(timeout=_ELEMENT_MS)
        case BrowserScroll(direction=direction):
            height = page.viewport_size["height"] if page.viewport_size else 720
            await page.mouse.wheel(0, height if direction == "down" else -height)
        case BrowserWait(text=str(text), seconds=seconds):
            await page.get_by_text(text).first.wait_for(timeout=seconds * 1_000)
        case BrowserWait(seconds=seconds):
            await page.wait_for_load_state("load", timeout=seconds * 1_000)
        case BrowserClick(ref=ref):
            await _click(await _element(page, ref), ref)
        case BrowserType(ref=ref, text=text):
            textbox = await _element(page, ref)
            kind = await textbox.get_attribute("type", timeout=_ELEMENT_MS)
            if (kind or "").lower() == "password":
                raise BrowserActionFailed(
                    f"The field with ref {ref} is a password field, which only the vault fills."
                )
            await textbox.fill(text, timeout=_ELEMENT_MS)
        case BrowserSelect(ref=ref, value=value):
            await (await _element(page, ref)).select_option(value, timeout=_ELEMENT_MS)
        case BrowserPress(key=key):
            focused_password = page.locator("input[type=password i]:focus")
            if key not in _PASSWORD_KEYS and await focused_password.count():
                raise BrowserActionFailed(
                    f"The focus is on a password field, which takes no {key} key. Only the vault "
                    "fills a password."
                )
            await page.keyboard.press(key)
        case BrowserUpload(ref=ref, file=file):
            await (await _element(page, ref)).set_input_files(
                {
                    "name": file.name,
                    "mimeType": mimetypes.guess_type(file.name)[0] or "application/octet-stream",
                    "buffer": base64.b64decode(file.content),
                },
                timeout=_ELEMENT_MS,
            )


async def _click(element: Locator, ref: str) -> None:
    try:
        await element.click(timeout=_ELEMENT_MS)
    except PlaywrightTimeoutError as error:
        covered = _COVERED.findall(error.message)
        if not covered:
            raise
        raise BrowserActionFailed(
            f"The element with ref {ref} is covered by {covered[-1]}, which would take the click."
        ) from None


async def _downloaded(downloads: list[PlaywrightDownload]) -> tuple[BrowserFile, ...]:
    """The files *downloads* gave, which a browser in another process can only save, not lend.
    Over the cap together, they are refused before the hub reads one."""
    with TemporaryDirectory() as directory:
        saved = []
        for index, download in enumerate(downloads):
            path = Path(directory) / str(index)
            await download.save_as(path)
            saved.append((download.suggested_filename, path))
        if (size := sum(path.stat().st_size for _, path in saved)) > BROWSER_FILE_BYTES:
            raise BrowserActionFailed(
                f"The page downloaded {', '.join(name for name, _ in saved)}, {size:,} bytes, "
                f"over the browser's cap of {BROWSER_FILE_BYTES:,}. Nothing was saved."
            )
        return tuple(
            [
                BrowserFile(
                    name=name,
                    content=base64.b64encode(await asyncio.to_thread(path.read_bytes)).decode(),
                )
                for name, path in saved
            ]
        )


async def _screencast(page: PlaywrightPage) -> _Screencast:
    """Start the page's live view, which shows a new frame each time the page changes."""
    session = await page.context.new_cdp_session(page)
    screencast = _Screencast(page, session)

    async def framed(frame: dict[str, object]) -> None:
        screencast.show(str(frame["data"]))
        # Chromium sends the next frame once this one is acknowledged.
        with suppress(PlaywrightError):
            await session.send("Page.screencastFrameAck", {"sessionId": frame["sessionId"]})

    session.on("Page.screencastFrame", framed)
    # Frames the viewport's size keep their pixels the page's, which the user's input names.
    size = page.viewport_size or {"width": 1280, "height": 720}
    await session.send(
        "Page.startScreencast",
        {
            "format": "jpeg",
            "quality": _SCREENCAST_QUALITY,
            "maxWidth": size["width"],
            "maxHeight": size["height"],
        },
    )
    return screencast


async def _element(page: PlaywrightPage, ref: str) -> Locator:
    element = page.locator(f"aria-ref={ref}")
    if not await element.count():
        raise BrowserActionFailed(f"No element on the page has ref {ref}. Take a new snapshot.")
    return element


def _interactive(tree: str) -> str:
    """The lines of *tree* for the elements a page lets you act on, a link with its URL."""
    kept: list[str] = []
    for line, following in pairwise([*tree.splitlines(), ""]):
        role = _ROLE.match(line)
        if role is None or (role.group(1) not in _INTERACTIVE and "[cursor=pointer]" not in line):
            continue
        element = line.strip().removesuffix(":")
        if following.strip().startswith(_URL_LINE):
            element = f"{element} {following.strip().removeprefix(_URL_LINE)}"
        kept.append(element)
    return "\n".join(kept)
