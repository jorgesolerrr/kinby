"""Read knowledge graph nodes from an instance directory."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated
from uuid import UUID

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError
from pydantic.dataclasses import dataclass

from kinby.contracts import NodeId
from kinby.frontmatter import (
    FrontmatterError,
    parse_frontmatter,
    render_frontmatter_value,
)
from kinby.instance.layout import GRAPH_DIR, MEMORY_DIR
from kinby.memory.facade import Episode, Fact, MemoryHit, MemoryNode


class MemoryNodeError(ValueError):
    """A graph node has missing or invalid frontmatter."""


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class _NodeFrontmatter:
    date: date
    thread: UUID
    description: Annotated[str, Field(min_length=1)]
    subjects: tuple[str, ...]


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class _FactFrontmatter(_NodeFrontmatter):
    tombstone: bool = False


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class _EpisodeFrontmatter(_NodeFrontmatter):
    turn: UUID
    tools: tuple[str, ...]
    tombstone: bool = False


type _Frontmatter = _FactFrontmatter | _EpisodeFrontmatter
_FRONTMATTER = TypeAdapter(_Frontmatter)


class GraphStore:
    """The markdown knowledge graph feed for one instance."""

    def __init__(self, instance_path: Path) -> None:
        self._path = instance_path / MEMORY_DIR / GRAPH_DIR

    def recall(
        self,
        query: str,
        *,
        after: date | None = None,
        before: date | None = None,
    ) -> tuple[MemoryHit, ...]:
        """Find matching graph nodes within inclusive date bounds."""
        if not self._path.is_dir():
            return ()
        matches: list[MemoryHit] = []
        terms = query.casefold().split()
        for path in self._path.glob("*.md"):
            memory = _read_node(path)
            if memory is None:
                continue
            if after is not None and memory.date < after:
                continue
            if before is not None and memory.date > before:
                continue
            searchable = " ".join((memory.description, *memory.subjects)).casefold()
            if not all(term in searchable for term in terms):
                continue
            matches.append(MemoryHit(memory.node, memory.date, memory.description))
        return tuple(sorted(matches, key=lambda hit: (hit.date, hit.node), reverse=True)[:20])

    def open(self, node: NodeId) -> MemoryNode:
        memory = _read_node(self._node_path(node))
        if memory is None:
            raise MemoryNodeError(f'Graph node "{node}" was forgotten.')
        return memory

    def remember(self, memory: MemoryNode) -> NodeId:
        self._path.mkdir(parents=True, exist_ok=True)
        self._node_path(memory.node).write_text(
            _render_node(memory),
            encoding="utf-8",
            newline="\n",
        )
        return memory.node

    def forget(self, node: NodeId) -> None:
        path = self._node_path(node)
        if _read_node(path) is None:
            return
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        closing = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
        lines.insert(closing, "tombstone: true\n")
        path.write_text("".join(lines), encoding="utf-8", newline="\n")

    def _node_path(self, node: NodeId) -> Path:
        relative = Path(f"{node}.md")
        if relative.parent != Path():
            raise MemoryNodeError(f'Invalid graph node id "{node}".')
        return self._path / relative


def _render_node(memory: MemoryNode) -> str:
    description = render_frontmatter_value(memory.description)
    subjects = render_frontmatter_value(memory.subjects)
    tools = (
        f"tools: {render_frontmatter_value(memory.tools)}\n" if isinstance(memory, Episode) else ""
    )
    turn = f"turn: {memory.turn}\n" if isinstance(memory, Episode) else ""
    body = memory.body.rstrip("\r\n")
    return (
        "---\n"
        f"date: {memory.date.isoformat()}\n"
        f"thread: {memory.thread}\n"
        f"{turn}"
        f"description: {description}\n"
        f"subjects: {subjects}\n"
        f"{tools}"
        "---\n"
        f"{body}\n"
    )


def _read_node(path: Path) -> MemoryNode | None:
    try:
        values, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        frontmatter = _FRONTMATTER.validate_python(values)
    except (FrontmatterError, ValidationError) as exc:
        raise MemoryNodeError(f'Graph node "{path}" has invalid frontmatter.') from exc
    if frontmatter.tombstone:
        return None
    node = NodeId(path.stem)
    if isinstance(frontmatter, _FactFrontmatter):
        return Fact(
            node,
            frontmatter.date,
            frontmatter.thread,
            frontmatter.description,
            frontmatter.subjects,
            body,
        )
    return Episode(
        node=node,
        date=frontmatter.date,
        thread=frontmatter.thread,
        description=frontmatter.description,
        subjects=frontmatter.subjects,
        body=body,
        turn=frontmatter.turn,
        tools=frontmatter.tools,
    )
