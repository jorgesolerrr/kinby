"""A workbench runs an agent step on the kinby engine as a turn in the run's folder, through the
shipped gate policy, with every ask refused at once."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from uuid import UUID, uuid4

from langchain_core.messages import AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.tools import StructuredTool

from kinby.contracts import (
    AgentStepRun,
    Event,
    FactoryRunOrigin,
    FactoryRunTranscriptCommand,
    RunFolder,
    StepEnding,
    StepResult,
    StepRunCommand,
)
from kinby.workbench import WorkbenchSteps, prepare_workspace
from tests.test_hub_factories import write_factory
from tests.test_workbench_steps import CHECK
from tests.test_workbench_workspace import _seed

PLAIN = RunFolder(branch=None)
STEP = AgentStepRun(prompt="Summarize the notes.\n", model="anthropic:claude-opus-5-5")


class TurnModel:
    """Answers each call with the next of *responses*, and keeps the messages of each call."""

    def __init__(self, *responses: AIMessageChunk) -> None:
        self._responses = iter(responses)
        self.calls: list[list[BaseMessage]] = []

    def bind_tools(self, tools: Sequence[StructuredTool]) -> TurnModel:
        return self

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.calls.append(list(messages))
        yield next(self._responses)


def _calling(name: str, **arguments: str) -> AIMessageChunk:
    return AIMessageChunk(
        content="", tool_calls=[{"name": name, "args": arguments, "id": "call-1"}]
    )


def _run(
    tmp_path: Path,
    model: TurnModel,
    *,
    source: str | None = None,
    folder: RunFolder = PLAIN,
    check: str | None = None,
    run_id: UUID | None = None,
) -> StepResult:
    directory = tmp_path / "workbench"
    prepare_workspace(directory / "workspace", source)
    workbench = WorkbenchSteps(
        directory, tmp_path / "factories", tmp_path / "shipped", model_factory=lambda _: model
    )
    command = StepRunCommand(
        step=STEP,
        origin=FactoryRunOrigin(factory="tickets", run_id=run_id or uuid4(), step="implement"),
        attempt=1,
        hook=check,
        work_item={"issue": 12},
        folder=folder,
    )
    return asyncio.run(workbench.run(command))


def _tool_output(model: TurnModel) -> str:
    [result] = [message for message in model.calls[1] if isinstance(message, ToolMessage)]
    return result.text


def test_a_kinby_agent_step_runs_a_turn_with_its_prompt_in_the_runs_folder(tmp_path):
    model = TurnModel(
        _calling("read", path="NOTES.md"), AIMessageChunk(content="The notes say seed.")
    )

    result = _run(
        tmp_path, model, source=str(_seed(tmp_path)), folder=RunFolder(branch="tickets/issue-12")
    )

    assert (result.ending, result.summary) == (StepEnding.CLEAN, "The notes say seed.")
    assert "Summarize the notes." in model.calls[0][-1].text
    assert _tool_output(model) == "seed\n"
    assert result.usage is not None and result.usage.api is not None


def test_a_kinby_turns_write_is_refused_at_once_and_the_turn_told_so(tmp_path):
    model = TurnModel(
        _calling("write", path="draft.md", content="# Draft\n"),
        AIMessageChunk(content="I could not write the draft."),
    )
    run_id = uuid4()

    result = _run(tmp_path, model, run_id=run_id)

    assert (result.ending, result.summary) == (StepEnding.CLEAN, "I could not write the draft.")
    assert not (tmp_path / "workbench" / "runs" / str(run_id) / "draft.md").exists()
    assert "no one can answer" in _tool_output(model).lower()


def test_a_kinby_turns_tools_reach_nothing_outside_the_runs_folder(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("hunter2\n", encoding="utf-8")
    read = TurnModel(_calling("read", path=str(secret)), AIMessageChunk(content="Done."))
    cat = TurnModel(_calling("bash", command=f"cat {secret}"), AIMessageChunk(content="Done."))

    _run(tmp_path, read)
    _run(tmp_path, cat)

    assert "outside the workspace" in _tool_output(read)
    assert "hunter2" not in _tool_output(cat)
    assert "no one can answer" in _tool_output(cat).lower()


def test_a_kinby_agent_steps_check_runs_when_its_turn_ends(tmp_path):
    write_factory(tmp_path / "factories" / "tickets", {"hooks/drafted.py": CHECK})
    model = TurnModel(AIMessageChunk(content="Nothing to draft."))

    result = _run(tmp_path, model, check="drafted")

    assert result.values == {"ending": "clean", "drafted": False, "issue": 12}
    assert result.outcome == "redraft"


def test_a_kinby_agent_steps_attempt_keeps_its_turns_events_as_its_transcript(tmp_path):
    model = TurnModel(AIMessageChunk(content="Nothing to draft."))
    run_id = uuid4()
    _run(tmp_path, model, run_id=run_id)
    workbench = WorkbenchSteps(tmp_path / "workbench", tmp_path / "factories", tmp_path / "shipped")

    transcript = asyncio.run(
        workbench.transcript(
            FactoryRunTranscriptCommand(run_id=run_id, step="implement", attempt=1)
        )
    )

    types = [Event.model_validate(event).payload.type for event in transcript.events]
    assert (types[0], types[-1]) == ("turn.started", "turn.completed")
    assert "message.delta" in types


def test_a_kinby_turn_is_offered_the_factorys_skills_and_the_workbenchs_picks(tmp_path):
    write_factory(tmp_path / "factories" / "tickets", {"skills/ship/SKILL.md": _skill("ship")})
    write_factory(tmp_path / "workbench", {"skills/tdd/SKILL.md": _skill("tdd")})
    model = TurnModel(AIMessageChunk(content="Done."))

    _run(tmp_path, model)

    system = model.calls[0][0].text
    assert "- ship: Do ship." in system
    assert "- tdd: Do tdd." in system


def _skill(name: str) -> str:
    return f"---\nname: {name}\ndescription: Do {name}.\n---\nDo it.\n"
