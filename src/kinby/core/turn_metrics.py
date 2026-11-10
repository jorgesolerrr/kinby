"""Derive one metrics record for every closed turn in an event log."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from itertools import pairwise
from typing import TYPE_CHECKING, NewType
from uuid import UUID

from kinby.contracts import (
    ApprovalRequested,
    CompletionOutcome,
    DenyCounts,
    Event,
    EventType,
    GateOutcome,
    MemoryCallCounts,
    MemoryRecalled,
    MemoryRecapped,
    ModelCallMismatch,
    ModelCompleted,
    Navigation,
    NodeId,
    Origin,
    ProfileCounted,
    ProfileVersion,
    PromptVersion,
    ReportedRun,
    RunDelegated,
    RunStopped,
    ThreadCompacted,
    TokenTotals,
    ToolCall,
    ToolGated,
    ToolResult,
    ToolTime,
    TurnClosingKind,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnMetrics,
    TurnRated,
    TurnStarted,
)
from kinby.core.pricing import SHIPPED_PRICES, cache_read_cost, token_cost
from kinby.instance import ModelPrice

if TYPE_CHECKING:
    from kinby.core.events import EventReader

_MEMORY_CALL_KINDS = {
    "memory_search": "search",
    "memory_open": "open",
    "remember": "remember",
    "forget": "forget",
}
_SKILL_TOOL = "skill"
_NAVIGATION_EXCLUDED = frozenset((*_MEMORY_CALL_KINDS, _SKILL_TOOL))

UnpricedModel = NewType("UnpricedModel", str)

#: What turn_metrics folds from before a range, whatever turn it belongs to.
_STATE_EVENTS = frozenset({EventType.RUN_STOPPED, EventType.PROFILE_COUNTED})
#: The events turn_metrics folds.
METRICS_EVENTS = frozenset(
    {
        *_STATE_EVENTS,
        EventType.TURN_STARTED,
        EventType.MODEL_COMPLETED,
        EventType.TOOL_CALL,
        EventType.TOOL_GATED,
        EventType.TOOL_RESULT,
        EventType.MEMORY_RECALLED,
        EventType.APPROVAL_REQUESTED,
        EventType.RUN_DELEGATED,
        EventType.TURN_COMPLETED,
        EventType.TURN_FAILED,
        EventType.TURN_INTERRUPTED,
        EventType.MEMORY_RECAPPED,
        EventType.THREAD_COMPACTED,
        EventType.TURN_RATED,
    }
)
#: A turn's start, and the events turn_metrics folds differently once their turn started.
_JOINED_EVENTS = frozenset(
    {
        EventType.TURN_STARTED,
        EventType.TURN_COMPLETED,
        EventType.TURN_FAILED,
        EventType.TURN_INTERRUPTED,
        EventType.RUN_DELEGATED,
        EventType.MEMORY_RECALLED,
    }
)


@dataclass(frozen=True)
class TurnKey:
    thread_id: UUID
    turn_id: UUID


@dataclass
class _ModelCall:
    input_tokens: int
    output_tokens: int
    #: The tool results that came back before the next call.
    results: list[ToolResult] = field(default_factory=list)


@dataclass
class _Spend:
    """A closed turn's own calls, and the calls recorded apart under it: its recap's and its
    thread's compactions'."""

    own: TokenTotals
    #: None when the turn's model is unpriced.
    own_cost: float | None
    recap: MemoryRecapped | None = None
    compactions: list[ThreadCompacted] = field(default_factory=list)


@dataclass(frozen=True)
class _MemoryUse:
    tokens: float = 0
    carried_tokens: float = 0
    carried_cost: float | None = None
    measured: bool = False


@dataclass
class _TurnEvents:
    started_at: datetime
    model: str
    prompt_version: PromptVersion | None
    profile_version: ProfileVersion | None
    origin: Origin
    tool_calls: Counter[str] = field(default_factory=Counter)
    tool_writes: dict[str, bool | None] = field(default_factory=dict)
    denies: Counter[str] = field(default_factory=Counter)
    tool_duration: Counter[str] = field(default_factory=Counter)
    memory_calls: Counter[str] = field(default_factory=Counter)
    approvals_requested: int = 0
    returned_memory: bool = False
    model_calls: list[_ModelCall] = field(default_factory=list)
    model_input_tokens: int = 0
    model_output_tokens: int = 0
    model_cache_read_tokens: int = 0
    model_cache_creation_tokens: int = 0
    read_calls: int = 0
    reads_before_first_write: int = 0
    navigation_writes: int = 0
    read_duration_ms: int = 0
    opened_paths: Counter[str] = field(default_factory=Counter)
    read_call_ids: set[str] = field(default_factory=set)
    tokens_before_first_write: int = 0
    delegated_runs: list[ReportedRun] = field(default_factory=list)
    recalled: dict[NodeId, None] = field(default_factory=dict)
    compactions: list[ThreadCompacted] = field(default_factory=list)


def closing_day(record: TurnMetrics) -> date:
    """Return the UTC day on which a turn closed."""
    return record.closed_at.astimezone(UTC).date()


@dataclass(frozen=True)
class StepRun:
    """A delegated run no turn reported: a factory step's coding client run, under its run."""

    run_id: UUID
    reported: ReportedRun


@dataclass(frozen=True)
class RunRecap:
    """The recap of a factory run's stop. Its cost is None when its model is unpriced."""

    run_id: UUID
    timestamp: datetime
    tokens: TokenTotals
    cost: float | None


@dataclass(frozen=True)
class RunRecall:
    """A recall no turn made: a factory run's coding session read *nodes*, under its run."""

    run_id: UUID
    timestamp: datetime
    nodes: tuple[NodeId, ...]


@dataclass(frozen=True)
class TurnMetricsResult:
    records: list[TurnMetrics]
    unpriced_models_by_turn: Mapping[TurnKey, frozenset[UnpricedModel]]
    warnings: list[ModelCallMismatch]
    step_runs: list[StepRun]
    run_recaps: list[RunRecap]
    run_recalls: list[RunRecall]


def metrics_events(reader: EventReader, since: datetime | None) -> Iterator[Event]:
    """The events turn_metrics folds for what happened from *since* on: those stored since then,
    after the runs stopped and the profiles counted before it, and what each turn that started
    before it stored then."""
    if since is None:
        yield from reader.events_of(METRICS_EVENTS)
        return
    yield from reader.events_of(_STATE_EVENTS, until=since)
    started: set[TurnKey] = set()
    earlier: dict[TurnKey, None] = {}
    for event in reader.events_of(_JOINED_EVENTS, since=since):
        key = TurnKey(event.thread_id, event.turn_id)
        if isinstance(event.payload, TurnStarted):
            started.add(key)
        elif key not in started:
            earlier[key] = None
    for key in earlier:
        yield from (
            event
            for event in reader.turn_events(key.turn_id)
            if event.thread_id == key.thread_id
            and event.timestamp < since
            and event.payload.type in METRICS_EVENTS - _STATE_EVENTS
        )
    yield from reader.events_of(METRICS_EVENTS, since=since)


def turn_metrics(
    events: Iterable[Event],
    prices: Mapping[str, ModelPrice] = SHIPPED_PRICES,
) -> TurnMetricsResult:
    """Read event history once and return its closed turns in closing order."""
    open_turns: dict[TurnKey, _TurnEvents] = {}
    closed_turns: dict[TurnKey, TurnMetrics] = {}
    spends: dict[TurnKey, _Spend] = {}
    records: list[TurnMetrics] = []
    unpriced_models_by_turn: dict[TurnKey, set[UnpricedModel]] = {}
    mismatches: list[ModelCallMismatch] = []
    step_runs: list[StepRun] = []
    run_recaps: list[RunRecap] = []
    run_recalls: list[RunRecall] = []
    stopped_runs: set[UUID] = set()
    profile_sizes: dict[ProfileVersion, int] = {}
    #: Records that read a profile, each sized once every count has been read.
    profiled: list[tuple[TurnMetrics, ProfileVersion]] = []

    for event in events:
        key = TurnKey(event.thread_id, event.turn_id)
        payload = event.payload
        if isinstance(payload, TurnStarted):
            open_turns[key] = _TurnEvents(
                event.timestamp,
                payload.model,
                payload.prompt_version,
                payload.profile_version,
                payload.origin,
            )
            continue
        if isinstance(payload, ProfileCounted):
            profile_sizes[payload.version] = payload.tokens
            continue
        if isinstance(payload, RunStopped):
            stopped_runs.add(event.thread_id)
            continue

        turn = open_turns.get(key)
        if isinstance(payload, ModelCompleted):
            if turn is not None:
                turn.model_calls.append(_ModelCall(payload.input_tokens, payload.output_tokens))
                turn.model_input_tokens += payload.input_tokens
                turn.model_output_tokens += payload.output_tokens
                turn.model_cache_read_tokens += payload.cache_read_tokens
                turn.model_cache_creation_tokens += payload.cache_creation_tokens
                if turn.navigation_writes == 0:
                    turn.tokens_before_first_write += payload.input_tokens + payload.output_tokens
            continue
        if isinstance(payload, ToolCall):
            if turn is not None:
                turn.tool_calls[payload.name] += 1
                turn.tool_writes[payload.call_id] = payload.write
                if kind := _MEMORY_CALL_KINDS.get(payload.name):
                    turn.memory_calls[kind] += 1
                _record_navigation_call(turn, payload)
            continue
        if isinstance(payload, ToolGated):
            if turn is not None and payload.action is GateOutcome.DENY:
                turn.denies[payload.decided_by.value] += 1
            continue
        if isinstance(payload, ToolResult):
            if turn is not None:
                turn.returned_memory |= payload.name in _MEMORY_CALL_KINDS
                if turn.model_calls:
                    turn.model_calls[-1].results.append(payload)
                write = turn.tool_writes.get(payload.call_id)
                if payload.duration_ms is not None and write is not None:
                    turn.tool_duration["write_ms" if write else "read_ms"] += payload.duration_ms
                if payload.call_id in turn.read_call_ids and payload.duration_ms is not None:
                    turn.read_duration_ms += payload.duration_ms
            continue
        if isinstance(payload, MemoryRecalled):
            if turn is not None:
                turn.recalled |= dict.fromkeys(payload.nodes)
            else:
                run_recalls.append(RunRecall(event.thread_id, event.timestamp, payload.nodes))
            continue
        if isinstance(payload, ApprovalRequested):
            if turn is not None:
                turn.approvals_requested += 1
            continue
        if isinstance(payload, ThreadCompacted) and turn is not None:
            turn.compactions.append(payload)
            continue
        if isinstance(payload, RunDelegated):
            reported = ReportedRun(timestamp=event.timestamp, run=payload.run)
            if turn is not None:
                turn.delegated_runs.append(reported)
            else:
                step_runs.append(StepRun(event.thread_id, reported))
            continue
        if isinstance(payload, TurnCompleted | TurnFailed | TurnInterrupted):
            turn = open_turns.pop(key, None)
            input_tokens = payload.input_tokens
            output_tokens = payload.output_tokens
            outcome = (
                payload.outcome if isinstance(payload, TurnCompleted) else CompletionOutcome.WORK
            )
            no_work = outcome is CompletionOutcome.NO_WORK
            if (
                turn is not None
                and turn.model_calls
                and not no_work
                and _model_totals(turn)
                != TokenTotals(
                    input_tokens=payload.input_tokens,
                    output_tokens=payload.output_tokens,
                    cache_read_tokens=payload.cache_read_tokens,
                    cache_creation_tokens=payload.cache_creation_tokens,
                )
            ):
                mismatches.append(
                    ModelCallMismatch(
                        thread_id=event.thread_id,
                        turn_id=event.turn_id,
                    )
                )
            memory_calls = MemoryCallCounts.model_validate(turn.memory_calls if turn else {})
            price = prices.get(turn.model) if turn is not None else None
            if turn is not None and price is None and not no_work:
                unpriced_models_by_turn.setdefault(key, set()).add(UnpricedModel(turn.model))
            memory = _memory_use(turn, price) if turn is not None else _MemoryUse()
            own_cost = 0 if no_work else token_cost(payload, price) if price is not None else None
            record = TurnMetrics(
                thread_id=event.thread_id,
                turn_id=event.turn_id,
                model=turn.model if turn else None,
                prompt_version=turn.prompt_version if turn else None,
                profile_version=turn.profile_version if turn else None,
                origin=turn.origin if turn else None,
                closing_kind=_closing_kind(payload),
                outcome=outcome,
                started_at=turn.started_at if turn else None,
                closed_at=event.timestamp,
                duration_seconds=(
                    (event.timestamp - turn.started_at).total_seconds() if turn else None
                ),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=payload.cache_read_tokens,
                cache_creation_tokens=payload.cache_creation_tokens,
                recap_input_tokens=0,
                recap_output_tokens=0,
                cost=own_cost,
                tool_calls=dict(turn.tool_calls) if turn else {},
                memory_calls=memory_calls,
                memory_consulted=bool(memory_calls.search or memory_calls.open),
                approvals_requested=turn.approvals_requested if turn else 0,
                denies=DenyCounts.model_validate(turn.denies if turn else {}),
                tool_duration=ToolTime.model_validate(turn.tool_duration if turn else {}),
                memory_tokens=memory.tokens,
                memory_carried_tokens=memory.carried_tokens,
                memory_carried_cost=memory.carried_cost,
                memory_measured=memory.measured,
                rating=None,
                navigation=_navigation(turn) if turn else Navigation(),
                delegated_runs=turn.delegated_runs if turn else [],
                recalled=list(turn.recalled) if turn else [],
            )
            records.append(record)
            if turn is not None and turn.model_calls and turn.profile_version is not None:
                profiled.append((record, turn.profile_version))
            closed_turns[key] = record
            spend = spends[key] = _Spend(
                payload, own_cost, compactions=turn.compactions if turn is not None else []
            )
            if spend.compactions:
                for compaction in spend.compactions:
                    if (unpriced := _unpriced(compaction, prices)) is not None:
                        unpriced_models_by_turn.setdefault(key, set()).add(unpriced)
                _settle(record, spend, prices)
            continue

        record = closed_turns.get(key)
        if record is None:
            if isinstance(payload, MemoryRecapped) and event.thread_id in stopped_runs:
                cost = _call_cost(payload, prices)
                run_recaps.append(RunRecap(event.thread_id, event.timestamp, payload, cost))
            continue
        if isinstance(payload, MemoryRecapped | ThreadCompacted):
            spend = spends[key]
            if isinstance(payload, ThreadCompacted):
                spend.compactions.append(payload)
            elif record.outcome is CompletionOutcome.NO_WORK:
                continue
            else:
                spend.recap = payload
            if (unpriced := _unpriced(payload, prices)) is not None:
                unpriced_models_by_turn.setdefault(key, set()).add(unpriced)
            _settle(record, spend, prices)
        elif isinstance(payload, TurnRated):
            record.rating = payload

    for record, version in profiled:
        if version in profile_sizes:
            record.memory_tokens += profile_sizes[version]
        else:
            record.memory_measured = False

    return TurnMetricsResult(
        records,
        {key: frozenset(models) for key, models in unpriced_models_by_turn.items()},
        mismatches,
        step_runs,
        run_recaps,
        run_recalls,
    )


def _settle(record: TurnMetrics, spend: _Spend, prices: Mapping[str, ModelPrice]) -> None:
    """Total *record* over its turn's own calls and those recorded apart under it."""
    later = [*([spend.recap] if spend.recap is not None else []), *spend.compactions]
    calls = [spend.own, *later]
    record.input_tokens = sum(call.input_tokens for call in calls)
    record.output_tokens = sum(call.output_tokens for call in calls)
    record.cache_read_tokens = sum(call.cache_read_tokens for call in calls)
    record.cache_creation_tokens = sum(call.cache_creation_tokens for call in calls)
    record.recap_input_tokens = spend.recap.input_tokens if spend.recap is not None else 0
    record.recap_output_tokens = spend.recap.output_tokens if spend.recap is not None else 0
    record.compaction_input_tokens = sum(call.input_tokens for call in spend.compactions)
    record.compaction_output_tokens = sum(call.output_tokens for call in spend.compactions)
    costs = [spend.own_cost, *(_call_cost(call, prices) for call in later)]
    priced = [cost for cost in costs if cost is not None]
    record.cost = sum(priced) if len(priced) == len(costs) else None


def _call_cost(
    call: MemoryRecapped | ThreadCompacted, prices: Mapping[str, ModelPrice]
) -> float | None:
    """A recap's or a compaction's cost at its model: nothing when it spent no tokens, None when
    unpriced."""
    price = prices.get(call.model) if call.model is not None else None
    if price is not None:
        return token_cost(call, price)
    if not (call.total or call.cache_read_tokens or call.cache_creation_tokens):
        return 0
    return None


def _unpriced(
    call: MemoryRecapped | ThreadCompacted, prices: Mapping[str, ModelPrice]
) -> UnpricedModel | None:
    """The model of a recap or a compaction that spent tokens at no known price."""
    if _call_cost(call, prices) is None and call.model is not None:
        return UnpricedModel(call.model)
    return None


def _memory_use(turn: _TurnEvents, price: ModelPrice | None) -> _MemoryUse:
    """Count each step's memory by how much the next model call's input grew."""
    tokens = carried = 0.0
    calls = turn.model_calls
    measured = bool(calls) or not turn.returned_memory
    for index, (call, following) in enumerate(pairwise(calls)):
        grown = following.input_tokens - call.input_tokens - call.output_tokens
        if grown < 0:
            # The next input left out some of this call's output, such as hidden reasoning.
            measured = False
            continue
        step = grown * _memory_share(call.results)
        tokens += step
        carried += step * (len(calls) - index - 2)
    return _MemoryUse(
        tokens=tokens,
        carried_tokens=carried,
        carried_cost=cache_read_cost(carried, price) if price is not None else None,
        measured=measured,
    )


def _memory_share(results: list[ToolResult]) -> float:
    """All of a memory-only step, or memory's share of a mixed step's characters."""
    memory = [result for result in results if result.name in _MEMORY_CALL_KINDS]
    if not memory:
        return 0
    if len(memory) == len(results):
        return 1
    characters = sum(len(result.output) for result in results)
    return sum(len(result.output) for result in memory) / characters if characters else 0


def _record_navigation_call(turn: _TurnEvents, payload: ToolCall) -> None:
    if payload.name in _NAVIGATION_EXCLUDED:
        return
    if payload.write is False:
        turn.read_calls += 1
        turn.read_call_ids.add(payload.call_id)
        path = payload.arguments.get("path")
        if isinstance(path, str):
            turn.opened_paths[path] += 1
        if turn.navigation_writes == 0:
            turn.reads_before_first_write += 1
    elif payload.write is True:
        turn.navigation_writes += 1


def _navigation(turn: _TurnEvents) -> Navigation:
    distinct_paths = len(turn.opened_paths)
    return Navigation(
        read_calls=turn.read_calls,
        reads_before_first_write=turn.reads_before_first_write,
        write_calls=turn.navigation_writes,
        duration_ms=turn.read_duration_ms,
        distinct_paths=distinct_paths,
        repeat_opens=sum(turn.opened_paths.values()) - distinct_paths,
        tokens_before_first_write=turn.tokens_before_first_write,
    )


def _model_totals(turn: _TurnEvents) -> TokenTotals:
    return TokenTotals(
        input_tokens=turn.model_input_tokens,
        output_tokens=turn.model_output_tokens,
        cache_read_tokens=turn.model_cache_read_tokens,
        cache_creation_tokens=turn.model_cache_creation_tokens,
    )


def _closing_kind(
    payload: TurnCompleted | TurnFailed | TurnInterrupted,
) -> TurnClosingKind:
    if isinstance(payload, TurnCompleted):
        return TurnClosingKind.COMPLETED
    if isinstance(payload, TurnFailed):
        return TurnClosingKind.FAILED
    return TurnClosingKind.INTERRUPTED
