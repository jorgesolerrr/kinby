"""Consolidation, the dreaming stage that turns uncovered episodes into cited facts (ADR 0099)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Collection
from datetime import date, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import TypeAdapter
from pydantic.dataclasses import dataclass

from kinby.contracts import MemoryConsolidated, NodeId, ToolResult
from kinby.memory.facade import Episode, Fact, new_node_id
from kinby.plugins.tools import Tool, tool

if TYPE_CHECKING:
    from kinby.core.events import EventLog
    from kinby.memory.graph import ForgottenFact, GraphStore

#: The most episodes one consolidation reviews, so a first run on a large graph fits its budget.
BATCH_LIMIT = 50
#: How old a covered episode no live fact cites gets before it is pruned (ADR 0074).
PRUNE_AGE = timedelta(days=90)

_RULES = (
    "Write a fact for a lesson or detail several episodes repeat, and an area fact for an area "
    "of the work they share: where it lives, what was decided about it, and why.",
    "Every fact cites as sources the episodes it was learned from: episodes of this batch, or "
    "the sources of a fact shown here.",
    "An area is a subject, and the latest fact about a subject wins. To update an area fact, "
    "write a new fact on its subject that cites its sources and the new episodes.",
    "Write nothing the episodes do not support. A batch that teaches nothing needs no fact.",
    "The user forgot the forgotten facts: do not write them back. A fact citing a forgotten "
    "fact's sources is refused unless it also cites an episode recorded after the forget.",
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
class _FramedForgottenFact:
    node: NodeId
    date: date
    description: str
    subjects: tuple[str, ...]
    sources: tuple[NodeId, ...]
    forgotten_on: date | None


@dataclass(frozen=True)
class _Subject:
    subject: str
    episodes: tuple[NodeId, ...]
    facts: tuple[NodeId, ...]
    forgotten: tuple[NodeId, ...]


@dataclass(frozen=True)
class _ConsolidationFrame:
    """What the code step hands the model, and the record the consolidate tool checks against."""

    rules: tuple[str, ...]
    contract: str
    subjects: tuple[_Subject, ...]
    episodes: tuple[_FramedEpisode, ...]
    facts: tuple[_FramedFact, ...]
    forgotten: tuple[_FramedForgottenFact, ...]

    @property
    def batch(self) -> tuple[NodeId, ...]:
        return tuple(episode.node for episode in self.episodes)

    @property
    def citable(self) -> frozenset[NodeId]:
        return frozenset(self.batch) | {
            source for fact in (*self.facts, *self.forgotten) for source in fact.sources
        }


_FRAME = TypeAdapter(_ConsolidationFrame)
#: Writes a consolidate call started, held until they finish, since the loop holds tasks weakly.
_finishing: set[asyncio.Task[tuple[NodeId, ...]]] = set()


class ConsolidationRefused(ValueError):
    """A consolidate call the batch does not support. Nothing was written."""


def consolidation_step(
    graph: GraphStore, log: EventLog, *, today: Callable[[], date], prune: bool
) -> Tool:
    """The stage's code step: prune, then frame the oldest uncovered episodes or report no work.

    It prunes only when *prune*, which the gate grants when the stage's writes need no approval.
    """

    @tool(write=False)
    def consolidate() -> str | None:
        """Prune old episodes, then frame the oldest uncovered ones and the facts on them."""
        covered = {
            node
            for event in log.all_events()
            if isinstance(event.payload, MemoryConsolidated)
            for node in event.payload.episodes
        }
        if prune:
            _prune(graph, covered, today() - PRUNE_AGE)
        nodes = graph.nodes()
        batch = sorted(
            (node for node in nodes if isinstance(node, Episode) and node.node not in covered),
            key=lambda episode: (episode.date, episode.node),
        )[:BATCH_LIMIT]
        if not batch:
            return None
        frame = _frame(
            batch, [node for node in nodes if isinstance(node, Fact)], graph.forgotten_facts()
        )
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
        the sources of a fact the frame shows. A fact citing a forgotten fact's sources is
        refused unless it also cites an episode recorded after the forget. Call with no facts
        when the batch teaches nothing. A refused call writes nothing and covers nothing.
        """
        frame = await asyncio.to_thread(_turn_frame, log, thread_id, turn_id)
        writing = asyncio.create_task(write_and_cover(frame, facts))
        _finishing.add(writing)
        writing.add_done_callback(_finishing.discard)
        # An interrupt cancels this call but not the writes, so no fact lands without coverage.
        written = await asyncio.shield(writing)
        return f"Wrote {len(written)} facts and covered {len(frame.batch)} episodes."

    return consolidate


def _prune(graph: GraphStore, covered: set[NodeId], cutoff: date) -> None:
    """Delete each episode from before *cutoff* that is covered and cited by no live fact."""
    nodes = graph.nodes()
    cited = {source for node in nodes if isinstance(node, Fact) for source in node.sources}
    for node in nodes:
        if (
            isinstance(node, Episode)
            and node.date < cutoff
            and node.node in covered
            and node.node not in cited
        ):
            graph.prune(node.node)


def _frame(
    batch: list[Episode], facts: list[Fact], forgotten: list[ForgottenFact]
) -> _ConsolidationFrame:
    subjects: dict[str, str] = {}
    for episode in batch:
        for subject in episode.subjects:
            subjects.setdefault(subject.casefold(), subject)
    framed_facts = _on(subjects, facts)
    framed_forgotten = _forgotten_on(subjects, forgotten)
    return _ConsolidationFrame(
        rules=_RULES,
        contract=_CONTRACT,
        subjects=tuple(
            _Subject(
                subject=subject,
                episodes=_about(key, batch),
                facts=_about(key, framed_facts),
                forgotten=_about(key, [gone.fact for gone in framed_forgotten]),
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
        forgotten=tuple(
            _FramedForgottenFact(
                node=gone.fact.node,
                date=gone.fact.date,
                description=gone.fact.description,
                subjects=gone.fact.subjects,
                sources=gone.fact.sources,
                forgotten_on=gone.forgotten_on,
            )
            for gone in framed_forgotten
        ),
    )


def _on(subjects: Collection[str], facts: list[Fact]) -> list[Fact]:
    """The facts on any of *subjects*, which are casefolded."""
    return [
        fact for fact in facts if any(subject.casefold() in subjects for subject in fact.subjects)
    ]


def _forgotten_on(subjects: Collection[str], forgotten: list[ForgottenFact]) -> list[ForgottenFact]:
    """The forgotten facts on any of *subjects*, which are casefolded."""
    return [
        gone
        for gone in forgotten
        if any(subject.casefold() in subjects for subject in gone.fact.subjects)
    ]


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
    episodes = {node.node: node.date for node in graph.nodes() if isinstance(node, Episode)}
    forgotten = graph.forgotten_facts()
    refusals: list[str] = []
    for fact in facts:
        if not fact.sources:
            refusals.append(f'Fact "{fact.description}" cites no source.')
        elif any(
            _writes_back(fact, gone, episodes)
            for gone in _forgotten_on({subject.casefold() for subject in fact.subjects}, forgotten)
        ):
            refusals.append(
                f'Fact "{fact.description}" cites a forgotten fact\'s sources '
                "and no episode recorded after the forget."
            )
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


def _writes_back(fact: LearnedFact, gone: ForgottenFact, episodes: dict[NodeId, date]) -> bool:
    """Whether *fact* relearns *gone* from its sources with nothing recorded since the forget.

    A tombstone with no forget day lets no episode count as recorded after it.
    """
    if not set(fact.sources) & set(gone.fact.sources):
        return False
    return not any(
        gone.forgotten_on is not None
        and source in episodes
        and episodes[source] > gone.forgotten_on
        for source in fact.sources
    )
