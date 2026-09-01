"""The interface shared by memory callers and feeds."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID, uuid7

from kinby.contracts import NodeId


@dataclass(frozen=True)
class MemoryHit:
    """A graph node description returned by recall."""

    node: NodeId
    date: date
    description: str


@dataclass(frozen=True)
class _MemoryNode:
    """Fields shared by opened knowledge graph records."""

    node: NodeId
    date: date
    thread: UUID
    description: str
    subjects: tuple[str, ...]
    body: str


@dataclass(frozen=True)
class Fact(_MemoryNode):
    """One opened, time-stamped fact."""


@dataclass(frozen=True)
class Episode(_MemoryNode):
    """One opened turn record with its tool trace."""

    turn: UUID
    tools: tuple[str, ...]


type MemoryNode = Fact | Episode


class Memory(Protocol):
    """Search, open, add, and forget long-term memory."""

    def recall(
        self,
        query: str,
        *,
        after: date | None = None,
        before: date | None = None,
    ) -> tuple[MemoryHit, ...]: ...

    def open(self, node: NodeId) -> MemoryNode: ...

    def remember(self, memory: MemoryNode) -> NodeId: ...

    def forget(self, node: NodeId) -> None: ...


def new_node_id(recorded_on: date, description: str) -> NodeId:
    """Create a readable, chronologically sortable graph node id."""
    slug = re.sub(r"[^a-z0-9]+", "-", description.casefold()).strip("-")
    readable = (slug or "fact")[:64].rstrip("-")
    return NodeId(f"{recorded_on.isoformat()}-{uuid7().hex}-{readable}")
