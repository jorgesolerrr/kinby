"""An instance's ``skills-lock.json``: each skill it picked from the library, as the ``skills`` CLI
records a skill it installed, and each MCP server it picked."""

from __future__ import annotations

import hashlib
import string
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field  # noqa: TID251 - the lock's file boundary
from pydantic.alias_generators import to_camel

from kinby.contracts import (
    FileHash,
    GitCommit,
    GitSource,
    LibrarySource,
    McpServer,
    McpServerName,
    SkillName,
)
from kinby.instance.config_changes import file_hash
from kinby.instance.layout import SKILLS_LOCK_NAME

# The order JavaScript's localeCompare puts these characters in. The `skills` CLI sorts a
# skill's files with it before hashing them into `computedHash`. A character outside it sorts
# after them by code point, where the root collation puts the CJK ideographs too.
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
    #: For a pick from git, the branch or tag, the skill's folder in the repository, and the commit.
    ref: str | None = None
    skill_path: str | None = None
    commit: GitCommit | None = None
    computed_hash: FileHash

    @classmethod
    def of(cls, folder: Path, source: LibrarySource | GitSource) -> LockedSkill:
        """The entry for the skill copied into *folder* from a library entry that came from
        *source*."""
        if isinstance(source, GitSource):
            return cls(
                source=source.url,
                source_type=LibrarySource.GIT,
                ref=source.ref,
                skill_path=source.path,
                commit=source.commit,
                computed_hash=skill_hash(folder),
            )
        return cls(source=_SOURCES[source], source_type=source, computed_hash=skill_hash(folder))


class LockedMcpServer(BaseModel):
    """One MCP server pick: the library's definition as copied, and whether the user trusts its
    read-only hints."""

    model_config = ConfigDict(
        alias_generator=to_camel, validate_by_name=True, serialize_by_alias=True, frozen=True
    )

    server: McpServer
    trust_hints: bool = False


class SkillsLock(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, validate_by_name=True, serialize_by_alias=True
    )

    version: int = 1
    skills: dict[SkillName, LockedSkill] = Field(default_factory=dict)
    mcp_servers: dict[McpServerName, LockedMcpServer] = Field(default_factory=dict)


def read_lock(instance_path: Path) -> SkillsLock:
    """The instance's lock, empty when it has picked nothing yet."""
    path = instance_path / SKILLS_LOCK_NAME
    if not path.is_file():
        return SkillsLock()
    return SkillsLock.model_validate_json(path.read_bytes())


def lock_hash(instance_path: Path) -> FileHash | None:
    """The hash of the instance's lock as a client reads it, or None when it has none."""
    path = instance_path / SKILLS_LOCK_NAME
    return file_hash(path.read_bytes()) if path.is_file() else None


def write_lock(instance_path: Path, lock: SkillsLock) -> None:
    ordered = SkillsLock(
        skills=dict(sorted(lock.skills.items())),
        mcp_servers=dict(sorted(lock.mcp_servers.items())),
    )
    # A lock without MCP servers stays the file the ``skills`` CLI writes.
    excluded = None if lock.mcp_servers else {"mcp_servers"}
    (instance_path / SKILLS_LOCK_NAME).write_text(
        ordered.model_dump_json(indent=2, exclude_none=True, exclude=excluded) + "\n",
        encoding="utf-8",
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
    return [_collation_rank(char) for char in path.lower()], [char.isupper() for char in path]


def _collation_rank(char: str) -> int:
    rank = _COLLATION.find(char)
    return rank if rank >= 0 else len(_COLLATION) + ord(char)
