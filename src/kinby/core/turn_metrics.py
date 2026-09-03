"""Derive one metrics record for every closed turn in an event log."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from kinby.contracts import (
    ApprovalRequested,
    Event,
    MemoryCallCounts,
    MemoryRecapped,
    ToolCall,
    ToolResult,
    TurnClosingKind,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnMetrics,
    TurnRated,
    TurnStarted,
)

_MEMORY_CALL_KINDS = {
    "memory_search": "search",
    "memory_open": "open",
    "remember": "remember",
    "forget": "forget",
}
_CHARACTERS_PER_ESTIMATED_MEMORY_TOKEN = 4


@dataclass
class _TurnEvents:
    started_at: datetime
    model: str
    tool_calls: Counter[str] = field(default_factory=Counter)
    memory_calls: Counter[str] = field(default_factory=Counter)
    approvals_requested: int = 0
    memory_characters: int = 0


def turn_metrics(events: Iterable[Event]) -> list[TurnMetrics]:
    """Read event history once and return its closed turns in closing order."""
    open_turns: dict[tuple[UUID, UUID], _TurnEvents] = {}
    closed_turns: dict[tuple[UUID, UUID], TurnMetrics] = {}
    records: list[TurnMetrics] = []

    for event in events:
        key = event.thread_id, event.turn_id
        payload = event.payload
        if isinstance(payload, TurnStarted):
            open_turns[key] = _TurnEvents(event.timestamp, payload.model)
            continue

        turn = open_turns.get(key)
        if isinstance(payload, ToolCall):
            if turn is not None:
                turn.tool_calls[payload.name] += 1
                if kind := _MEMORY_CALL_KINDS.get(payload.name):
                    turn.memory_calls[kind] += 1
            continue
        if isinstance(payload, ToolResult):
            if turn is not None and payload.name in _MEMORY_CALL_KINDS:
                turn.memory_characters += len(payload.output)
            continue
        if isinstance(payload, ApprovalRequested):
            if turn is not None:
                turn.approvals_requested += 1
            continue
        if isinstance(payload, TurnCompleted | TurnFailed | TurnInterrupted):
            turn = open_turns.pop(key, None)
            input_tokens = payload.input_tokens if isinstance(payload, TurnCompleted) else 0
            output_tokens = payload.output_tokens if isinstance(payload, TurnCompleted) else 0
            memory_calls = MemoryCallCounts.model_validate(turn.memory_calls if turn else {})
            record = TurnMetrics(
                thread_id=event.thread_id,
                turn_id=event.turn_id,
                model=turn.model if turn else None,
                closing_kind=_closing_kind(payload),
                started_at=turn.started_at if turn else None,
                closed_at=event.timestamp,
                duration_seconds=(
                    (event.timestamp - turn.started_at).total_seconds() if turn else None
                ),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                recap_input_tokens=0,
                recap_output_tokens=0,
                tool_calls=dict(turn.tool_calls) if turn else {},
                memory_calls=memory_calls,
                memory_consulted=bool(memory_calls.search or memory_calls.open),
                approvals_requested=turn.approvals_requested if turn else 0,
                memory_tokens=(
                    turn.memory_characters / _CHARACTERS_PER_ESTIMATED_MEMORY_TOKEN if turn else 0
                ),
                rating=None,
            )
            records.append(record)
            closed_turns[key] = record
            continue

        record = closed_turns.get(key)
        if record is None:
            continue
        if isinstance(payload, MemoryRecapped):
            record.input_tokens += payload.input_tokens - record.recap_input_tokens
            record.output_tokens += payload.output_tokens - record.recap_output_tokens
            record.recap_input_tokens = payload.input_tokens
            record.recap_output_tokens = payload.output_tokens
        elif isinstance(payload, TurnRated):
            record.rating = payload

    return records


def _closing_kind(
    payload: TurnCompleted | TurnFailed | TurnInterrupted,
) -> TurnClosingKind:
    if isinstance(payload, TurnCompleted):
        return TurnClosingKind.COMPLETED
    if isinstance(payload, TurnFailed):
        return TurnClosingKind.FAILED
    return TurnClosingKind.INTERRUPTED
