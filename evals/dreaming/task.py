"""Run consolidation over fixture graphs under Inspect."""

import sys
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import ChatMessageAssistant, ModelOutput, get_model
from inspect_ai.scorer import CORRECT, INCORRECT, Score, Scorer, Target, accuracy, scorer
from inspect_ai.solver import Generate, Solver, TaskState, solver
from pydantic import TypeAdapter

from kinby.memory import Fact

# Inspect loads task files outside their package, with only this directory on sys.path.
sys.path.append(str(Path(__file__).parents[2]))
from evals.dreaming.consolidation import CASES_DIR, consolidate, grade, live_facts, load_case
from evals.memory.adapter import inspect_model_factory

_LIVE_FACTS = "live_facts"
_FACTS = TypeAdapter(tuple[Fact, ...])


@task
def dreaming() -> Task:
    """Return the consolidation eval task over every fixture graph."""
    return Task(
        dataset=MemoryDataset(
            [_sample(path) for path in sorted(CASES_DIR.iterdir()) if path.is_dir()]
        ),
        solver=run_consolidation(),
        scorer=graph_state(),
    )


@solver
def run_consolidation() -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        del generate
        after = await consolidate(_case_path(state), inspect_model_factory(get_model()))
        state.store.set(_LIVE_FACTS, _FACTS.dump_python(after, mode="json"))
        facts = "\n\n".join(f"{fact.node}\n{fact.description}\n{fact.body}" for fact in after)
        state.output = ModelOutput.from_content(model=str(state.model), content=facts)
        state.messages.append(ChatMessageAssistant(content=facts, source="generate"))
        state.completed = True
        return state

    return solve


@scorer(metrics=[accuracy()])
def graph_state() -> Scorer:
    """Grade the live facts consolidation left against the case's replacements."""

    async def score(state: TaskState, target: Target) -> Score:
        del target
        case_path = _case_path(state)
        found = grade(
            load_case(case_path),
            live_facts(case_path / "instance"),
            _FACTS.validate_python(state.store.get(_LIVE_FACTS)),
        )
        return Score(
            value=INCORRECT if found else CORRECT,
            explanation="\n".join(found) or "Every replacement was written as the case expects.",
        )

    return score


def _sample(case_path: Path) -> Sample:
    case = load_case(case_path)
    return Sample(
        id=case.name,
        input=f"Consolidation of the uncovered episodes on {case.today}.",
        metadata={"case_path": str(case_path)},
    )


def _case_path(state: TaskState) -> Path:
    path = state.metadata.get("case_path")
    if not isinstance(path, str):
        raise TypeError("The dreaming sample has no case path.")
    return Path(path)
