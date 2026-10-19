"""Run the learning review over fixture weeks under Inspect."""

import shutil
import sys
import tempfile
import tomllib
from contextlib import aclosing
from datetime import date
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import ChatMessageAssistant, ModelOutput, get_model
from inspect_ai.scorer import model_graded_qa
from inspect_ai.solver import Generate, Solver, TaskState, solver
from pydantic import ConfigDict, TypeAdapter
from pydantic.dataclasses import dataclass

from kinby.cli.client import ContractClient
from kinby.contracts import (
    ROUTINE_RUN,
    THREAD_SUBSCRIBE,
    ErrorEnvelope,
    MessageDelta,
    RoutineName,
    RoutineRunCommand,
    Scope,
    ThreadSubscribeCommand,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
)
from kinby.core.dispatcher import ScheduledTurnConfig, TurnConfig, build_dispatcher
from kinby.core.events import EventLog
from kinby.core.scheduler import SchedulerConfig
from kinby.core.turn_runner import LangGraphRunner
from kinby.instance import load_instance
from kinby.instance.init import DEFAULT_LEARNING_REVIEW_LENS
from kinby.instance.layout import ROUTINE_FILE, ROUTINES_DIR

# Inspect loads task files outside their package, with only this directory on sys.path.
sys.path.append(str(Path(__file__).parents[2]))
from evals.memory.adapter import inspect_model_factory

_CASES_DIR = Path(__file__).parent / "cases"
_JUDGE_MODEL = "openai/gpt-5-mini"
_STAGE = RoutineName("learning-review")
_VERDICT_GRADER_TEMPLATE = """
You are comparing a learning review's dream report with the reference verdicts for the same week.

[BEGIN DATA]
************
[Week]: {question}
************
[Reference]: {criterion}
************
[Report]: {answer}
************
[END DATA]

A verdict is a repeated mistake or an ignored lesson. A report's verdict matches a reference
verdict when it is the same kind, cites the same turn by its thread and turn ids or the same run
by its run id, and cites the same fact by its node id. An empty report names no verdict. Ignore
wording, order, and any explanation the report gives.

Does the report name every reference verdict and no verdict the reference does not name?

{instructions}
"""
_SCOPES = tuple(Scope)


class LearningEvalError(RuntimeError):
    """Report a failure while running a learning eval case."""


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class LearningCase:
    name: str
    #: The review reads the seven days before it.
    today: date
    reference_verdicts: str


_LEARNING_CASE = TypeAdapter(LearningCase)


@task
def learning() -> Task:
    """Return the learning review eval task over every fixture week."""
    return Task(
        dataset=MemoryDataset(
            [_sample(path) for path in sorted(_CASES_DIR.iterdir()) if path.is_dir()]
        ),
        solver=run_learning_review(),
        scorer=model_graded_qa(template=_VERDICT_GRADER_TEMPLATE, model=_JUDGE_MODEL),
    )


@solver
def run_learning_review() -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        del generate
        case_path = _case_path(state)
        case = _load_case(case_path)
        with tempfile.TemporaryDirectory(prefix="kinby-learning-eval-") as temporary:
            instance_path = Path(temporary) / "instance"
            shutil.copytree(case_path / "instance", instance_path)
            _write_stage(instance_path)
            report = await _review(instance_path, case.today)
        state.output = ModelOutput.from_content(model=str(state.model), content=report)
        state.messages.append(ChatMessageAssistant(content=report, source="generate"))
        state.completed = True
        return state

    return solve


def _write_stage(instance_path: Path) -> None:
    """The stage as a new instance ships it, so the eval checks the shipped lens."""
    path = instance_path / ROUTINES_DIR / _STAGE / ROUTINE_FILE
    path.parent.mkdir(parents=True)
    path.write_text(
        f"---\ndescription: Review the week's work.\nrun: review\n---\n"
        f"{DEFAULT_LEARNING_REVIEW_LENS}\n",
        encoding="utf-8",
    )


async def _review(instance_path: Path, today: date) -> str:
    instance = load_instance(instance_path)
    event_log = EventLog(instance.manifest.state_dir)
    runner = LangGraphRunner(
        instance,
        event_log=event_log,
        model_factory=inspect_model_factory(get_model()),
        today=lambda: today,
    )
    dispatcher = build_dispatcher(
        instance.manifest.state_dir,
        event_log=event_log,
        turns=ScheduledTurnConfig(
            TurnConfig(runner.prepare_for_turn, runner.permission_ceiling, runner),
            SchedulerConfig(instance),
        ),
    )
    client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, _SCOPES)
    accepted = await client.call(ROUTINE_RUN, RoutineRunCommand(name=_STAGE))
    if isinstance(accepted, ErrorEnvelope):
        raise LearningEvalError(f"Could not run the learning review: {accepted}")

    stream = await client.subscribe(
        THREAD_SUBSCRIBE,
        ThreadSubscribeCommand(thread_id=accepted.thread_id, after_sequence=accepted.sequence),
    )
    if isinstance(stream, ErrorEnvelope):
        raise LearningEvalError(f"Could not subscribe to the review: {stream.message}")
    report: list[str] = []
    async with aclosing(stream.items) as items:
        async for item in items:
            if isinstance(item, ErrorEnvelope):
                raise LearningEvalError(f"The review's stream failed: {item.message}")
            if item.turn_id != accepted.turn_id:
                continue
            if isinstance(item.payload, MessageDelta):
                report.append(item.payload.text)
            elif isinstance(item.payload, TurnCompleted):
                return "".join(report)
            elif isinstance(item.payload, TurnFailed):
                raise LearningEvalError(f"The review failed: {item.payload.message}")
            elif isinstance(item.payload, TurnInterrupted):
                raise LearningEvalError("The review was interrupted.")
    raise LearningEvalError("The review's stream ended before its turn closed.")


def _sample(case_path: Path) -> Sample:
    case = _load_case(case_path)
    return Sample(
        id=case.name,
        input=f"The learning review of the week before {case.today}.",
        target=case.reference_verdicts,
        metadata={"case_path": str(case_path)},
    )


def _load_case(case_path: Path) -> LearningCase:
    return _LEARNING_CASE.validate_python(
        tomllib.loads((case_path / "case.toml").read_text(encoding="utf-8"))
    )


def _case_path(state: TaskState) -> Path:
    path = state.metadata.get("case_path")
    if not isinstance(path, str):
        raise TypeError("The learning sample has no case path.")
    return Path(path)
