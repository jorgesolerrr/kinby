"""An instance's ``skills-lock.json``: each skill it picked from the library, as the ``skills`` CLI
records a skill it installed."""

from __future__ import annotations

import hashlib
import string
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field  # noqa: TID251 - the lock's file boundary
from pydantic.alias_generators import to_camel

from kinby.contracts import FileHash, LibrarySource, SkillName
from kinby.instance.layout import SKILLS_LOCK_NAME

# The order JavaScript's localeCompare puts these characters in. The `skills` CLI sorts a
# skill's files with it before hashing them into `computedHash`.
_COLLATION = " _-,;:!?.'\"()[]{}@*/\\&#%`^+<=>|~$" + string.digits + string.ascii_lowercase

#: Where a pick's ``source`` says it came from, by the kind of library entry it was copied from.
_SOURCES = {LibrarySource.SHIPPED: "kinby", LibrarySource.WRITTEN: "hub"}


class LockedSkill(BaseModel):
    """One pick, under the field names the ``skills`` CLI gives a lock entry."""

    model_config = ConfigDict(
        alias_generator=to_camel, validate_by_name=True, serialize_by_alias=True, frozen=True
    )

    source: str
    source_type: LibrarySource
    computed_hash: FileHash

    @classmethod
    def of(cls, folder: Path, source: LibrarySource) -> LockedSkill:
        """The entry for the skill copied into *folder* from a *source* library entry."""
        return cls(source=_SOURCES[source], source_type=source, computed_hash=skill_hash(folder))


class SkillsLock(BaseModel):
    version: int = 1
    skills: dict[SkillName, LockedSkill] = Field(default_factory=dict)


def read_lock(instance_path: Path) -> SkillsLock:
    """The instance's lock, empty when it has picked nothing yet."""
    path = instance_path / SKILLS_LOCK_NAME
    if not path.is_file():
        return SkillsLock()
    return SkillsLock.model_validate_json(path.read_bytes())


def write_lock(instance_path: Path, lock: SkillsLock) -> None:
    ordered = SkillsLock(skills=dict(sorted(lock.skills.items())))
    (instance_path / SKILLS_LOCK_NAME).write_text(
        ordered.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )


def skill_hash(folder: Path) -> FileHash:
    """Hash a skill folder as the ``skills`` CLI does: each file's relative path, then its bytes."""
    files = (path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file())
    digest = hashlib.sha256()
    for file in sorted(files, key=_collation_key):
        digest.update(file.encode())
        digest.update((folder / file).read_bytes())
    return FileHash(digest.hexdigest())


def _collation_key(path: str) -> tuple[list[int], list[bool]]:
    return [_COLLATION.find(char.lower()) for char in path], [char.isupper() for char in path]
