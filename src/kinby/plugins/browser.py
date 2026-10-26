"""The browser service as an instance's turns reach it (ADR 0113)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID


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


type BrowserAction = Navigate | Scroll | Wait


@dataclass(frozen=True)
class Page:
    """Where the turn's page is after an action."""

    url: str
    title: str


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

    async def close(self, turn: UUID) -> None:
        """Close the turn's context, if it opened one."""
