"""The browser tools: open a page on the hub's browser service and read it (ADR 0113)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from kinby.instance import Instance
from kinby.plugins.browser import BrowserService, Navigate, Scroll, Wait
from kinby.plugins.defaults.web import PAGE_CHARACTERS, capped, guard_url, untrusted
from kinby.plugins.tools import Tool, ToolContext, tool

#: How much of the page a snapshot returns unless the model asks for another amount.
SNAPSHOT_CHARACTERS = 20_000
#: Where browser_screenshot saves its images, under the workspace.
SCREENSHOTS_DIR = "screenshots"


def browser_tools(instance: Instance) -> tuple[Tool, ...]:
    """The browser tools, in an instance whose manifest turns the browser on."""
    if not instance.manifest.browser:
        return ()
    return (
        browser_open,
        browser_snapshot,
        browser_text,
        browser_screenshot,
        browser_scroll,
        browser_wait,
    )


@tool(write=False)
async def browser_open(url: str, context: ToolContext) -> str:
    """Open `url` in the browser, which runs the page's JavaScript. Opening a URL that is not in
    the conversation yet needs the user's approval. Then read the page with browser_snapshot."""
    await guard_url(url)
    browser, turn = await _opened(context)
    page = await browser.run(turn, Navigate(url))
    return untrusted(f"Opened {page.url}\nTitle: {page.title}", page.url)


@tool(write=False)
async def browser_snapshot(
    context: ToolContext,
    *,
    interactive: bool = True,
    max_chars: Annotated[int, Field(ge=1_000, le=PAGE_CHARACTERS)] = SNAPSHOT_CHARACTERS,
) -> str:
    """The open page's accessibility tree, each element with a ref like e12 that the other
    browser tools take. `interactive` keeps only the elements you can act on, such as links,
    buttons and fields; turn it off to read the page's whole structure."""
    browser, turn = await _opened(context)
    read = await browser.snapshot(turn, interactive=interactive, max_chars=max_chars)
    return untrusted(read.text, read.url)


@tool(write=False)
async def browser_text(context: ToolContext, ref: str | None = None) -> str:
    """The text of the element with `ref` from the last snapshot, or of the whole page."""
    browser, turn = await _opened(context)
    read = await browser.text(turn, ref)
    return untrusted(capped(read.text, PAGE_CHARACTERS), read.url)


@tool(write=False)
async def browser_screenshot(context: ToolContext) -> str:
    """Save a screenshot of the open page into the workspace, and return its path."""
    browser, turn = await _opened(context)
    image = await browser.screenshot(turn)
    name = f"{SCREENSHOTS_DIR}/{datetime.now(UTC):%Y%m%dT%H%M%S%f}.png"
    path = context.workspace / name
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_bytes, image)
    return f"Saved a screenshot of the page to {name}."


@tool(write=False)
async def browser_scroll(
    context: ToolContext,
    direction: Literal["up", "down"] = "down",
    ref: str | None = None,
) -> str:
    """Scroll the open page a screen up or down, or bring the element with `ref` into view."""
    browser, turn = await _opened(context)
    page = await browser.run(turn, Scroll(direction, ref))
    return f"Scrolled {page.url}."


@tool(write=False)
async def browser_wait(
    context: ToolContext,
    text: str | None = None,
    seconds: Annotated[float, Field(gt=0, le=60)] = 10,
) -> str:
    """Wait up to `seconds` for `text` to appear on the open page, or without it for the page
    to finish loading."""
    browser, turn = await _opened(context)
    page = await browser.run(turn, Wait(text, seconds))
    return f"{page.url} shows the text." if text else f"{page.url} finished loading."


async def _opened(context: ToolContext) -> tuple[BrowserService, UUID]:
    """The turn's browser, opened on its first call."""
    if context.browser is None or context.turn_id is None:
        raise ValueError("The browser opens only in a turn of a browser-on instance.")
    await context.browser.open(context.turn_id)
    return context.browser, context.turn_id
