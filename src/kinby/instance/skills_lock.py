"""An instance's ``skills-lock.json``: each skill it picked from the library, as the ``skills`` CLI
records a skill it installed, and each MCP server it picked."""

from __future__ import annotations

import hashlib
import string
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,  # noqa: TID251 - the lock's file boundary
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
)
from pydantic.alias_generators import to_camel

from kinby.contracts import (
    FileHash,
    GitCommit,
    GitSource,
    LibrarySource,
    McpHttpEntry,
    McpServer,
    McpServerName,
    McpStdioEntry,
    SkillName,
    ToolState,
)
from kinby.instance.config_changes import file_hash
from kinby.instance.dataclasses import ToolStates
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


class _UrlTransport(BaseModel):
    type: Literal["url"]
    url: str
    headers: dict[str, str] = Field(default_factory=dict)


class _UvxTransport(BaseModel):
    type: Literal["uvx"]
    package: str
    args: list[str] = Field(default_factory=list)


class _OldMcpServer(BaseModel):
    """An MCP server's definition as the hub stored it before ADR 0127: each header and env key
    named a variable of the instance's ``.env``."""

    transport: _UrlTransport | _UvxTransport = Field(discriminator="type")
    env: list[str] = Field(default_factory=list)
    include: list[str] = Field(default_factory=list)

    def converted(self) -> dict[str, object]:
        transport = self.transport
        entry = (
            McpHttpEntry(
                url=transport.url,
                headers={name: f"${{{variable}}}" for name, variable in transport.headers.items()},
            )
            if isinstance(transport, _UrlTransport)
            else McpStdioEntry(
                command="uvx",
                args=[transport.package, *transport.args],
                env={name: f"${{{name}}}" for name in self.env},
            )
        )
        return {"entry": entry, "include": self.include}


#: The tools a turn was offered, by name, before tool states replaced the list (ADR 0129).
_INCLUDE = TypeAdapter(list[str])


def _converted(stored: object) -> object:
    if isinstance(stored, dict) and "transport" in stored:
        stored = _OldMcpServer.model_validate(stored).converted()
    if isinstance(stored, dict) and "include" in stored:
        kept = {key: value for key, value in stored.items() if key != "include"}
        include = _INCLUDE.validate_python(stored["include"])
        return {
            **kept,
            "default_state": ToolState.OFF,
            "tool_states": dict.fromkeys(include, ToolState.LOADED),
        }
    return stored


#: An MCP server's definition as the hub stores it, in the library or a lock, converted when it
#: is still in the shape before ADR 0127 or holds an include list.
type StoredMcpServer = Annotated[McpServer, BeforeValidator(_converted)]


class LockedMcpServer(BaseModel):
    """One MCP server pick: the library's definition as copied, whether the user trusts its
    read-only hints, whether the server asked the hub for a sign-in, and the tool states the
    instance changed from the definition's."""

    model_config = ConfigDict(
        alias_generator=to_camel, validate_by_name=True, serialize_by_alias=True, frozen=True
    )

    server: StoredMcpServer
    trust_hints: bool = False
    needs_sign_in: bool = False
    #: The server's switch. Off, every tool of the server is off, one with its own state too.
    on: bool = Field(default=True, exclude_if=lambda on: on)
    default_state: ToolState | None = None
    tool_states: dict[str, ToolState] = Field(
        default_factory=dict, exclude_if=lambda states: not states
    )

    def states(self) -> ToolStates:
        """The states the server's tools take: the pick's, then the library's."""
        if not self.on:
            return ToolStates(default=ToolState.OFF)
        return ToolStates(
            default=self.default_state or self.server.default_state,
            tools=self.server.tool_states | self.tool_states,
        )


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
    (instance_path / SKILLS_LOCK_NAME).write_text(lock_text(lock), encoding="utf-8")


def lock_text(lock: SkillsLock) -> str:
    """The lock as its file holds it."""
    ordered = SkillsLock(
        skills=dict(sorted(lock.skills.items())),
        mcp_servers=dict(sorted(lock.mcp_servers.items())),
    )
    # A lock without MCP servers stays the file the ``skills`` CLI writes.
    excluded = None if lock.mcp_servers else {"mcp_servers"}
    return ordered.model_dump_json(indent=2, exclude_none=True, exclude=excluded) + "\n"


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
