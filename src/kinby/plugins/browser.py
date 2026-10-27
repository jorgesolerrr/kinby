"""The browser service as an instance's turns reach it (ADR 0113)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from kinby.contracts import TakeoverInput


@dataclass(frozen=True)
class Navigate:
    url: str


@dataclass(frozen=True)
class Scroll:
    direction: Literal["up", "down"]
    #: The element to scroll into view instead, by its ref in a snapshot.
    ref: str | None = None


@dataclass(frozen=True)
class Wait:
    #: The text to wait for. Without it, wait for the page to finish loading.
    text: str | None
    seconds: float


@dataclass(frozen=True)
class Click:
    ref: str


@dataclass(frozen=True)
class Type:
    """Type *text* into a field, in place of what it holds."""

    ref: str
    text: str


@dataclass(frozen=True)
class Select:
    """Select an option of a list, by its value or its label."""

    ref: str
    value: str


@dataclass(frozen=True)
class Press:
    """Press a key on the element that has the focus."""

    key: str


@dataclass(frozen=True)
class Upload:
    """Attach the file at *path* to a file field."""

    ref: str
    path: Path


type BrowserAction = Navigate | Scroll | Wait | Click | Type | Select | Press | Upload


@dataclass(frozen=True)
class Download:
    """A file the page downloaded, under the name the page gave it."""

    name: str
    content: bytes


@dataclass(frozen=True)
class Page:
    """Where the turn's page is after an action, and what the action downloaded."""

    url: str
    title: str
    downloads: tuple[Download, ...] = ()


@dataclass(frozen=True)
class PageRead:
    """What the browser read from the page at *url*."""

    url: str
    text: str


# A tool's context is validated, so the browser in it is checked by its methods.
@runtime_checkable
class BrowserService(Protocol):
    """A fresh browser context for each turn, which shares nothing with another turn."""

    async def open(self, turn: UUID) -> None:
        """Open the turn's context, unless it has one already."""

    async def run(self, turn: UUID, action: BrowserAction) -> Page:
        """Run one action on the turn's page."""

    async def snapshot(self, turn: UUID, *, interactive: bool, max_chars: int) -> PageRead:
        """The page's accessibility tree with a ref on each element, cut at *max_chars*.
        *interactive* keeps only the elements the page lets you act on."""

    async def text(self, turn: UUID, ref: str | None) -> PageRead:
        """The text of the element with *ref*, or of the whole page."""

    async def screenshot(self, turn: UUID) -> bytes:
        """The page's viewport as a PNG."""

    def screencast(self, turn: UUID) -> AsyncGenerator[bytes]:
        """The page's viewport as JPEG frames, a new one each time it changes, for as long as the
        caller reads them."""

    async def send(self, turn: UUID, user_input: TakeoverInput) -> None:
        """Pass the user's mouse or keyboard to the turn's page, during a takeover."""

    async def close(self, turn: UUID) -> None:
        """Close the turn's context, if it opened one."""
