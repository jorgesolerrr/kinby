"""Answer a client's reads and writes of the knowledge graph."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime

from kinby.contracts import (
    MemoryAddCommand,
    MemoryCorrectCommand,
    MemoryForgetCommand,
    MemoryForgetResult,
    MemoryListCommand,
    MemoryListResult,
    MemoryOpenCommand,
    MemoryOpenResult,
    MemoryWriteResult,
    NodeId,
    NodeKind,
    NodeSource,
    NodeSummary,
)
from kinby.core.errors import (
    EpisodeNotCorrectable,
    InvalidFact,
    InvalidMemoryNode,
    MemoryNodeNotFound,
)
from kinby.memory import Episode, Fact, GraphStore, InvalidNodeId, MemoryNode, NodeNotFound
from kinby.memory.facade import new_node_id


class InstanceMemory:
    """The instance's knowledge graph as clients read and write it.

    A write goes straight to the graph: it passes no gate and appends nothing to a thread.
    """

    def __init__(self, graph: GraphStore, clock: Callable[[], datetime]) -> None:
        self._graph = graph
        self._clock = clock

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

    async def add(self, command: MemoryAddCommand) -> MemoryWriteResult:
        return MemoryWriteResult(node=self._graph.remember(self._user_fact(command)))

    async def correct(self, command: MemoryCorrectCommand) -> MemoryWriteResult:
        """Write the corrected fact, then tombstone the one it replaces."""
        with _refusals():
            replaced = self._graph.open(command.node)
        if isinstance(replaced, Episode):
            raise EpisodeNotCorrectable(f'Graph node "{command.node}" is an episode.')
        corrected = self._graph.remember(self._user_fact(command))
        self._graph.forget(replaced.node)
        return MemoryWriteResult(node=corrected)

    async def forget(self, command: MemoryForgetCommand) -> MemoryForgetResult:
        with _refusals():
            self._graph.open(command.node)
        self._graph.forget(command.node)
        return MemoryForgetResult()

    def _user_fact(self, command: MemoryAddCommand) -> Fact:
        """The fact *command* describes, dated today and belonging to no thread."""
        description = command.description.strip()
        if not description:
            raise InvalidFact({"description": "Describe the fact."})
        today = self._clock().astimezone(UTC).date()
        return Fact(
            node=new_node_id(today, description),
            date=today,
            description=description,
            subjects=tuple(command.subjects),
            body=command.body,
            thread=None,
            added_by_user=True,
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
