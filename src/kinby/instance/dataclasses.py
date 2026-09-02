"""Typed data loaded from an instance manifest."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

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


class RecapPolicy(StrEnum):
    """When kinby writes a model-assisted recap."""

    EVERY_TURN = "every-turn"
    TRACE_ONLY = "off"


@dataclass(frozen=True)
class Memory:
    """Memory behavior selected for one instance."""

    recap: RecapPolicy


@dataclass(frozen=True)
class Tools:
    """Whether the manifest enables kinby's default tools."""

    defaults: bool = True


@dataclass(frozen=True)
class Manifest:
    """Validated settings from ``kinby.toml``."""

    id: str
    persona_name: str | None
    state_dir: Path
    models: Models
    workspace: Workspace
    memory: Memory
    tools: Tools


@dataclass(frozen=True)
class Instance:
    path: Path
    manifest: Manifest
    matching_rule: MatchingRule
