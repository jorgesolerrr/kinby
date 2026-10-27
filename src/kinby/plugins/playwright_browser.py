"""The browser service through Playwright: each turn's context lives in the hub's browser
service, which the hub's browser route connects it to (ADR 0113)."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urljoin
from uuid import UUID

from playwright.async_api import Browser, BrowserContext, Locator, Playwright, async_playwright
from playwright.async_api import Download as PlaywrightDownload
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page as PlaywrightPage
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from kinby.contracts import BROWSER_OPEN, BrowserOpenCommand, BrowserOpenResult
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
from kinby.plugins.defaults.web import REFUSED_HEADER, RefusedUrl, capped
from kinby.plugins.hub import call_hub

#: Asks for a browser for one turn: where it connects, and the token it connects with.
Lease = Callable[[UUID], Awaitable[BrowserOpenResult]]

_NAVIGATION_MS = 30_000
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


async def hub_lease(turn: UUID) -> BrowserOpenResult:
    """Ask the hub for a browser for *turn*, on this instance's hub route."""
    command = BrowserOpenCommand(turn_id=turn)
    result = await call_hub(BROWSER_OPEN.name, command.model_dump(mode="json"), "the browser")
    return BrowserOpenResult.model_validate(result)


@dataclass
class _Opened:
    playwright: Playwright
    browser: Browser
    context: BrowserContext
    #: The downloads begun since the last action returned, which the next action returns.
    downloads: list[PlaywrightDownload] = field(default_factory=list)
    begun: asyncio.Event = field(default_factory=asyncio.Event)

    def collect(self, download: PlaywrightDownload) -> None:
        self.downloads.append(download)
        self.begun.set()


class PlaywrightBrowser:
    """A BrowserService whose contexts live in the browser service *lease* connects each turn to."""

    def __init__(self, lease: Lease = hub_lease) -> None:
        self._lease = lease
        self._opened: dict[UUID, _Opened] = {}

    async def open(self, turn: UUID) -> None:
        if turn in self._opened:
            return
        lease = await self._lease(turn)
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.connect(
                lease.address, headers={"Authorization": f"Bearer {lease.token}"}
            )
            opened = _Opened(playwright, browser, await browser.new_context())
            opened.context.on("page", lambda page: page.on("download", opened.collect))
            await opened.context.new_page()
        except BaseException:
            await playwright.stop()
            raise
        self._opened[turn] = opened

    async def run(self, turn: UUID, action: BrowserAction) -> Page:
        opened, page = self._opened[turn], await self._page(turn)
        await _run(page, action)
        # A download begins when its server answers, which can be after the action returns.
        if isinstance(action, Click | Press):
            with suppress(TimeoutError):
                await asyncio.wait_for(opened.begun.wait(), _DOWNLOAD_SECONDS)
        downloads = [*opened.downloads]
        opened.downloads.clear()
        opened.begun.clear()
        return Page(
            url=page.url,
            title=await page.title(),
            downloads=tuple([await _downloaded(download) for download in downloads]),
        )

    async def snapshot(self, turn: UUID, *, interactive: bool, max_chars: int) -> PageRead:
        page = await self._page(turn)
        tree = _HREF.sub(
            lambda href: f"{href.group(1)}{urljoin(page.url, href.group(2))}",
            await page.aria_snapshot(mode="ai"),
        )
        if interactive:
            tree = _interactive(tree)
        return PageRead(url=page.url, text=capped(tree, max_chars))

    async def text(self, turn: UUID, ref: str | None) -> PageRead:
        page = await self._page(turn)
        element = await _element(page, ref) if ref else page.locator("body")
        return PageRead(url=page.url, text=await element.inner_text(timeout=_ELEMENT_MS))

    async def screenshot(self, turn: UUID) -> bytes:
        return await (await self._page(turn)).screenshot(type="png")

    async def close(self, turn: UUID) -> None:
        opened = self._opened.pop(turn, None)
        if opened is None:
            return
        # The turn has ended, so a browser service that already went away changes nothing.
        with suppress(PlaywrightError):
            await opened.browser.close()
        await opened.playwright.stop()

    async def _page(self, turn: UUID) -> PlaywrightPage:
        """The turn's newest page, which a link that opens a new window makes."""
        context = self._opened[turn].context
        return context.pages[-1] if context.pages else await context.new_page()


async def _run(page: PlaywrightPage, action: BrowserAction) -> None:
    match action:
        case Navigate(url=url):
            response = await page.goto(url, timeout=_NAVIGATION_MS)
            if response is not None and (refusal := response.headers.get(REFUSED_HEADER.lower())):
                raise RefusedUrl(refusal)
        case Scroll(ref=str(ref)):
            await (await _element(page, ref)).scroll_into_view_if_needed(timeout=_ELEMENT_MS)
        case Scroll(direction=direction):
            height = page.viewport_size["height"] if page.viewport_size else 720
            await page.mouse.wheel(0, height if direction == "down" else -height)
        case Wait(text=str(text), seconds=seconds):
            await page.get_by_text(text).first.wait_for(timeout=seconds * 1_000)
        case Wait(seconds=seconds):
            await page.wait_for_load_state("load", timeout=seconds * 1_000)
        case Click(ref=ref):
            await _click(await _element(page, ref), ref)
        case Type(ref=ref, text=text):
            textbox = await _element(page, ref)
            if await textbox.evaluate("field => field.type === 'password'"):
                raise PermissionError(
                    f"The field with ref {ref} is a password field, which only the vault fills."
                )
            await textbox.fill(text, timeout=_ELEMENT_MS)
        case Select(ref=ref, value=value):
            await (await _element(page, ref)).select_option(value, timeout=_ELEMENT_MS)
        case Press(key=key):
            if key not in _PASSWORD_KEYS and await page.evaluate(
                "() => document.activeElement?.type === 'password'"
            ):
                raise PermissionError(
                    f"The focus is on a password field, which takes no {key} key. Only the vault "
                    "fills a password."
                )
            await page.keyboard.press(key)
        case Upload(ref=ref, path=path):
            await (await _element(page, ref)).set_input_files(path, timeout=_ELEMENT_MS)


async def _click(element: Locator, ref: str) -> None:
    try:
        await element.click(timeout=_ELEMENT_MS)
    except PlaywrightTimeoutError as error:
        covered = _COVERED.findall(error.message)
        if not covered:
            raise
        raise ValueError(
            f"The element with ref {ref} is covered by {covered[-1]}, which would take the click."
        ) from None


async def _downloaded(download: PlaywrightDownload) -> Download:
    """The file *download* gave, which a browser in another process can only save, not lend."""
    with TemporaryDirectory() as directory:
        path = Path(directory) / "download"
        await download.save_as(path)
        content = await asyncio.to_thread(path.read_bytes)
    return Download(download.suggested_filename, content)


async def _element(page: PlaywrightPage, ref: str) -> Locator:
    element = page.locator(f"aria-ref={ref}")
    if not await element.count():
        raise LookupError(f"No element on the page has ref {ref}. Take a new snapshot.")
    return element


def _interactive(tree: str) -> str:
    """The lines of *tree* for the elements a page lets you act on, a link with its URL."""
    lines = tree.splitlines()
    kept: list[str] = []
    for line, following in zip(lines, [*lines[1:], ""], strict=True):
        role = _ROLE.match(line)
        if role is None or (role.group(1) not in _INTERACTIVE and "[cursor=pointer]" not in line):
            continue
        element = line.strip().removesuffix(":")
        if following.strip().startswith(_URL_LINE):
            element = f"{element} {following.strip().removeprefix(_URL_LINE)}"
        kept.append(element)
    return "\n".join(kept)
