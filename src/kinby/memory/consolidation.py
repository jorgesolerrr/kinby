"""Consolidation, the dreaming stage that turns uncovered episodes into cited facts (ADR 0099)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import TypeAdapter
from pydantic.dataclasses import dataclass

from kinby.contracts import MemoryConsolidated, NodeId, ToolResult
from kinby.memory.facade import Episode, Fact, new_node_id
from kinby.plugins.tools import Tool, tool

if TYPE_CHECKING:
    from kinby.core.events import EventLog
    from kinby.memory.graph import GraphStore

#: The most episodes one consolidation reviews, so a first run on a large graph fits its budget.
BATCH_LIMIT = 50

_RULES = (
    "Write a fact for a lesson or detail several episodes repeat, and an area fact for an area "
    "of the work they share: where it lives, what was decided about it, and why.",
    "Every fact cites as sources the episodes it was learned from: episodes of this batch, or "
    "the sources of a fact shown here.",
    "An area is a subject, and the latest fact about a subject wins. To update an area fact, "
    "write a new fact on its subject that cites its sources and the new episodes.",
    "Write nothing the episodes do not support. A batch that teaches nothing needs no fact.",
)
_CONTRACT = (
    "Call consolidate once with every fact. The call covers every episode of this batch, "
    "including those that produced no fact. A refused call writes nothing and covers nothing."
)


@dataclass(frozen=True)
class LearnedFact:
    """One fact consolidation learned, citing the episodes it came from."""

    description: str
    subjects: tuple[str, ...]
    body: str
    sources: tuple[NodeId, ...]


@dataclass(frozen=True)
class _FramedEpisode:
    node: NodeId
    date: date
    description: str
    subjects: tuple[str, ...]
    body: str


@dataclass(frozen=True)
class _FramedFact:
    node: NodeId
    date: date
    description: str
    subjects: tuple[str, ...]
    body: str
    sources: tuple[NodeId, ...]


@dataclass(frozen=True)
class _Subject:
    subject: str
    episodes: tuple[NodeId, ...]
    facts: tuple[NodeId, ...]


@dataclass(frozen=True)
class _ConsolidationFrame:
    """What the code step hands the model, and the record the consolidate tool checks against."""

    rules: tuple[str, ...]
    contract: str
    subjects: tuple[_Subject, ...]
    episodes: tuple[_FramedEpisode, ...]
    facts: tuple[_FramedFact, ...]

    @property
    def batch(self) -> tuple[NodeId, ...]:
        return tuple(episode.node for episode in self.episodes)

    @property
    def citable(self) -> frozenset[NodeId]:
        return frozenset(self.batch) | {source for fact in self.facts for source in fact.sources}


_FRAME = TypeAdapter(_ConsolidationFrame)
#: Writes a consolidate call started, held until they finish, since the loop holds tasks weakly.
_finishing: set[asyncio.Task[tuple[NodeId, ...]]] = set()


class ConsolidationRefused(ValueError):
    """A consolidate call the batch does not support. Nothing was written."""


def consolidation_step(graph: GraphStore, log: EventLog) -> Tool:
    """The stage's code step: frame the oldest uncovered episodes, or report no work."""

    @tool(write=False)
    def consolidate() -> str | None:
        """Frame the oldest uncovered episodes and the facts on their subjects."""
        covered = {
            node
            for event in log.all_events()
            if isinstance(event.payload, MemoryConsolidated)
            for node in event.payload.episodes
        }
        nodes = graph.nodes()
        batch = sorted(
            (node for node in nodes if isinstance(node, Episode) and node.node not in covered),
            key=lambda episode: (episode.date, episode.node),
        )[:BATCH_LIMIT]
        if not batch:
            return None
        frame = _frame(batch, [node for node in nodes if isinstance(node, Fact)])
        return _FRAME.dump_json(frame, indent=2).decode()

    return consolidate


def consolidate_tool(
    graph: GraphStore,
    log: EventLog,
    thread_id: UUID,
    turn_id: UUID,
    *,
    today: Callable[[], date],
) -> Tool:
    """The model's one write on a consolidation turn, checked against the turn's frame."""

    async def write_and_cover(
        frame: _ConsolidationFrame, facts: tuple[LearnedFact, ...]
    ) -> tuple[NodeId, ...]:
        written = await asyncio.to_thread(_write, graph, frame, facts, thread_id, today())
        # The log, not the turn's emit, which refuses events once the turn is interrupted.
        await log.append(
            thread_id, turn_id, MemoryConsolidated(episodes=frame.batch, facts=written)
        )
        return written

    @tool(write=True)
    async def consolidate(facts: tuple[LearnedFact, ...]) -> str:
        """Write this consolidation's facts and cover every episode of its batch.

        Each fact cites as sources the episodes it was learned from: episodes of the batch, or
        the sources of a fact the frame shows. Call with no facts when the batch teaches nothing.
        A refused call writes nothing and covers nothing.
        """
        frame = await asyncio.to_thread(_turn_frame, log, thread_id, turn_id)
        writing = asyncio.create_task(write_and_cover(frame, facts))
        _finishing.add(writing)
        writing.add_done_callback(_finishing.discard)
        # An interrupt cancels this call but not the writes, so no fact lands without coverage.
        written = await asyncio.shield(writing)
        return f"Wrote {len(written)} facts and covered {len(frame.batch)} episodes."

    return consolidate


def _frame(batch: list[Episode], facts: list[Fact]) -> _ConsolidationFrame:
    subjects: dict[str, str] = {}
    for episode in batch:
        for subject in episode.subjects:
            subjects.setdefault(subject.casefold(), subject)
    framed_facts = [
        fact for fact in facts if any(subject.casefold() in subjects for subject in fact.subjects)
    ]
    return _ConsolidationFrame(
        rules=_RULES,
        contract=_CONTRACT,
        subjects=tuple(
            _Subject(
                subject=subject,
                episodes=_about(key, batch),
                facts=_about(key, framed_facts),
            )
            for key, subject in subjects.items()
        ),
        episodes=tuple(
            _FramedEpisode(
                node=episode.node,
                date=episode.date,
                description=episode.description,
                subjects=episode.subjects,
                body=episode.body,
            )
            for episode in batch
        ),
        facts=tuple(
            _FramedFact(
                node=fact.node,
                date=fact.date,
                description=fact.description,
                subjects=fact.subjects,
                body=fact.body,
                sources=fact.sources,
            )
            for fact in framed_facts
        ),
    )


def _about(subject: str, nodes: list[Episode] | list[Fact]) -> tuple[NodeId, ...]:
    return tuple(
        node.node for node in nodes if subject in {each.casefold() for each in node.subjects}
    )


def _turn_frame(log: EventLog, thread_id: UUID, turn_id: UUID) -> _ConsolidationFrame:
    """The frame this turn's code step returned, which the event log keeps across a restart."""
    payloads = [event.payload for event in log.stored(thread_id) if event.turn_id == turn_id]
    if any(isinstance(payload, MemoryConsolidated) for payload in payloads):
        raise ConsolidationRefused("Nothing was written. This turn already covered its batch.")
    return _FRAME.validate_json(
        next(payload.output for payload in payloads if isinstance(payload, ToolResult))
    )


def _write(
    graph: GraphStore,
    frame: _ConsolidationFrame,
    facts: tuple[LearnedFact, ...],
    thread_id: UUID,
    learned_on: date,
) -> tuple[NodeId, ...]:
    episodes = {node.node for node in graph.nodes() if isinstance(node, Episode)}
    refusals: list[str] = []
    for fact in facts:
        if not fact.sources:
            refusals.append(f'Fact "{fact.description}" cites no source.')
        for source in fact.sources:
            if source not in frame.citable:
                refusals.append(f'Source "{source}" is outside the batch.')
            elif source not in episodes:
                refusals.append(f'Source "{source}" is not an episode.')
    if refusals:
        raise ConsolidationRefused(" ".join(("Nothing was written.", *refusals)))
    return tuple(
        graph.remember(
            Fact(
                node=new_node_id(learned_on, fact.description),
                date=learned_on,
                description=fact.description,
                subjects=fact.subjects,
                body=fact.body,
                thread=thread_id,
                sources=fact.sources,
            )
        )
        for fact in facts
    )
