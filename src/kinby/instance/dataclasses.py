"""Typed data loaded from an instance manifest."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Models:
    """Models selected for one instance."""

    main: str
    recap: str
    embed: str | None


@dataclass(frozen=True)
class Workspace:
    """The workspace configured for one instance."""

    path: Path
    source: str | None


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
    """A resolved instance directory and its validated manifest."""

    path: Path
    manifest: Manifest
    resolved_by: str = "explicit directory"
