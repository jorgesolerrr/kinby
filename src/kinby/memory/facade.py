"""The interface shared by memory callers and feeds."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import NewType, Protocol
from uuid import UUID

NodeId = NewType("NodeId", str)


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
    """One opened task record with its tool trace."""

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
