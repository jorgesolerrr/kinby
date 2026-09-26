"""The curated list: the packages kinby offers when creating an instance.

Each entry is a `<name>.toml` file here, next to its image recipe `<name>.Dockerfile`. The entry
pins one full commit of the package's repository, so a pin moves by editing the entry, and the
hub never fetches from that repository (ADR 0061).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import ConfigDict, TypeAdapter, ValidationError
from pydantic.dataclasses import dataclass

from kinby.contracts import CuratedPackage, PackageCommit, PackageSelection

CURATED_DIRECTORY = Path(__file__).parent


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class _EntryFile:
    id: str
    display_name: str
    description: str
    icon: str
    distribution: str
    source: PackageCommit


_ENTRY_FILE = TypeAdapter(_EntryFile)


@dataclass(frozen=True)
class CuratedEntry:
    """One curated entry: the package a client sees, and the recipe only the hub reads."""

    package: CuratedPackage
    recipe: str


def curated_list(directory: Path = CURATED_DIRECTORY) -> tuple[CuratedEntry, ...]:
    """Every entry in the directory, in file name order. A malformed entry raises."""
    return tuple(_entry(path) for path in sorted(directory.glob("*.toml")))


def with_recipe(
    package: PackageSelection | None,
    entries: tuple[CuratedEntry, ...],
) -> PackageSelection | None:
    """The selection with the recipe of the entry that offers it. Any other stays as it is."""
    for entry in entries:
        if package is not None and package == entry.package.selection:
            return package.model_copy(update={"image_recipe": entry.recipe})
    return package


def _entry(path: Path) -> CuratedEntry:
    try:
        declared = _ENTRY_FILE.validate_python(tomllib.loads(path.read_text(encoding="utf-8")))
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ValueError(f"Curated entry {path.name} is malformed: {exc}") from exc
    return CuratedEntry(
        package=CuratedPackage(
            id=declared.id,
            display_name=declared.display_name,
            description=declared.description,
            icon=declared.icon,
            selection=PackageSelection(
                id=declared.id,
                distribution=declared.distribution,
                version=declared.source,
            ),
        ),
        recipe=path.with_suffix(".Dockerfile").read_text(encoding="utf-8"),
    )


__all__ = ["CURATED_DIRECTORY", "CuratedEntry", "curated_list", "with_recipe"]
