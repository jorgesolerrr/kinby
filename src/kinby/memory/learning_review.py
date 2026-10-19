"""The learning review, the dreaming stage that judges whether the instance learned from its
memory (ADR 0101)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import TypeAdapter

from kinby.contracts import (
    Event,
    FactoryRunOrigin,
    MemoryRecalled,
    NodeId,
    Origin,
    RoutineOrigin,
    RunStop,
    RunStopped,
    StepAttempt,
    ToolResult,
    TurnRated,
    TurnStarted,
    TurnVerdict,
)
from kinby.memory.facade import Episode, Fact, RecappedRun, RecappedTurn
from kinby.plugins.tools import Tool, tool

if TYPE_CHECKING:
    from kinby.core.events import EventLog
    from kinby.memory.graph import GraphStore

_WEEK = timedelta(days=7)
#: How much of a failed tool's output the frame keeps, from the end, where it says why it failed.
_OUTPUT_TAIL = 2_000

_RULES = (
    "Each turn and factory run below went badly this week: a tool failed, a step ran again "
    "after a retry or a send-back, or the user rated a turn bad.",
    "A repeated mistake: the turn or run went wrong in a way a matching fact already "
    "described, and it did not recall that fact.",
    "An ignored lesson: the turn or run recalled a fact and then did what the fact warned against.",
    "Name only these two verdicts, and only where the facts support them. A bad week may "
    "hold neither.",
)
_CONTRACT = (
    "Write nothing and propose nothing. Your final message is the dream report: each "
    "repeated mistake and ignored lesson, citing the turn by its thread and turn ids or the "
    "run by its run id, and the fact by its node id."
)


@dataclass(frozen=True)
class _FailedTool:
    tool: str
    output: str


@dataclass
class _TurnLog:
    """What the event log holds of one turn."""

    thread: UUID
    turn: UUID
    at: datetime
    message: str
    origin: Origin
    failed_tools: list[_FailedTool] = field(default_factory=list)
    #: The current rating: a later one replaces an earlier one.
    rating: TurnRated | None = None
    recalled: list[NodeId] = field(default_factory=list)

    @property
    def bad_rating(self) -> TurnRated | None:
        bad = self.rating is not None and self.rating.verdict is TurnVerdict.BAD
        return self.rating if bad else None

    @property
    def went_badly(self) -> bool:
        return bool(self.failed_tools) or self.bad_rating is not None


@dataclass
class _RunLog:
    """What the event log holds of one factory run: its agent steps' turns, its stops, and its
    coding sessions' recalls."""

    turns: list[_TurnLog] = field(default_factory=list)
    stops: list[RunStop] = field(default_factory=list)
    recalled: list[NodeId] = field(default_factory=list)

    @property
    def attempts(self) -> list[StepAttempt]:
        return [attempt for stop in self.stops for attempt in stop.attempts]

    @property
    def started(self) -> datetime:
        return min(
            [turn.at for turn in self.turns] + [attempt.started_at for attempt in self.attempts]
        )

    @property
    def went_badly(self) -> bool:
        # A step's second attempt follows a retry or a send-back.
        ran_again = any(attempt.attempt > 1 for attempt in self.attempts)
        return ran_again or any(turn.went_badly for turn in self.turns)


@dataclass(frozen=True)
class _BadTurn:
    thread: UUID
    turn: UUID
    at: datetime
    message: str
    failed_tools: tuple[_FailedTool, ...]
    rating: TurnRated | None
    episodes: tuple[Episode, ...]
    matching_facts: tuple[Fact, ...]
    recalled_facts: tuple[Fact, ...]


@dataclass(frozen=True)
class _BadRun:
    run: UUID
    attempts: tuple[StepAttempt, ...]
    failed_tools: tuple[_FailedTool, ...]
    bad_ratings: tuple[TurnRated, ...]
    episodes: tuple[Episode, ...]
    matching_facts: tuple[Fact, ...]
    recalled_facts: tuple[Fact, ...]


@dataclass(frozen=True)
class _ReviewFrame:
    rules: tuple[str, ...]
    contract: str
    turns: tuple[_BadTurn, ...]
    runs: tuple[_BadRun, ...]


_FRAME = TypeAdapter(_ReviewFrame)


def learning_review_step(graph: GraphStore, log: EventLog, *, today: Callable[[], date]) -> Tool:
    """The stage's code step: frame the week before *today*'s turns and factory runs that went
    badly, or report no work."""

    @tool(write=False)
    def learning_review() -> str | None:
        """Frame the week's turns and factory runs that went badly, each with the facts a memory
        search matches for it and the facts it recalled."""
        until = today()
        turns, runs = _read(log.all_events(), since=until - _WEEK, until=until)
        bad_turns = [turn for turn in turns if turn.went_badly]
        bad_runs = {run_id: run for run_id, run in runs.items() if run.went_badly}
        if not bad_turns and not bad_runs:
            return None
        nodes = graph.nodes()
        facts = {node.node: node for node in nodes if isinstance(node, Fact)}
        episodes = [node for node in nodes if isinstance(node, Episode)]

        def matching(recaps: tuple[Episode, ...], started: datetime) -> tuple[Fact, ...]:
            # A fact holds only the day it was learned, so one from the work's own day may have
            # come after it, and is left out.
            subjects = dict.fromkeys(subject for recap in recaps for subject in recap.subjects)
            hits = dict.fromkeys(hit.node for subject in subjects for hit in graph.recall(subject))
            return tuple(
                facts[node] for node in hits if node in facts and facts[node].date < started.date()
            )

        def recalled(nodes: Iterable[NodeId]) -> tuple[Fact, ...]:
            return tuple(facts[node] for node in dict.fromkeys(nodes) if node in facts)

        def frame_turn(turn: _TurnLog) -> _BadTurn:
            recaps = tuple(
                episode
                for episode in episodes
                if episode.recaps == RecappedTurn(turn.thread, turn.turn)
            )
            return _BadTurn(
                thread=turn.thread,
                turn=turn.turn,
                at=turn.at,
                message=turn.message,
                failed_tools=tuple(turn.failed_tools),
                rating=turn.bad_rating,
                episodes=recaps,
                matching_facts=matching(recaps, turn.at),
                recalled_facts=recalled(turn.recalled),
            )

        def frame_run(run_id: UUID, run: _RunLog) -> _BadRun:
            recaps = tuple(
                episode
                for episode in episodes
                if isinstance(episode.recaps, RecappedRun) and episode.recaps.run == run_id
            )
            return _BadRun(
                run=run_id,
                attempts=tuple(run.attempts),
                failed_tools=tuple(failed for turn in run.turns for failed in turn.failed_tools),
                bad_ratings=tuple(
                    rating for turn in run.turns if (rating := turn.bad_rating) is not None
                ),
                episodes=recaps,
                matching_facts=matching(recaps, run.started),
                recalled_facts=recalled(
                    [*(node for turn in run.turns for node in turn.recalled), *run.recalled]
                ),
            )

        frame = _ReviewFrame(
            rules=_RULES,
            contract=_CONTRACT,
            turns=tuple(frame_turn(turn) for turn in bad_turns),
            runs=tuple(frame_run(run_id, run) for run_id, run in bad_runs.items()),
        )
        return _FRAME.dump_json(frame, indent=2).decode()

    return learning_review


def _read(
    events: Iterable[Event], *, since: date, until: date
) -> tuple[list[_TurnLog], dict[UUID, _RunLog]]:
    """The work turns that started from *since* until before *until*, and the factory runs with
    a turn, a stop or a recall in that time. A dreaming stage's turns are not work."""
    turns: dict[tuple[UUID, UUID], _TurnLog] = {}
    runs: defaultdict[UUID, _RunLog] = defaultdict(_RunLog)
    for event in events:
        turn = turns.get((event.thread_id, event.turn_id))
        in_week = since <= event.timestamp.date() < until
        match event.payload:
            case TurnStarted(message=message, origin=origin) if in_week:
                turns[event.thread_id, event.turn_id] = _TurnLog(
                    event.thread_id, event.turn_id, event.timestamp, message, origin
                )
            case ToolResult(error=True, name=name, output=output) if turn is not None:
                turn.failed_tools.append(_FailedTool(name, output[-_OUTPUT_TAIL:]))
            case TurnRated() as rating if turn is not None:
                turn.rating = rating
            case MemoryRecalled(nodes=nodes) if turn is not None:
                turn.recalled.extend(nodes)
            case MemoryRecalled(nodes=nodes, step=step) if step is not None and in_week:
                # A coding session's recall, on its run's thread.
                runs[event.thread_id].recalled.extend(nodes)
            case RunStopped(stop=stop) if in_week:
                runs[event.thread_id].stops.append(stop)
    work: list[_TurnLog] = []
    for turn in turns.values():
        match turn.origin:
            case FactoryRunOrigin(run_id=run_id):
                runs[run_id].turns.append(turn)
            case RoutineOrigin(dreaming_stage=True):
                pass
            case _:
                work.append(turn)
    return work, dict(runs)
