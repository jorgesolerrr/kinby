"""Run the learning review over fixture weeks under Inspect."""

import shutil
import sys
import tempfile
import tomllib
from datetime import date
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import ChatMessageAssistant, ModelOutput, get_model
from inspect_ai.scorer import model_graded_qa
from inspect_ai.solver import Generate, Solver, TaskState, solver
from pydantic import ConfigDict, TypeAdapter
from pydantic.dataclasses import dataclass

from kinby.contracts import RoutineName
from kinby.instance.init import DEFAULT_LEARNING_REVIEW_LENS

# Inspect loads task files outside their package, with only this directory on sys.path.
sys.path.append(str(Path(__file__).parents[2]))
from evals.memory.adapter import inspect_model_factory
from evals.stage import run_stage, write_stage

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
            # The stage as a new instance ships it, so the eval checks the shipped lens.
            write_stage(
                instance_path,
                _STAGE,
                description="Review the week's work.",
                run="learning_review",
                lens=DEFAULT_LEARNING_REVIEW_LENS,
            )
            report = await run_stage(
                instance_path,
                _STAGE,
                today=case.today,
                model_factory=inspect_model_factory(get_model()),
            )
        state.output = ModelOutput.from_content(model=str(state.model), content=report)
        state.messages.append(ChatMessageAssistant(content=report, source="generate"))
        state.completed = True
        return state

    return solve


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
