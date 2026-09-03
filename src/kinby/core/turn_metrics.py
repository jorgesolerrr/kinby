"""Derive one metrics record for every closed turn in an event log."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import NewType
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
from kinby.core.pricing import SHIPPED_PRICES, token_cost
from kinby.instance import ModelPrice

_MEMORY_CALL_KINDS = {
    "memory_search": "search",
    "memory_open": "open",
    "remember": "remember",
    "forget": "forget",
}
_CHARACTERS_PER_ESTIMATED_MEMORY_TOKEN = 4

UnpricedModel = NewType("UnpricedModel", str)


@dataclass(frozen=True)
class TurnKey:
    thread_id: UUID
    turn_id: UUID


@dataclass
class _TurnEvents:
    started_at: datetime
    model: str
    tool_calls: Counter[str] = field(default_factory=Counter)
    memory_calls: Counter[str] = field(default_factory=Counter)
    approvals_requested: int = 0
    memory_characters: int = 0


def estimate_memory_tokens(character_count: int) -> float:
    """Apply the shared character estimate used for memory text."""
    return character_count / _CHARACTERS_PER_ESTIMATED_MEMORY_TOKEN


@dataclass(frozen=True)
class TurnMetricsResult:
    records: list[TurnMetrics]
    unpriced_models_by_turn: Mapping[TurnKey, frozenset[UnpricedModel]]


def turn_metrics(
    events: Iterable[Event],
    prices: Mapping[str, ModelPrice] = SHIPPED_PRICES,
) -> TurnMetricsResult:
    """Read event history once and return its closed turns in closing order."""
    open_turns: dict[TurnKey, _TurnEvents] = {}
    closed_turns: dict[TurnKey, TurnMetrics] = {}
    records: list[TurnMetrics] = []
    unpriced_models_by_turn: dict[TurnKey, set[UnpricedModel]] = {}

    for event in events:
        key = TurnKey(event.thread_id, event.turn_id)
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
            price = prices.get(turn.model) if turn is not None else None
            if turn is not None and price is None:
                unpriced_models_by_turn.setdefault(key, set()).add(UnpricedModel(turn.model))
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
                cost=(
                    token_cost(input_tokens, output_tokens, price) if price is not None else None
                ),
                tool_calls=dict(turn.tool_calls) if turn else {},
                memory_calls=memory_calls,
                memory_consulted=bool(memory_calls.search or memory_calls.open),
                approvals_requested=turn.approvals_requested if turn else 0,
                memory_tokens=(estimate_memory_tokens(turn.memory_characters) if turn else 0),
                rating=None,
            )
            records.append(record)
            closed_turns[key] = record
            continue

        record = closed_turns.get(key)
        if record is None:
            continue
        if isinstance(payload, MemoryRecapped):
            main_input_tokens = record.input_tokens - record.recap_input_tokens
            main_output_tokens = record.output_tokens - record.recap_output_tokens
            main_price = prices.get(record.model) if record.model is not None else None
            recap_price = prices.get(payload.model) if payload.model is not None else None
            if payload.model is not None and recap_price is None:
                unpriced_models_by_turn.setdefault(key, set()).add(UnpricedModel(payload.model))
            record.input_tokens += payload.input_tokens - record.recap_input_tokens
            record.output_tokens += payload.output_tokens - record.recap_output_tokens
            record.recap_input_tokens = payload.input_tokens
            record.recap_output_tokens = payload.output_tokens
            record.cost = (
                token_cost(main_input_tokens, main_output_tokens, main_price)
                + token_cost(payload.input_tokens, payload.output_tokens, recap_price)
                if main_price is not None and recap_price is not None
                else None
            )
        elif isinstance(payload, TurnRated):
            record.rating = payload

    return TurnMetricsResult(
        records,
        {key: frozenset(models) for key, models in unpriced_models_by_turn.items()},
    )


def _closing_kind(
    payload: TurnCompleted | TurnFailed | TurnInterrupted,
) -> TurnClosingKind:
    if isinstance(payload, TurnCompleted):
        return TurnClosingKind.COMPLETED
    if isinstance(payload, TurnFailed):
        return TurnClosingKind.FAILED
    return TurnClosingKind.INTERRUPTED
