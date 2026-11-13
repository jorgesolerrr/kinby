"""Run memory cases through kinby's dispatcher under Inspect."""

import random
import shutil
import tempfile
import tomllib
from collections.abc import Sequence
from contextlib import aclosing
from datetime import date, timedelta
from enum import StrEnum
from itertools import product
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import ChatMessageAssistant, ModelOutput, get_model
from inspect_ai.scorer import (
    CORRECT,
    INCORRECT,
    Score,
    Scorer,
    Target,
    accuracy,
    grouped,
    mean,
    model_graded_fact,
    model_graded_qa,
    scorer,
)
from inspect_ai.solver import Generate, Solver, TaskState, solver
from langchain.embeddings import init_embeddings
from pydantic import ConfigDict, TypeAdapter, ValidationError
from pydantic.dataclasses import dataclass

from kinby.cli.client import ContractClient
from kinby.contracts import (
    THREAD_CREATE,
    THREAD_SUBSCRIBE,
    THREAD_TURN_START,
    ErrorEnvelope,
    Event,
    MemoryRecalled,
    MemoryRecapped,
    MessageDelta,
    NodeId,
    Scope,
    ThreadCreateCommand,
    ThreadSubscribeCommand,
    ThreadTurnStartCommand,
    ToolCall,
    ToolResult,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnStarted,
)
from kinby.core.dispatcher import TurnConfig, build_dispatcher
from kinby.core.events import EventLog
from kinby.core.turn_metrics import turn_metrics
from kinby.core.turn_runner import LangGraphRunner
from kinby.instance import load_instance
from kinby.instance.layout import (
    GRAPH_DIR,
    MANIFEST_NAME,
    MEMORY_DIR,
    PERMISSIONS_NAME,
    PROFILE_NAME,
    SYSTEM_NAME,
)
from kinby.instance.manifest import parse_manifest
from kinby.memory import (
    Fact,
    GraphStore,
    MemoryDelivery,
    RecapWriter,
    deliver_memory,
    embedded_pre_search,
)
from kinby.memory.delivery import DeliveredMemory
from kinby.memory.facade import new_node_id

if TYPE_CHECKING or __package__ == "evals.memory":
    from evals.memory.adapter import inspect_model_factory
else:
    # Inspect loads task files outside their package, with this directory on sys.path.
    from adapter import inspect_model_factory

_CASES_DIR = Path(__file__).parent / "cases"
_JUDGE_MODEL = "openai/gpt-5-mini"
_CHARACTERS_PER_TOKEN = 4
_FACT_GRADER_TEMPLATE = """
You are comparing a submitted answer to a reference answer for a personal memory question.

[BEGIN DATA]
************
[Question]: {question}
************
[Reference]: {criterion}
************
[Submission]: {answer}
************
[END DATA]

Compare the factual content of the submitted answer with the reference answer. Treat "I",
"you", "we", and "Jorge" as the same participant. Do not require the submission to repeat
facts already supplied by the question. Ignore differences in style, grammar, or punctuation.

Does the submission contain the reference answer's facts without contradicting them?

{instructions}
"""
_LESSON_GRADER_TEMPLATE = """
You are grading whether a teammate's work followed a lesson from its memory.

[BEGIN DATA]
************
[Work asked for]: {question}
************
[Following the lesson]: {criterion}
************
[The mistake the lesson warns against]: {mistake}
************
[Submission]: {answer}
************
[END DATA]

The submission follows the lesson when its work does what "Following the lesson" describes.
A submission that makes the mistake does not follow the lesson, even when it also mentions the
lesson or does the right thing elsewhere. A submission that only asks a question or does no work
does not follow it. Ignore style and anything the work does besides.

Does the submission follow the lesson?

{instructions}
"""
_SCOPES = tuple(Scope)
_BOTH_ARMS = "both"
_LESSONS_DIR = Path(__file__).parent / "lessons"
#: Every lesson case runs with its memory holding about this many facts.
_FACT_COUNTS = (10, 100, 500)
_PULL_LINE = "Before you start a task, search your memory for it with `memory_search`."
_UNRELATED_PEOPLE = (
    "Maya",
    "Sam",
    "Elena",
    "Tomás",
    "Priya",
    "Owen",
    "Lucía",
    "Kenji",
    "Ruth",
    "Ines",
)
_UNRELATED_ERRANDS = (
    "waters the garden plot",
    "returns the library books",
    "collects the dry cleaning",
    "walks the neighbour's dog",
    "calls the plumber",
    "takes a piano lesson",
    "buys groceries at North Market",
    "picks up the film prints",
    "swims at the lido",
    "visits the farmers market",
)
_UNRELATED_DAYS = (
    "Mondays",
    "Tuesdays",
    "Wednesdays",
    "Thursdays",
    "Fridays",
    "Saturdays",
    "Sundays",
)
#: The unrelated facts were learned over the year up to this day.
_UNRELATED_LAST_DATE = date(2026, 11, 1)


class MemoryEvalError(RuntimeError):
    """Report a failure while running a memory eval case."""


class MemoryArm(StrEnum):
    GRAPH = "graph"
    STUFFING = "stuffing"


class LessonArm(StrEnum):
    PULL = "pull"
    INDEX = "index"
    PRE_SEARCH = "pre-search"
    EMBEDDED_PRE_SEARCH = "embedded-pre-search"
    #: The lesson is removed from memory and nothing is delivered: the base rate to beat.
    CONTROL = "control"


#: How each lesson arm's runtime brings memory to the turn. Pull leaves it to the agent, and the
#: runtime has no embeddings, so the eval runs the embedded pre-search itself.
_DELIVERIES = {
    LessonArm.PULL: MemoryDelivery.NONE,
    LessonArm.INDEX: MemoryDelivery.INDEX,
    LessonArm.PRE_SEARCH: MemoryDelivery.PRE_SEARCH,
    LessonArm.EMBEDDED_PRE_SEARCH: MemoryDelivery.NONE,
    LessonArm.CONTROL: MemoryDelivery.NONE,
}


class MemoryExpectation(StrEnum):
    OPEN_NODE = "open-node"
    REMEMBER_RECALL = "remember-recall"
    FORGET = "forget"


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class MemoryTurn:
    thread: str
    message: str


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class MemoryCase:
    name: str
    turns: tuple[MemoryTurn, ...]
    reference_answer: str
    must_pass: bool
    expectation: MemoryExpectation = MemoryExpectation.OPEN_NODE
    expected_node: NodeId | None = None


@dataclass(frozen=True, config=ConfigDict(extra="ignore"))
class MemoryOpenResult:
    node: NodeId


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class LessonCase:
    name: str
    #: Ordinary work that invites the mistake the case's lesson warns against.
    work: str
    followed: str
    mistake: str


_MEMORY_CASE = TypeAdapter(MemoryCase)
_LESSON_CASE = TypeAdapter(LessonCase)
_MEMORY_OPEN_RESULT = TypeAdapter(MemoryOpenResult)


@task
def memory(arm: str = _BOTH_ARMS) -> Task:
    """Return the memory eval task for both arms or one selected arm."""
    selected_arms = tuple(MemoryArm) if arm == _BOTH_ARMS else (MemoryArm(arm),)
    return Task(
        dataset=MemoryDataset(
            [
                _sample(path, selected_arm)
                for path in sorted(_CASES_DIR.iterdir())
                if path.is_dir()
                for selected_arm in selected_arms
            ]
        ),
        solver=run_memory_case(),
        scorer=(
            model_graded_fact(template=_FACT_GRADER_TEMPLATE, model=_JUDGE_MODEL),
            memory_behavior(),
            memory_tokens(),
        ),
        metadata={"arms": [selected_arm.value for selected_arm in selected_arms]},
    )


@task
def lessons(arm: str = LessonArm.PULL) -> Task:
    """Return the lesson cases under one arm, each at every fact count."""
    selected_arm = LessonArm(arm)
    case_paths = [path for path in sorted(_LESSONS_DIR.iterdir()) if path.is_dir()]
    if selected_arm is LessonArm.EMBEDDED_PRE_SEARCH:
        unset = [path.name for path in case_paths if _embed_model(path / "instance") is None]
        if unset:
            raise MemoryEvalError(
                "Skipped the embedded-pre-search arm: models.embed is not set in the "
                f"kinby.toml of {', '.join(unset)}. The other arms run without it."
            )
    return Task(
        dataset=MemoryDataset(
            [_lesson_sample(path, facts) for path in case_paths for facts in _FACT_COUNTS]
        ),
        solver=run_lesson_case(selected_arm),
        scorer=(
            model_graded_qa(template=_LESSON_GRADER_TEMPLATE, model=_JUDGE_MODEL),
            lesson_recalled(),
            memory_tokens(),
        ),
        # The mean of a grade is the lesson-following rate, or the lesson's recall rate.
        metrics=[grouped(mean(), "facts")],
        metadata={"arm": selected_arm.value},
    )


@scorer(metrics=[accuracy()])
def memory_behavior() -> Scorer:
    async def score(state: TaskState, target: Target) -> Score:
        del target
        passed = state.metadata.get("memory_behavior")
        if not isinstance(passed, bool):
            raise TypeError("The memory sample has no memory-behavior result.")
        explanation = state.metadata.get("memory_behavior_explanation")
        if not isinstance(explanation, str):
            raise TypeError("The memory sample has no memory-behavior explanation.")
        return Score(
            value=CORRECT if passed else INCORRECT,
            explanation=explanation,
        )

    return score


@scorer(metrics=[mean()])
def lesson_recalled() -> Scorer:
    """Whether the turn recalled the lesson: opened it, or had it pre-searched into its message.

    A search hit shows only the lesson's description and the index only lists it, so neither
    counts.
    """

    async def score(state: TaskState, target: Target) -> Score:
        del target
        recalled = state.metadata.get("lesson_recalled")
        if not isinstance(recalled, bool):
            raise TypeError("The lesson sample has no lesson-recall result.")
        return Score(value=CORRECT if recalled else INCORRECT)

    return score


@scorer(metrics=[mean()])
def memory_tokens() -> Scorer:
    async def score(state: TaskState, target: Target) -> Score:
        del target
        value = state.metadata.get("memory_tokens")
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise TypeError("The memory sample has no memory-token estimate.")
        return Score(value=value)

    return score


@solver
def run_memory_case() -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        del generate
        case_path = _case_path(state)
        case = _load_case(case_path)
        arm = _arm(state)
        with tempfile.TemporaryDirectory(prefix="kinby-memory-eval-") as temporary:
            instance_path = Path(temporary) / "instance"
            _prepare_instance(case_path, instance_path, arm)
            events, answer = await _run_turns(instance_path, case.turns)
            last_turn = _last_turn(events)
            state.metadata["memory_tokens"] = _memory_token_estimate(
                instance_path,
                events,
                last_turn,
            )
            behavior, explanation = _memory_behavior(
                case,
                arm,
                instance_path,
                events,
                last_turn,
            )
        state.output = ModelOutput.from_content(model=str(state.model), content=answer)
        state.messages.append(ChatMessageAssistant(content=answer, source="generate"))
        trace = [
            event
            for event in events
            if isinstance(event.payload, (ToolCall, ToolResult, MemoryRecapped))
        ]
        state.metadata["event_log"] = [event.model_dump(mode="json") for event in trace]
        state.metadata["memory_behavior"] = behavior
        state.metadata["memory_behavior_explanation"] = explanation
        state.completed = True
        return state

    return solve


@solver
def run_lesson_case(arm: LessonArm) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        del generate
        delivery = _DELIVERIES[arm]
        case_path = _case_path(state)
        with tempfile.TemporaryDirectory(prefix="kinby-lesson-eval-") as temporary:
            instance_path = Path(temporary) / "instance"
            _prepare_lesson(case_path, instance_path, _facts(state), arm)
            # Read before the turn, as the turn reads it when it starts.
            delivered = (
                await _embedded_pre_search(instance_path, state.input_text)
                if arm is LessonArm.EMBEDDED_PRE_SEARCH
                else deliver_memory(delivery, GraphStore(instance_path), state.input_text)
            )
            message = state.input_text
            recalled: tuple[NodeId, ...] = ()
            if arm is LessonArm.EMBEDDED_PRE_SEARCH and delivered is not None:
                # As a pre-search turn puts the facts after its message and recalls them.
                message = f"{message}\n\n{delivered.text}"
                recalled = delivered.recalled
            events, answer = await _run_turns(
                instance_path, (MemoryTurn(thread="work", message=message),), delivery, recalled
            )
            delivered_tokens = (
                0 if delivered is None else len(delivered.text) / _CHARACTERS_PER_TOKEN
            )
            last_turn = _last_turn(events)
            state.metadata["memory_tokens"] = (
                _memory_token_estimate(instance_path, events, last_turn) + delivered_tokens
            )
        opened = {
            _opened_node(result)
            for _, result in _successful_tool_uses(events, last_turn, "memory_open")
        }
        pre_searched = () if delivered is None else delivered.recalled
        state.metadata["lesson_recalled"] = not _lesson_nodes(case_path).isdisjoint(
            {*opened, *pre_searched}
        )
        state.output = ModelOutput.from_content(model=str(state.model), content=answer)
        state.messages.append(ChatMessageAssistant(content=answer, source="generate"))
        state.completed = True
        return state

    return solve


async def _embedded_pre_search(instance_path: Path, task: str) -> DeliveredMemory | None:
    model = _embed_model(instance_path)
    if model is None:
        raise MemoryEvalError("The embedded-pre-search arm needs models.embed.")
    return await embedded_pre_search(GraphStore(instance_path), task, init_embeddings(model))


def _embed_model(instance_path: Path) -> str | None:
    manifest = (instance_path / MANIFEST_NAME).read_text(encoding="utf-8")
    return parse_manifest(manifest).models.embed


def _prepare_lesson(case_path: Path, instance_path: Path, facts: int, arm: LessonArm) -> None:
    shutil.copytree(case_path / "instance", instance_path)
    # No one answers an approval in the eval, so a write is denied rather than left waiting.
    (instance_path / PERMISSIONS_NAME).write_text('mode = "read-only"\n', encoding="utf-8")
    _add_unrelated_facts(instance_path, facts)
    if arm is LessonArm.CONTROL:
        # After the fill, so the unrelated facts are the ones every other arm gets.
        for lesson in _lesson_nodes(case_path):
            (instance_path / MEMORY_DIR / GRAPH_DIR / f"{lesson}.md").unlink()
    if arm is not LessonArm.PULL:
        return
    system_path = instance_path / SYSTEM_NAME
    system = system_path.read_text(encoding="utf-8").rstrip("\r\n")
    system_path.write_text(f"{system}\n\n{_PULL_LINE}\n", encoding="utf-8")


def _lesson_nodes(case_path: Path) -> frozenset[NodeId]:
    """The facts of the case's fixture: its lesson."""
    return GraphStore(case_path / "instance").fact_ids()


def _add_unrelated_facts(instance_path: Path, facts: int) -> None:
    """Fill the graph up to *facts* facts with the same unrelated facts on every run."""
    store = GraphStore(instance_path)
    errands = list(product(_UNRELATED_PEOPLE, _UNRELATED_ERRANDS, _UNRELATED_DAYS))
    random.Random(0).shuffle(errands)
    for index, (person, errand, day) in enumerate(errands[: facts - len(store.fact_ids())]):
        description = f"{person} {errand} on {day}"
        learned_on = _UNRELATED_LAST_DATE - timedelta(days=index % 365)
        store.remember(
            Fact(
                node=new_node_id(learned_on, description, UUID(int=index)),
                date=learned_on,
                description=description,
                subjects=(person,),
                body=f"{description}.",
                thread=None,
            )
        )


def _prepare_instance(case_path: Path, instance_path: Path, arm: MemoryArm) -> None:
    shutil.copytree(case_path / "instance", instance_path)
    if arm is MemoryArm.GRAPH:
        return

    shutil.rmtree(instance_path / MEMORY_DIR / GRAPH_DIR)
    profile_path = instance_path / MEMORY_DIR / PROFILE_NAME
    profile = profile_path.read_text(encoding="utf-8").rstrip("\r\n")
    transcript = (case_path / "transcript.md").read_text(encoding="utf-8").strip("\r\n")
    profile_path.write_text(f"{profile}\n\n{transcript}\n", encoding="utf-8")


def _last_turn(events: Sequence[Event]) -> UUID:
    turn_id = next(
        (event.turn_id for event in reversed(events) if isinstance(event.payload, TurnStarted)),
        None,
    )
    if turn_id is None:
        raise MemoryEvalError("The memory eval recorded no started turn.")
    return turn_id


def _memory_behavior(
    case: MemoryCase,
    arm: MemoryArm,
    instance_path: Path,
    events: Sequence[Event],
    last_turn: UUID,
) -> tuple[bool, str]:
    match case.expectation:
        case MemoryExpectation.OPEN_NODE:
            expected_node = _expected_node(case)
            searched = _successful_result(events, last_turn, "memory_search") is not None
            opened = any(
                call.arguments.get("node") == expected_node
                and _opened_node(result) == expected_node
                for call, result in _successful_tool_uses(events, last_turn, "memory_open")
            )
            passed = searched and opened and _recapped(events, last_turn)
            return passed, (
                "The last turn searched memory, opened the expected node, and was recapped."
                if passed
                else "The last turn did not search memory, open the expected node, and recap."
            )
        case MemoryExpectation.REMEMBER_RECALL:
            return _remember_recall_behavior(events, last_turn)
        case MemoryExpectation.FORGET:
            return _forget_behavior(case, arm, instance_path, events, last_turn)


def _expected_node(case: MemoryCase) -> NodeId:
    if case.expected_node is None:
        raise MemoryEvalError(f'Case "{case.name}" requires an expected node.')
    return case.expected_node


def _recapped(events: Sequence[Event], turn_id: UUID) -> bool:
    return any(
        event.turn_id == turn_id and isinstance(event.payload, MemoryRecapped) for event in events
    )


def _successful_result(
    events: Sequence[Event],
    turn_id: UUID,
    name: str,
) -> ToolResult | None:
    return next(
        (result for _, result in _successful_tool_uses(events, turn_id, name)),
        None,
    )


def _successful_tool_uses(
    events: Sequence[Event],
    turn_id: UUID,
    name: str,
) -> list[tuple[ToolCall, ToolResult]]:
    pending: dict[str, ToolCall] = {}
    uses: list[tuple[ToolCall, ToolResult]] = []
    for event in events:
        if event.turn_id != turn_id:
            continue
        payload = event.payload
        if isinstance(payload, ToolCall) and payload.name == name:
            pending[payload.call_id] = payload
        elif isinstance(payload, ToolResult) and payload.name == name:
            call = pending.pop(payload.call_id, None)
            if call is not None and not payload.error:
                uses.append((call, payload))
    return uses


def _opened_node(result: ToolResult) -> NodeId | None:
    try:
        return _MEMORY_OPEN_RESULT.validate_json(result.output).node
    except ValidationError:
        return None


def _started_turns(events: Sequence[Event]) -> list[Event]:
    return [event for event in events if isinstance(event.payload, TurnStarted)]


def _drained_before_last_turn(events: Sequence[Event], prior_turn: UUID) -> bool:
    recap_index = next(
        (
            index
            for index, event in enumerate(events)
            if event.turn_id == prior_turn and isinstance(event.payload, MemoryRecapped)
        ),
        None,
    )
    last_start_index = next(
        (
            index
            for index, event in reversed(list(enumerate(events)))
            if isinstance(event.payload, TurnStarted)
        ),
        None,
    )
    return (
        recap_index is not None and last_start_index is not None and recap_index < last_start_index
    )


def _remember_recall_behavior(
    events: Sequence[Event],
    last_turn: UUID,
) -> tuple[bool, str]:
    started = _started_turns(events)
    if len(started) < 2:
        return False, "The case did not run a record turn and a later recall turn."
    first = started[0]
    remembered = _successful_result(events, first.turn_id, "remember")
    searched = _successful_result(events, last_turn, "memory_search")
    fresh_thread = first.thread_id != started[-1].thread_id
    drained = _drained_before_last_turn(events, first.turn_id)
    found = remembered is not None and searched is not None and remembered.output in searched.output
    passed = fresh_thread and drained and found and _recapped(events, last_turn)
    return passed, (
        "A fresh thread found the remembered fact through memory_search "
        "after the prior recap drained."
        if passed
        else "The fresh-thread recall did not find the remembered fact "
        "after the prior recap drained."
    )


def _forget_behavior(
    case: MemoryCase,
    arm: MemoryArm,
    instance_path: Path,
    events: Sequence[Event],
    last_turn: UUID,
) -> tuple[bool, str]:
    expected_node = _expected_node(case)
    started = _started_turns(events)
    if len(started) < 2:
        return False, "The case did not run a forget turn and a later search turn."
    first = started[0]
    forgot = _successful_result(events, first.turn_id, "forget") is not None
    search = _successful_result(events, last_turn, "memory_search")
    absent = search is not None and str(expected_node) not in search.output
    node_path = instance_path / MEMORY_DIR / GRAPH_DIR / f"{expected_node}.md"
    tombstoned = node_path.is_file() and "tombstone: true\n" in node_path.read_text(
        encoding="utf-8"
    )
    drained = _drained_before_last_turn(events, first.turn_id)
    passed = (
        arm is MemoryArm.GRAPH
        and forgot
        and absent
        and tombstoned
        and drained
        and _recapped(events, last_turn)
    )
    return passed, (
        "The later search omitted the tombstoned node after the forget turn's recap drained."
        if passed
        else "The later search or fixture did not prove that the forgotten node stayed tombstoned."
    )


def _memory_token_estimate(
    instance_path: Path,
    events: Sequence[Event],
    last_turn: UUID,
) -> float:
    record = next(
        (record for record in turn_metrics(events).records if record.turn_id == last_turn),
        None,
    )
    if record is None:
        raise MemoryEvalError("The memory eval recorded no closed turn metrics.")
    profile_path = instance_path / MEMORY_DIR / PROFILE_NAME
    profile = profile_path.read_text(encoding="utf-8").strip("\r\n")
    return record.memory_tokens + len(profile) / _CHARACTERS_PER_TOKEN


def _sample(case_path: Path, arm: MemoryArm) -> Sample:
    case = _load_case(case_path)
    return Sample(
        id=f"{case.name}-{arm}",
        input=case.turns[-1].message,
        target=case.reference_answer,
        metadata={
            "arm": arm,
            "case_path": str(case_path),
            "expected_node": case.expected_node,
            "expectation": case.expectation,
            "must_pass": case.must_pass,
        },
    )


def _lesson_sample(case_path: Path, facts: int) -> Sample:
    case = _LESSON_CASE.validate_python(
        tomllib.loads((case_path / "case.toml").read_text(encoding="utf-8"))
    )
    return Sample(
        id=f"{case.name}-{facts}",
        input=case.work,
        target=case.followed,
        metadata={"case_path": str(case_path), "facts": facts, "mistake": case.mistake},
    )


def _load_case(case_path: Path) -> MemoryCase:
    return _MEMORY_CASE.validate_python(
        tomllib.loads((case_path / "case.toml").read_text(encoding="utf-8"))
    )


def _case_path(state: TaskState) -> Path:
    path = state.metadata.get("case_path")
    if not isinstance(path, str):
        raise TypeError("The memory sample has no case path.")
    return Path(path)


def _facts(state: TaskState) -> int:
    value = state.metadata.get("facts")
    if not isinstance(value, int):
        raise TypeError("The lesson sample has no fact count.")
    return value


def _arm(state: TaskState) -> MemoryArm:
    value = state.metadata.get("arm")
    if not isinstance(value, str):
        raise TypeError("The memory sample has no arm.")
    return MemoryArm(value)


async def _run_turns(
    instance_path: Path,
    turns: Sequence[MemoryTurn],
    memory_delivery: MemoryDelivery = MemoryDelivery.NONE,
    recalled: tuple[NodeId, ...] = (),
) -> tuple[list[Event], str]:
    """Run *turns* in order, each recalling the *recalled* facts its message brings."""
    instance = load_instance(instance_path)
    event_log = EventLog(instance.manifest.state_dir)
    factory = inspect_model_factory(get_model())
    runner = LangGraphRunner(instance, model_factory=factory, memory_delivery=memory_delivery)
    recap = RecapWriter(
        event_log,
        GraphStore(instance.path),
        instance,
        model_factory=factory,
    )
    dispatcher = build_dispatcher(
        instance.manifest.state_dir,
        event_log=event_log,
        turns=TurnConfig(
            runner.prepare_for_turn,
            runner.permission_ceiling,
            runner,
            recap,
        ),
    )
    client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, _SCOPES)
    threads: dict[str, UUID] = {}
    answer = ""
    for case_turn in turns:
        thread_id = threads.get(case_turn.thread)
        if thread_id is None:
            created = await client.call(
                THREAD_CREATE,
                ThreadCreateCommand(title=f"memory eval: {case_turn.thread}"),
            )
            if isinstance(created, ErrorEnvelope):
                raise MemoryEvalError(f"Could not create eval thread: {created}")
            thread_id = created.id
            threads[case_turn.thread] = thread_id
        answer = await _run_turn(client, event_log, thread_id, case_turn.message, recalled)
        await recap.drain()
    events = sorted(
        (event for thread_id in threads.values() for event in event_log.thread_events(thread_id)),
        key=lambda event: event.timestamp,
    )
    return events, answer


async def _run_turn(
    client: ContractClient,
    event_log: EventLog,
    thread_id: UUID,
    message: str,
    recalled: tuple[NodeId, ...],
) -> str:
    accepted = await client.call(
        THREAD_TURN_START,
        ThreadTurnStartCommand(thread_id=thread_id, message=message),
    )
    if isinstance(accepted, ErrorEnvelope):
        raise MemoryEvalError(f"Could not start eval turn: {accepted}")
    if recalled:
        await event_log.append(thread_id, accepted.turn_id, MemoryRecalled(nodes=recalled))

    stream = await client.subscribe(
        THREAD_SUBSCRIBE,
        ThreadSubscribeCommand(thread_id=thread_id, after_sequence=accepted.sequence),
    )
    if isinstance(stream, ErrorEnvelope):
        raise MemoryEvalError(f"Could not subscribe to eval turn: {stream.message}")
    answer: list[str] = []
    async with aclosing(stream.items) as items:
        async for item in items:
            if isinstance(item, ErrorEnvelope):
                raise MemoryEvalError(f"Eval turn stream failed: {item.message}")
            if item.turn_id != accepted.turn_id:
                continue
            if isinstance(item.payload, MessageDelta):
                answer.append(item.payload.text)
            elif isinstance(item.payload, TurnCompleted):
                return "".join(answer)
            elif isinstance(item.payload, TurnFailed):
                raise MemoryEvalError(f"Eval turn failed: {item.payload.message}")
            elif isinstance(item.payload, TurnInterrupted):
                raise MemoryEvalError("Eval turn was interrupted.")
    raise MemoryEvalError("Eval turn subscription ended before the turn closed.")
