"""Reflection, the dreaming stage that makes proposals from what consolidation learns (ADR 0099)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import replace
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import JsonValue, TypeAdapter
from pydantic.dataclasses import dataclass

from kinby.contracts import (
    Event,
    EventType,
    GateDecider,
    GateOutcome,
    MemoryConsolidated,
    MessageDelta,
    NodeId,
    RoutineName,
    RoutineOrigin,
    ToolCall,
    ToolGated,
    ToolResult,
    TurnCompleted,
    TurnStarted,
)
from kinby.memory.facade import Episode, Fact
from kinby.plugins.tools import Tool, tool

if TYPE_CHECKING:
    from kinby.core.events import EventLog, EventReader
    from kinby.memory.graph import GraphStore

#: The tools a proposal is written through. Each is a write, so the gate decides it.
_PROPOSAL_TOOLS = frozenset({"remember", "skill_write", "routine_write"})

_PROPOSAL_KINDS = (
    "A fact, written with remember, for a pattern a later search should find.",
    "A skill, written with skill_write, for a workflow the user repeats.",
    "A routine without a code step, written with routine_write with no run key and no code, "
    "for work the user does on a schedule.",
)
_RULES = (
    "Look across the facts and the episodes they cite for patterns in how the user works.",
    "Propose only what the facts and episodes support. A period without a pattern needs no "
    "proposal.",
    "Never propose code: no hook, no tool and no code step. Never edit the profile.",
    "Never repeat a proposal from an earlier dream report, least of all one the user declined.",
)
_CONTRACT = (
    "Write each proposal with its tool. Each write waits on the user's approval. Your final "
    "message is the dream report: each fact you wrote, with the facts and episodes it came "
    "from, and each proposal, with its status."
)


class _ProposalStatus(StrEnum):
    WAITING = "waiting"
    APPLIED = "applied"
    #: The user denied it.
    DECLINED = "declined"
    #: The gate's policy or the tool refused it.
    REFUSED = "refused"


@dataclass(frozen=True)
class _Proposal:
    tool: str
    arguments: dict[str, JsonValue]
    status: _ProposalStatus
    #: The user's reason for declining it, or the refusal.
    reason: str | None = None


@dataclass(frozen=True)
class _DreamReport:
    at: datetime
    report: str
    proposals: tuple[_Proposal, ...]


@dataclass(frozen=True)
class _ReflectionFrame:
    rules: tuple[str, ...]
    proposal_kinds: tuple[str, ...]
    contract: str
    facts: tuple[Fact, ...]
    episodes: tuple[Episode, ...]
    dream_reports: tuple[_DreamReport, ...]


_FRAME = TypeAdapter(_ReflectionFrame)


def reflection_step(graph: GraphStore, log: EventLog, name: RoutineName) -> Tool:
    """The stage's code step: frame the facts consolidation wrote since this routine last
    reflected, or report no work."""

    @tool(write=False)
    def reflect() -> str | None:
        """Frame the facts consolidation wrote since the last reflection and the earlier dream
        reports."""
        with log.reader() as reader:
            written = _facts_since_last_reflection(reader, name)
            nodes = graph.nodes()
            facts = tuple(node for node in nodes if isinstance(node, Fact) and node.node in written)
            if not facts:
                return None
            cited = {source for fact in facts for source in fact.sources}
            frame = _ReflectionFrame(
                rules=_RULES,
                proposal_kinds=_PROPOSAL_KINDS,
                contract=_CONTRACT,
                facts=facts,
                episodes=tuple(
                    node for node in nodes if isinstance(node, Episode) and node.node in cited
                ),
                dream_reports=_dream_reports(reader, name),
            )
        return _FRAME.dump_json(frame, indent=2).decode()

    return reflect


def _facts_since_last_reflection(reader: EventReader, name: RoutineName) -> set[NodeId]:
    # Only consolidation writes these facts, and it writes them as it covers, so none arrive late.
    started: set[tuple[UUID, UUID]] = set()
    last: tuple[UUID, UUID] | None = None
    for event in reader.events_of({EventType.TURN_STARTED, EventType.TURN_COMPLETED}):
        key = (event.thread_id, event.turn_id)
        match event.payload:
            case TurnStarted(origin=RoutineOrigin(name=origin)) if origin == name:
                started.add(key)
            case TurnCompleted() if key in started:
                last = key
    facts: set[NodeId] = set()
    since = last is None
    for event in reader.events_of({EventType.TURN_STARTED, EventType.MEMORY_CONSOLIDATED}):
        match event.payload:
            case TurnStarted() if (event.thread_id, event.turn_id) == last:
                since = True
            case MemoryConsolidated(facts=written) if since:
                facts.update(written)
    return facts


def _dream_reports(reader: EventReader, name: RoutineName) -> tuple[_DreamReport, ...]:
    """This routine's earlier turns that left a report or a proposal, with what became of each
    proposal."""
    reports = (
        _dream_report(started, reader.turn_events(started.turn_id))
        for started in _starts(reader, name)
    )
    return tuple(report for report in reports if report.report or report.proposals)


def _starts(reader: EventReader, name: RoutineName) -> Iterator[Event]:
    """The start of each turn the routine ran."""
    seen: set[tuple[UUID, UUID]] = set()
    for event in reader.events_of({EventType.TURN_STARTED}):
        key = (event.thread_id, event.turn_id)
        match event.payload:
            case TurnStarted(origin=RoutineOrigin(name=origin)) if (
                origin == name and key not in seen
            ):
                seen.add(key)
                yield event


def _dream_report(started: Event, events: Iterable[Event]) -> _DreamReport:
    """The report and the proposals of the turn *started* began, read from the turn's events."""
    text = ""
    calls: dict[str, _Proposal] = {}
    begun = False
    for event in events:
        payload = event.payload
        if event.thread_id != started.thread_id:
            continue
        if isinstance(payload, TurnStarted):
            begun = True
            continue
        if not begun:
            continue
        match payload:
            case MessageDelta(text=delta):
                text += delta
            case ToolCall(call_id=call_id, name=tool_name, arguments=arguments):
                # The report is the final message, the text after the last tool call.
                text = ""
                if tool_name in _PROPOSAL_TOOLS:
                    calls[call_id] = _Proposal(tool_name, arguments, _ProposalStatus.WAITING)
            case ToolGated(call_id=call_id, action=GateOutcome.DENY) if call_id in calls:
                declined = payload.decided_by is GateDecider.USER
                calls[call_id] = replace(
                    calls[call_id],
                    status=_ProposalStatus.DECLINED if declined else _ProposalStatus.REFUSED,
                    reason=payload.reason if declined else f'Gate rule "{payload.rule}".',
                )
            case ToolResult(call_id=call_id) if (
                call_id in calls and calls[call_id].status is _ProposalStatus.WAITING
            ):
                calls[call_id] = replace(
                    calls[call_id],
                    status=_ProposalStatus.REFUSED if payload.error else _ProposalStatus.APPLIED,
                    reason=payload.output if payload.error else None,
                )
    return _DreamReport(at=started.timestamp, report=text, proposals=tuple(calls.values()))
