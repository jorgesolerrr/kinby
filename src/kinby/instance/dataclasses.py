"""Typed data loaded from an instance manifest."""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class Memory:
    """Reserved memory configuration."""


@dataclass(frozen=True)
class Manifest:
    """Validated settings from ``kinby.toml``."""

    id: str
    persona_name: str | None
    state_dir: Path
    models: Models
    workspace: Workspace
    memory: Memory


@dataclass(frozen=True)
class Instance:
    path: Path
    manifest: Manifest
    matching_rule: MatchingRule
