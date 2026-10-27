"""The browser tools: open a page on the hub's browser service, read it and act on it
(ADR 0113)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from kinby.instance import Instance
from kinby.plugins.browser import (
    BrowserAction,
    BrowserService,
    Click,
    Download,
    Navigate,
    Page,
    Press,
    Scroll,
    Select,
    Type,
    Upload,
    Wait,
)
from kinby.plugins.defaults.files import workspace_path
from kinby.plugins.defaults.web import PAGE_CHARACTERS, capped, guard_url, untrusted
from kinby.plugins.tools import Tool, ToolContext, tool

#: How much of the page a snapshot returns unless the model asks for another amount.
SNAPSHOT_CHARACTERS = 20_000
#: Where browser_screenshot saves its images, under the workspace.
SCREENSHOTS_DIR = "screenshots"
#: Where the page's downloads land, under the workspace.
DOWNLOADS_DIR = "downloads"


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
        browser_click,
        browser_type,
        browser_select,
        browser_press,
        browser_upload,
    )


@tool(write=False)
async def browser_open(url: str, context: ToolContext) -> str:
    """Open `url` in the browser, which runs the page's JavaScript. Opening a URL that is not in
    the conversation yet needs the user's approval. Then read the page with browser_snapshot."""
    await guard_url(url)
    page, downloaded = await _run(context, Navigate(url))
    return untrusted(f"Opened {page.url}\nTitle: {page.title}", page.url) + downloaded


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
    page, downloaded = await _run(context, Scroll(direction, ref))
    return f"Scrolled {page.url}.{downloaded}"


@tool(write=False)
async def browser_wait(
    context: ToolContext,
    text: str | None = None,
    seconds: Annotated[float, Field(gt=0, le=60)] = 10,
) -> str:
    """Wait up to `seconds` for `text` to appear on the open page, or without it for the page
    to finish loading."""
    page, downloaded = await _run(context, Wait(text, seconds))
    done = f"{page.url} shows the text." if text else f"{page.url} finished loading."
    return done + downloaded


@tool(write=True)
async def browser_click(ref: str, context: ToolContext) -> str:
    """Click the element with `ref` from the last snapshot. A file the click downloads lands in
    the workspace's downloads/, with the next browser action when it is slow to begin."""
    return await _act(context, Click(ref), f"Clicked {ref}")


@tool(write=True)
async def browser_type(ref: str, text: str, context: ToolContext) -> str:
    """Type `text` into the field with `ref`, in place of what it holds. A password field
    refuses it."""
    return await _act(context, Type(ref, text), f"Typed into {ref}")


@tool(write=True)
async def browser_select(ref: str, value: str, context: ToolContext) -> str:
    """Select the option with `value` as its value or its label, in the list with `ref`."""
    return await _act(context, Select(ref, value), f"Selected {value} in {ref}")


@tool(write=True)
async def browser_press(key: str, context: ToolContext) -> str:
    """Press `key`, such as Enter, Tab or Control+A, on the element that has the focus."""
    return await _act(context, Press(key), f"Pressed {key}")


@tool(write=True)
async def browser_upload(ref: str, path: str, context: ToolContext) -> str:
    """Attach the workspace file at `path` to the file field with `ref`."""
    file = workspace_path(context.workspace, path)
    return await _act(context, Upload(ref, file), f"Attached {path} to {ref}")


async def _act(context: ToolContext, action: BrowserAction, done: str) -> str:
    page, downloaded = await _run(context, action)
    return f"{done} on {page.url}.{downloaded}"


async def _run(context: ToolContext, action: BrowserAction) -> tuple[Page, str]:
    """Run *action*, and save what the page downloaded since the last action into the
    workspace, with a line that names each file."""
    browser, turn = await _opened(context)
    page = await browser.run(turn, action)
    saved = [await _save(context.workspace, download) for download in page.downloads]
    return page, "".join(f"\nDownloaded {name}." for name in saved)


async def _save(workspace: Path, download: Download) -> str:
    """Save *download* into the workspace's downloads/, under the last part of its name."""
    name = f"{DOWNLOADS_DIR}/{Path(download.name).name}"
    path = workspace_path(workspace, name)
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_bytes, download.content)
    return name


async def _opened(context: ToolContext) -> tuple[BrowserService, UUID]:
    """The turn's browser, opened on its first call."""
    if context.browser is None or context.turn_id is None:
        raise ValueError("The browser opens only in a turn of a browser-on instance.")
    await context.browser.open(context.turn_id)
    return context.browser, context.turn_id
