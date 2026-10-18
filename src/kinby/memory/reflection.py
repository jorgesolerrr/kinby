"""Reflection, the dreaming stage that makes proposals from what consolidation learns (ADR 0099)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import JsonValue, TypeAdapter
from pydantic.dataclasses import dataclass

from kinby.contracts import (
    Event,
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
    from kinby.core.events import EventLog
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
        events = list(log.all_events())
        written = _facts_since_last_reflection(events, name)
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
            dream_reports=_dream_reports(events, name),
        )
        return _FRAME.dump_json(frame, indent=2).decode()

    return reflect


def _facts_since_last_reflection(events: list[Event], name: RoutineName) -> set[NodeId]:
    # Only consolidation writes these facts, and it writes them as it covers, so none arrive late.
    started: dict[tuple[UUID, UUID], int] = {}
    since = 0
    for index, event in enumerate(events):
        key = (event.thread_id, event.turn_id)
        match event.payload:
            case TurnStarted(origin=RoutineOrigin(name=origin)) if origin == name:
                started.setdefault(key, index)
            case TurnCompleted() if key in started:
                since = started[key]
    return {
        fact
        for event in events[since:]
        if isinstance(event.payload, MemoryConsolidated)
        for fact in event.payload.facts
    }


def _dream_reports(events: list[Event], name: RoutineName) -> tuple[_DreamReport, ...]:
    """This routine's earlier turns that left a report or a proposal, with what became of each
    proposal."""
    turns: dict[tuple[UUID, UUID], datetime] = {}
    texts: dict[tuple[UUID, UUID], str] = {}
    proposals: dict[tuple[UUID, UUID], dict[str, _Proposal]] = {}
    for event in events:
        key = (event.thread_id, event.turn_id)
        payload = event.payload
        if isinstance(payload, TurnStarted):
            if isinstance(payload.origin, RoutineOrigin) and payload.origin.name == name:
                turns.setdefault(key, event.timestamp)
                texts.setdefault(key, "")
                proposals.setdefault(key, {})
            continue
        if key not in turns:
            continue
        calls = proposals[key]
        match payload:
            case MessageDelta(text=text):
                texts[key] += text
            case ToolCall(call_id=call_id, name=tool_name, arguments=arguments):
                # The report is the final message, the text after the last tool call.
                texts[key] = ""
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
    return tuple(
        _DreamReport(at=at, report=texts[key], proposals=tuple(proposals[key].values()))
        for key, at in turns.items()
        if texts[key] or proposals[key]
    )
