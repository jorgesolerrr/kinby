"""Typed data loaded from an instance manifest."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from kinby.contracts import FeedbackPolicy, RecapPolicy

MatchingRule = Literal[
    "explicit directory",
    "KINBY_INSTANCE",
    "walk-up",
    "home default",
]


@dataclass(frozen=True)
class Models:
    """Models selected for one instance."""

    main: str
    recap: str
    embed: str | None


@dataclass(frozen=True)
class ModelPrice:
    """Prices per million tokens for one model."""

    input: float
    output: float
    cache_read: float | None = None
    cache_write: float | None = None


@dataclass(frozen=True)
class Conventions:
    """Workspace instruction files and skill directories that exist."""

    instructions: tuple[Path, ...]
    skills: tuple[Path, ...]


@dataclass(frozen=True)
class Workspace:
    """The workspace configured for one instance."""

    path: Path
    source: str | None
    conventions: Conventions
    snapshots: bool = True


@dataclass(frozen=True)
class Memory:
    """Memory behavior selected for one instance."""

    recap: RecapPolicy


@dataclass(frozen=True)
class Feedback:
    """Turn-rating prompt behavior selected for one instance."""

    ask: FeedbackPolicy


class SearchBackend(StrEnum):
    """The one service an instance sends its web searches to."""

    BRAVE = "brave"
    TAVILY = "tavily"
    EXA = "exa"
    SEARXNG = "searxng"

    @property
    def key_variable(self) -> str:
        """Where the instance holds the backend's key. SearXNG runs on the user's own server,
        so its variable holds that server's URL instead."""
        if self is SearchBackend.SEARXNG:
            return "SEARXNG_URL"
        return f"{self.upper()}_API_KEY"


@dataclass(frozen=True)
class Tools:
    """How the manifest configures kinby's tools."""

    defaults: bool = True
    #: Default tools switched off by name, while the others stay on.
    off: frozenset[str] = frozenset()
    search: SearchBackend = SearchBackend.BRAVE
    bash_timeout_seconds: int = 120

    def offers(self, name: str) -> bool:
        """Whether the manifest leaves the default tool *name* on."""
        return self.defaults and name not in self.off

    @property
    def search_key(self) -> str | None:
        """The variable holding the search backend's key, while web search is on."""
        return self.search.key_variable if self.offers("web_search") else None


@dataclass(frozen=True)
class Budgets:
    """Limits configured for turns and instance spending."""

    steps: int | None = None
    tokens: int | None = None
    seconds: float | None = None
    usd_per_day: float | None = None


@dataclass(frozen=True)
class Routines:
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo("UTC"))


@dataclass(frozen=True)
class Serve:
    host: str
    port: int


@dataclass(frozen=True)
class Manifest:
    """Validated settings from ``kinby.toml``."""

    id: str
    persona_name: str | None
    state_dir: Path
    models: Models
    workspace: Workspace
    memory: Memory
    feedback: Feedback
    tools: Tools
    budgets: Budgets
    prices: Mapping[str, ModelPrice]
    routines: Routines = field(default_factory=Routines)
    serve: Serve | None = None


@dataclass(frozen=True)
class Instance:
    path: Path
    manifest: Manifest
    matching_rule: MatchingRule
    routine_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        repr=False,
        compare=False,
    )
    #: Held while the agent or a client writes or deletes an instance skill.
    skill_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        repr=False,
        compare=False,
    )
    #: Held while a client writes the prompts, permissions.toml, or kinby.toml.
    config_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        repr=False,
        compare=False,
    )
