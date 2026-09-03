"""Run memory cases through kinby's dispatcher under Inspect."""

import shutil
import tempfile
import tomllib
from collections.abc import Sequence
from enum import StrEnum
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
    mean,
    model_graded_fact,
    scorer,
)
from inspect_ai.solver import Generate, Solver, TaskState, solver
from pydantic import ConfigDict, TypeAdapter
from pydantic.dataclasses import dataclass

from kinby.cli.client import ContractClient
from kinby.contracts import (
    THREAD_CREATE,
    THREAD_SUBSCRIBE,
    THREAD_TURN_START,
    ErrorEnvelope,
    Event,
    MemoryRecapped,
    MessageDelta,
    NodeId,
    Scope,
    ThreadCreateCommand,
    ThreadSubscribeCommand,
    ThreadTurnStartCommand,
    ToolCall,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnStarted,
)
from kinby.core.dispatcher import TurnConfig, build_dispatcher
from kinby.core.events import EventLog
from kinby.core.turn_metrics import estimate_memory_tokens, turn_metrics
from kinby.core.turn_runner import LangGraphRunner
from kinby.instance import load_instance
from kinby.instance.layout import GRAPH_DIR, MEMORY_DIR, PROFILE_NAME
from kinby.memory import GraphStore, RecapWriter

if TYPE_CHECKING or __package__ == "evals.memory":
    from evals.memory.adapter import inspect_model_factory
else:
    # Inspect loads task files outside their package, with this directory on sys.path.
    from adapter import inspect_model_factory

_CASES_DIR = Path(__file__).parent / "cases"
_JUDGE_MODEL = "openai/gpt-5-mini"
_SCOPES = tuple(Scope)


class MemoryEvalError(RuntimeError):
    """Report a failure while running a memory eval case."""


class MemoryArm(StrEnum):
    GRAPH = "graph"
    STUFFING = "stuffing"


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class MemoryCase:
    name: str
    turns: tuple[str, ...]
    reference_answer: str
    expected_node: NodeId
    must_pass: bool


_MEMORY_CASE = TypeAdapter(MemoryCase)


@task
def memory(arm: str = MemoryArm.GRAPH) -> Task:
    """Return one arm of the memory eval task."""
    selected_arm = MemoryArm(arm)
    return Task(
        dataset=MemoryDataset(
            [_sample(path) for path in sorted(_CASES_DIR.iterdir()) if path.is_dir()]
        ),
        solver=run_memory_case(selected_arm),
        scorer=(
            model_graded_fact(model=_JUDGE_MODEL),
            expected_node_opened(),
            memory_tokens(),
        ),
        metadata={"arm": selected_arm},
    )


@scorer(metrics=[accuracy()])
def expected_node_opened() -> Scorer:
    async def score(state: TaskState, target: Target) -> Score:
        del target
        opened = state.metadata.get("opened_expected_node")
        if not isinstance(opened, bool):
            raise TypeError("The memory sample has no expected-node result.")
        return Score(
            value=CORRECT if opened else INCORRECT,
            explanation=(
                "The last turn opened the expected memory node."
                if opened
                else "The last turn did not open the expected memory node."
            ),
        )

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
def run_memory_case(arm: MemoryArm) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        del generate
        case_path = _case_path(state)
        case = _load_case(case_path)
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
        state.output = ModelOutput.from_content(model=str(state.model), content=answer)
        state.messages.append(ChatMessageAssistant(content=answer, source="generate"))
        trace = [
            event
            for event in events
            if event.turn_id == last_turn and isinstance(event.payload, (ToolCall, MemoryRecapped))
        ]
        state.metadata["event_log"] = [event.model_dump(mode="json") for event in trace]
        searched = any(
            isinstance(event.payload, ToolCall) and event.payload.name == "memory_search"
            for event in trace
        )
        opened_expected_node = any(
            isinstance(event.payload, ToolCall)
            and event.payload.name == "memory_open"
            and event.payload.arguments.get("node") == case.expected_node
            for event in trace
        )
        recapped = any(isinstance(event.payload, MemoryRecapped) for event in trace)
        state.metadata["opened_expected_node"] = opened_expected_node
        if (
            case.must_pass
            and arm is MemoryArm.GRAPH
            and not (searched and opened_expected_node and recapped)
        ):
            raise MemoryEvalError(
                "Must-pass memory case did not search memory, open the expected node, and recap."
            )
        state.completed = True
        return state

    return solve


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
    return record.memory_tokens + estimate_memory_tokens(len(profile))


def _sample(case_path: Path) -> Sample:
    case = _load_case(case_path)
    return Sample(
        id=case.name,
        input=case.turns[-1],
        target=case.reference_answer,
        metadata={
            "case_path": str(case_path),
            "expected_node": case.expected_node,
            "must_pass": case.must_pass,
        },
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


async def _run_turns(instance_path: Path, turns: Sequence[str]) -> tuple[list[Event], str]:
    instance = load_instance(instance_path)
    event_log = EventLog(instance.manifest.state_dir)
    factory = inspect_model_factory(get_model())
    runner = LangGraphRunner(instance, model_factory=factory)
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
    created = await client.call(
        THREAD_CREATE,
        ThreadCreateCommand(title="memory eval"),
    )
    if isinstance(created, ErrorEnvelope):
        raise MemoryEvalError(f"Could not create eval thread: {created}")

    answer = ""
    for message in turns:
        answer = await _run_turn(client, created.id, message)
        await recap.drain()
    return list(event_log.all_events()), answer


async def _run_turn(client: ContractClient, thread_id: UUID, message: str) -> str:
    accepted = await client.call(
        THREAD_TURN_START,
        ThreadTurnStartCommand(thread_id=thread_id, message=message),
    )
    if isinstance(accepted, ErrorEnvelope):
        raise MemoryEvalError(f"Could not start eval turn: {accepted}")

    answer: list[str] = []
    subscription = ThreadSubscribeCommand(
        thread_id=thread_id,
        after_sequence=accepted.sequence,
    )
    async for item in client.subscribe(THREAD_SUBSCRIBE, subscription):
        if isinstance(item, ErrorEnvelope):
            raise MemoryEvalError(f"Could not subscribe to eval turn: {item.message}")
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
