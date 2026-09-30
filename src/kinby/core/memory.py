"""Answer a client's reads of the knowledge graph."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

from kinby.contracts import (
    MemoryListCommand,
    MemoryListResult,
    MemoryOpenCommand,
    MemoryOpenResult,
    NodeId,
    NodeKind,
    NodeSource,
    NodeSummary,
)
from kinby.core.errors import InvalidMemoryNode, MemoryNodeNotFound
from kinby.memory import Episode, GraphStore, InvalidNodeId, MemoryNode, NodeNotFound


class InstanceMemory:
    """The instance's knowledge graph as clients read it."""

    def __init__(self, graph: GraphStore) -> None:
        self._graph = graph

    async def list(self, command: MemoryListCommand) -> MemoryListResult:
        """One page of the live nodes the command's filters match, newest first."""
        below = None if command.cursor is None else _sort_key(command.cursor)
        subject = None if command.subject is None else command.subject.casefold()
        matches = [
            memory
            for memory in self._graph.nodes(
                command.query, after=command.after, before=command.before
            )
            if (command.kind is None or _kind(memory) is command.kind)
            and (subject is None or subject in (named.casefold() for named in memory.subjects))
            and (below is None or (memory.date, memory.node) < below)
        ]
        page = matches[: command.limit]
        return MemoryListResult(
            items=[_summary(memory) for memory in page],
            cursor=page[-1].node if len(matches) > command.limit else None,
        )

    async def open(self, command: MemoryOpenCommand) -> MemoryOpenResult:
        with _refusals():
            memory = self._graph.open(command.node)
        episode = memory if isinstance(memory, Episode) else None
        return MemoryOpenResult(
            **_summary(memory).model_dump(),
            body=memory.body,
            thread=memory.thread,
            turn=None if episode is None else episode.turn,
            tools=None if episode is None else list(episode.tools),
        )


def _sort_key(cursor: NodeId) -> tuple[date, NodeId]:
    """Where *cursor* sorts. A node id begins with the date the node was recorded on."""
    try:
        return date.fromisoformat(cursor[:10]), cursor
    except ValueError as exc:
        raise InvalidMemoryNode(f'Invalid cursor "{cursor}".') from exc


@contextmanager
def _refusals() -> Iterator[None]:
    """Report the graph's refusals as the contract errors a client reads."""
    try:
        yield
    except NodeNotFound as exc:
        raise MemoryNodeNotFound(str(exc)) from exc
    except InvalidNodeId as exc:
        raise InvalidMemoryNode(str(exc)) from exc


def _summary(memory: MemoryNode) -> NodeSummary:
    return NodeSummary(
        node=memory.node,
        kind=_kind(memory),
        date=memory.date,
        description=memory.description,
        subjects=list(memory.subjects),
        source=_source(memory),
    )


def _kind(memory: MemoryNode) -> NodeKind:
    return NodeKind.EPISODE if isinstance(memory, Episode) else NodeKind.FACT


def _source(memory: MemoryNode) -> NodeSource:
    if isinstance(memory, Episode):
        return NodeSource.RECAP
    return NodeSource.USER if memory.added_by_user else NodeSource.AGENT
