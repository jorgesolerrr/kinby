"""The browser service as an instance's turns reach it (ADR 0113)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from kinby.contracts import (
    BrowserProfile,
    BrowserProfileListResult,
    BrowserProfileName,
    TakeoverInput,
    VaultEntryListResult,
    VaultEntryName,
    VaultField,
)

#: What a vault fill types, which the hub's browser route replaces with the value. Its NUL bytes
#: keep it apart from any text a page or the model types.
VAULT_PLACEHOLDER = "\0kinby-vault\0"


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


@dataclass(frozen=True)
class FillCredential:
    """Fill a field with the *field* of the vault entry *name*. The hub's browser route puts the
    value in place of the placeholder, on the entry's site only, so the turn never holds it."""

    ref: str
    name: VaultEntryName
    field: VaultField

    @property
    def placeholder(self) -> str:
        return f"{VAULT_PLACEHOLDER}{self.field.value}\0{self.name}"


type BrowserAction = (
    Navigate | Scroll | Wait | Click | Type | Select | Press | Upload | FillCredential
)


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

    async def open(self, turn: UUID, profile: BrowserProfileName | None = None) -> None:
        """Open the turn's context, unless it has one already, signed in with the instance's
        *profile*. A context opened without the profile refuses it."""

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

    async def profiles(self) -> BrowserProfileListResult:
        """The instance's profiles, by name and site."""

    async def vault(self) -> VaultEntryListResult:
        """The instance's vault entries, by name and site."""

    async def save_profile(self, turn: UUID, name: BrowserProfileName) -> BrowserProfile:
        """Save the turn's context as the instance's profile *name*, for its site. The context's
        state is saved back to it when the context closes."""

    async def close(self, turn: UUID) -> None:
        """Close the turn's context, if it opened one, saving its state back to its profile."""
