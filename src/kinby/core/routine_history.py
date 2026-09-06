"""Fold routine firings and failure-policy markers from the event log."""

from collections.abc import Iterable
from dataclasses import dataclass, field

from kinby.contracts import (
    ApprovalRequested,
    CompletionOutcome,
    Event,
    MessageDelta,
    RoutineFailureHandled,
    RoutineLastRun,
    RoutineName,
    RoutineNotice,
    RoutineOrigin,
    RoutineRunOutcome,
    ToolCall,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnStarted,
    is_turn_closing,
)
from kinby.core.turn_metrics import TurnKey


@dataclass
class RoutineHistory:
    last_run: RoutineLastRun | None = None
    failure_count: int = 0
    last_failure: str | None = None
    notices: list[RoutineNotice] = field(default_factory=list)


@dataclass(frozen=True)
class UnhandledFailure:
    name: RoutineName
    event: Event
    count: int
    reason: str


@dataclass(frozen=True)
class RoutineHistories:
    routines: dict[RoutineName, RoutineHistory]
    failures: tuple[UnhandledFailure, ...]


def routine_history(events: Iterable[Event]) -> RoutineHistories:
    histories: dict[RoutineName, RoutineHistory] = {}
    runs: dict[TurnKey, tuple[RoutineName, RoutineLastRun]] = {}
    texts: dict[TurnKey, str] = {}
    closed: set[TurnKey] = set()
    handled: set[TurnKey] = set()
    failures: dict[TurnKey, UnhandledFailure] = {}
    for event in events:
        key = TurnKey(event.thread_id, event.turn_id)
        payload = event.payload
        if isinstance(payload, RoutineFailureHandled):
            if key not in handled and payload.notice is not None:
                histories.setdefault(payload.name, RoutineHistory()).notices.append(payload.notice)
            handled.add(key)
        elif isinstance(payload, TurnStarted) and isinstance(payload.origin, RoutineOrigin):
            if key in runs:
                continue
            run = RoutineLastRun(
                thread_id=event.thread_id,
                turn_id=event.turn_id,
                started_at=event.timestamp,
                outcome=RoutineRunOutcome.RUNNING,
            )
            runs[key] = payload.origin.name, run
            histories.setdefault(payload.origin.name, RoutineHistory()).last_run = run
            texts[key] = ""
        elif key in runs and key not in closed:
            name, run = runs[key]
            history = histories[name]
            match payload:
                case MessageDelta(text=delta):
                    texts[key] += delta
                    run.first_line = texts[key].split("\n", 1)[0]
                case ToolCall():
                    texts[key] = ""
                    run.first_line = ""
                case ApprovalRequested():
                    run.outcome = RoutineRunOutcome.PARKED
                case TurnCompleted(outcome=outcome):
                    run.outcome = RoutineRunOutcome(outcome.value)
                    if outcome is CompletionOutcome.WORK:
                        history.failure_count = 0
                        failures = {key: f for key, f in failures.items() if f.name != name}
                case TurnFailed(message=reason):
                    run.outcome = RoutineRunOutcome.FAILED
                    history.failure_count += 1
                    history.last_failure = reason
                    failures[key] = UnhandledFailure(name, event, history.failure_count, reason)
                case TurnInterrupted():
                    run.outcome = RoutineRunOutcome.INTERRUPTED
            if is_turn_closing(payload):
                closed.add(key)
    return RoutineHistories(
        histories, tuple(f for key, f in failures.items() if key not in handled)
    )
