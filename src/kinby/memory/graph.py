"""Read knowledge graph nodes from an instance directory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated
from uuid import UUID

from pydantic import Field, TypeAdapter, ValidationError

from kinby.frontmatter import (
    FrontmatterError,
    parse_frontmatter,
)
from kinby.instance.layout import GRAPH_DIR, MEMORY_DIR
from kinby.memory.facade import Episode, Fact, MemoryHit, MemoryNode, NodeId


class MemoryNodeError(ValueError):
    """A graph node has missing or invalid frontmatter."""


@dataclass(frozen=True)
class _NodeFrontmatter:
    date: date
    thread: UUID
    description: Annotated[str, Field(min_length=1)]
    subjects: tuple[str, ...]
    tools: tuple[str, ...] | None = None


_NODE_FRONTMATTER = TypeAdapter(_NodeFrontmatter)


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
        relative = Path(f"{node}.md")
        if relative.parent != Path():
            raise MemoryNodeError(f'Invalid graph node id "{node}".')
        return _read_node(self._path / relative)

    def remember(self, memory: MemoryNode) -> NodeId:
        raise NotImplementedError

    def forget(self, node: NodeId) -> None:
        raise NotImplementedError


def _read_node(path: Path) -> MemoryNode:
    try:
        values, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        frontmatter = _NODE_FRONTMATTER.validate_python(values)
    except (FrontmatterError, ValidationError) as exc:
        raise MemoryNodeError(f'Graph node "{path}" has invalid frontmatter.') from exc
    node = NodeId(path.stem)
    if frontmatter.tools is None:
        return Fact(
            node,
            frontmatter.date,
            frontmatter.thread,
            frontmatter.description,
            frontmatter.subjects,
            body,
        )
    return Episode(
        node,
        frontmatter.date,
        frontmatter.thread,
        frontmatter.description,
        frontmatter.subjects,
        body,
        frontmatter.tools,
    )
