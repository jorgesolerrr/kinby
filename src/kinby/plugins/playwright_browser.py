"""The browser service through Playwright: each turn's context lives in the hub's browser
service, which the hub's browser route connects it to (ADR 0113)."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from urllib.parse import urljoin
from uuid import UUID

from playwright.async_api import Browser, BrowserContext, Locator, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page as PlaywrightPage

from kinby.contracts import BROWSER_OPEN, BrowserOpenCommand, BrowserOpenResult
from kinby.plugins.browser import BrowserAction, Navigate, Page, PageRead, Scroll, Wait
from kinby.plugins.defaults.web import REFUSED_HEADER, RefusedUrl, capped
from kinby.plugins.hub import call_hub

#: Asks for a browser for one turn: where it connects, and the token it connects with.
Lease = Callable[[UUID], Awaitable[BrowserOpenResult]]

_NAVIGATION_MS = 30_000
_ELEMENT_MS = 5_000
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


async def hub_lease(turn: UUID) -> BrowserOpenResult:
    """Ask the hub for a browser for *turn*, on this instance's hub route."""
    command = BrowserOpenCommand(turn_id=turn)
    result = await call_hub(BROWSER_OPEN.name, command.model_dump(mode="json"), "the browser")
    return BrowserOpenResult.model_validate(result)


@dataclass(frozen=True)
class _Opened:
    playwright: Playwright
    browser: Browser
    context: BrowserContext


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
            context = await browser.new_context()
            await context.new_page()
        except BaseException:
            await playwright.stop()
            raise
        self._opened[turn] = _Opened(playwright, browser, context)

    async def run(self, turn: UUID, action: BrowserAction) -> Page:
        page = await self._page(turn)
        match action:
            case Navigate(url=url):
                response = await page.goto(url, timeout=_NAVIGATION_MS)
                if response is not None and (
                    refusal := response.headers.get(REFUSED_HEADER.lower())
                ):
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
        return Page(url=page.url, title=await page.title())

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
