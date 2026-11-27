"""A workbench runs an agent step on the kinby engine as a turn in the run's folder, through the
shipped gate policy. An ask of the gate ends the attempt asked, and the answer resumes the turn."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.tools import StructuredTool

from kinby.contracts import (
    AgentStepRun,
    AnsweredAsk,
    ApprovalDecision,
    Event,
    FactoryRun,
    FactoryRunOrigin,
    FactoryRunTranscriptCommand,
    MemoryRecalled,
    NodeId,
    RunFolder,
    Scope,
    StepAnswer,
    StepEnding,
    StepId,
    StepResult,
    StepRunCommand,
    ViewGetCommand,
    WaitingApproval,
    WaitingTakeover,
)
from kinby.core.events import EventLog
from kinby.memory import GraphStore
from kinby.memory.tools import hits_json, open_memory
from kinby.plugins.hub import HUB_SCOPES_VARIABLE, HUB_URL_VARIABLE
from kinby.workbench import WorkbenchSteps, prepare_workspace
from tests.helpers import every_event
from tests.test_browser_tools import FakeBrowser
from tests.test_hub_factories import write_factory
from tests.test_hub_line_intake import Owned, _started_workbench, owned
from tests.test_hub_routing import control_token
from tests.test_hub_workbench_memory import CHORES, _lessons
from tests.test_hub_workbench_memory import _run as _hub_run
from tests.test_views import DATA, PAGE
from tests.test_workbench_steps import CHECK, CLAUDE_STREAM, streaming, stub_client
from tests.test_workbench_workspace import _seed

type Decision = Literal[ApprovalDecision.APPROVE, ApprovalDecision.DENY]

PLAIN = RunFolder(branch=None)
STEP = AgentStepRun(prompt="Summarize the notes.\n", model="anthropic:claude-opus-5-5")


class TurnModel:
    """Answers each call with the next of *responses*, raising one that is an exception, and
    keeps the messages of each call."""

    def __init__(self, *responses: AIMessageChunk | Exception) -> None:
        self._responses = iter(responses)
        self.calls: list[list[BaseMessage]] = []
        #: The names of the tools the turn offered the model.
        self.tools: list[str] = []

    def bind_tools(self, tools: Sequence[StructuredTool]) -> TurnModel:
        self.tools = [tool.name for tool in tools]
        return self

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.calls.append(list(messages))
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        yield response


def _calling(name: str, *, call: str = "call-1", **arguments: object) -> AIMessageChunk:
    return AIMessageChunk(content="", tool_calls=[{"name": name, "args": arguments, "id": call}])


def _run(tmp_path: Path, model: TurnModel, **options: Any) -> StepResult:
    return asyncio.run(_ran(tmp_path, model, **options))


async def _ran(
    tmp_path: Path,
    model: TurnModel,
    *,
    source: str | None = None,
    folder: RunFolder = PLAIN,
    check: str | None = None,
    run_id: UUID | None = None,
    factory: str = "tickets",
    at: StepId = "implement",
    step: AgentStepRun = STEP,
    attempt: int = 1,
    browser: FakeBrowser | None = None,
) -> StepResult:
    directory = tmp_path / "workbench"
    prepare_workspace(directory / "workspace", source)
    workbench = WorkbenchSteps(
        directory,
        tmp_path / "factories",
        tmp_path / "shipped",
        model_factory=lambda _: model,
        browser=browser,
    )
    command = StepRunCommand(
        step=step,
        origin=FactoryRunOrigin(factory=factory, run_id=run_id or uuid4(), step=at),
        attempt=attempt,
        hook=check,
        work_item={"issue": 12},
        folder=folder,
    )
    return await workbench.run(command)


def _tool_output(model: TurnModel, call: int = 1) -> str:
    """What the last tool the model called before its *call* returned."""
    *_, result = [message for message in model.calls[call] if isinstance(message, ToolMessage)]
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


def test_a_kinby_turns_write_the_gate_asks_about_ends_its_attempt_asked(tmp_path):
    write_factory(tmp_path / "factories" / "tickets", {"hooks/drafted.py": CHECK})
    model = TurnModel(_calling("write", path="draft.md", content="# Draft\n"))
    run_id = uuid4()

    result = _run(tmp_path, model, run_id=run_id, check="drafted")

    assert result.ending is StepEnding.ASKED
    assert isinstance(result.ask, WaitingApproval) and result.ask.request.name == "write"
    assert result.ask.request.arguments == {"path": "draft.md", "content": "# Draft\n"}
    assert "write" in result.summary
    assert result.values == {}
    assert not (tmp_path / "workbench" / "runs" / str(run_id) / "draft.md").exists()


def _answered(tmp_path: Path, decision: Decision, reason: str | None = None):
    """A write asked about and answered with *decision*: the model, the run and both results."""
    write_factory(tmp_path / "factories" / "tickets", {"hooks/drafted.py": CHECK})
    model = TurnModel(
        _calling("write", path="draft.md", content="# Draft\n"),
        AIMessageChunk(content="Drafted."),
    )
    run_id = uuid4()
    asked = _run(tmp_path, model, run_id=run_id, check="drafted")
    assert asked.ask is not None
    answer = AnsweredAsk(ask=asked.ask, answer=StepAnswer(decision=decision, reason=reason))
    resumed = _run(
        tmp_path,
        model,
        run_id=run_id,
        check="drafted",
        step=STEP.model_copy(update={"answered": answer}),
        attempt=2,
    )
    return model, tmp_path / "workbench" / "runs" / str(run_id), asked, resumed


def test_an_ask_too_large_to_send_fails_the_step_and_names_its_size(tmp_path):
    model = TurnModel(_calling("write", path="draft.md", content="x" * 2_000_000))

    result = _run(tmp_path, model)

    assert result.ending is StepEnding.FAILED
    assert result.ask is None
    assert result.summary.endswith(", over the 1,048,576-byte limit.")


def test_approving_a_kinby_turns_ask_resumes_the_turn_and_the_write_happens(tmp_path):
    model, folder, _, resumed = _answered(tmp_path, ApprovalDecision.APPROVE)

    assert (resumed.ending, resumed.summary) == (StepEnding.CLEAN, "Drafted.")
    assert (folder / "draft.md").read_text(encoding="utf-8") == "# Draft\n"
    assert len(model.calls) == 2


def test_refusing_a_kinby_turns_ask_resumes_the_turn_and_tells_it_so(tmp_path):
    model, folder, _, resumed = _answered(
        tmp_path, ApprovalDecision.DENY, "Keep the notes as they are."
    )

    assert (resumed.ending, resumed.summary) == (StepEnding.CLEAN, "Drafted.")
    assert not (folder / "draft.md").exists()
    assert "Keep the notes as they are." in _tool_output(model)


def test_a_kinby_agent_steps_check_runs_when_its_resumed_turn_ends(tmp_path):
    _, _, asked, resumed = _answered(tmp_path, ApprovalDecision.APPROVE)

    assert asked.outcome is None
    assert resumed.values == {"ending": "clean", "drafted": True, "issue": 12}


def test_an_answer_to_an_ask_no_turn_is_parked_on_any_more_fails_the_step(tmp_path):
    model = TurnModel(
        _calling("write", path="draft.md", content="# Draft\n"),
        AIMessageChunk(content="Drafted."),
    )
    asked = _run(tmp_path, model)
    assert asked.ask is not None
    answer = AnsweredAsk(ask=asked.ask, answer=StepAnswer(decision=ApprovalDecision.APPROVE))
    answered = STEP.model_copy(update={"answered": answer})

    resumed = _run(tmp_path, model, step=answered, attempt=2)
    again = _run(tmp_path, model, step=answered, attempt=3)

    assert resumed.ending is StepEnding.CLEAN
    assert again.ending is StepEnding.FAILED
    assert "parked" in again.summary


def test_a_kinby_turns_tools_reach_nothing_outside_the_runs_folder(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("hunter2\n", encoding="utf-8")
    read = TurnModel(_calling("read", path=str(secret)), AIMessageChunk(content="Done."))
    cat = TurnModel(_calling("bash", command=f"cat {secret}"), AIMessageChunk(content="Done."))

    _run(tmp_path, read)
    asked = _run(tmp_path, cat)

    assert "outside the workspace" in _tool_output(read)
    assert asked.ending is StepEnding.ASKED
    assert len(cat.calls) == 1


def test_a_failed_kinby_turns_attempt_spends_what_its_model_calls_cost(tmp_path):
    billed = _calling("read", path="NOTES.md")
    billed.usage_metadata = {"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100}
    model = TurnModel(billed, RuntimeError("The provider went away."))

    result = _run(tmp_path, model)

    assert result.ending is StepEnding.FAILED
    assert result.usage is not None and result.usage.api is not None
    assert result.usage.api.cost == 0.006


def test_a_kinby_turns_view_write_ends_its_attempt_asked(tmp_path):
    model = TurnModel(
        _calling("view_write", id="board", title="Applications", page=PAGE, data=DATA)
    )

    result = _run(tmp_path, model)

    assert result.ending is StepEnding.ASKED
    assert isinstance(result.ask, WaitingApproval) and result.ask.request.name == "view_write"
    assert result.views == []


def test_a_kinby_turns_view_write_asks_though_an_earlier_run_left_its_page_on_the_workbench(
    tmp_path,
):
    write = _calling("view_write", id="board", title="Applications", page=PAGE, data=DATA)
    _approved(tmp_path, TurnModel(write, AIMessageChunk(content="The board is up.")), writes=1)

    result = _run(tmp_path, TurnModel(write))

    assert result.ending is StepEnding.ASKED
    assert isinstance(result.ask, WaitingApproval) and result.ask.request.name == "view_write"


def _approved(tmp_path: Path, model: TurnModel, *, writes: int) -> StepResult:
    """The step's result once each of its turn's *writes* asked about is approved."""
    run_id = uuid4()
    result = _run(tmp_path, model, run_id=run_id)
    for attempt in range(2, writes + 2):
        assert result.ask is not None, result
        answer = AnsweredAsk(ask=result.ask, answer=StepAnswer(decision=ApprovalDecision.APPROVE))
        answered = STEP.model_copy(update={"answered": answer})
        result = _run(tmp_path, model, run_id=run_id, step=answered, attempt=attempt)
    return result


def test_a_kinby_turn_names_each_view_it_wrote_and_its_workbench_serves_it_as_last_written(
    tmp_path,
):
    model = TurnModel(
        _calling("view_write", id="board", title="Applications", page=PAGE, data=DATA),
        _calling("view_write", call="call-2", id="board", title="Jobs", page=PAGE, data=DATA),
        AIMessageChunk(content="The board is up."),
    )

    result = _approved(tmp_path, model, writes=2)

    workbench = WorkbenchSteps(tmp_path / "workbench", tmp_path / "factories", tmp_path / "shipped")
    view = asyncio.run(workbench.view(ViewGetCommand(id="board")))

    assert result.ending is StepEnding.CLEAN
    assert result.views == ["board"]
    assert (view.id, view.title, view.page, view.data) == ("board", "Jobs", PAGE, DATA)


def test_a_failed_kinby_turn_still_hands_back_the_views_it_wrote(tmp_path):
    model = TurnModel(
        _calling("view_write", id="board", title="Applications", page=PAGE, data=DATA),
        RuntimeError("The provider went away."),
    )

    result = _approved(tmp_path, model, writes=1)

    assert result.ending is StepEnding.FAILED
    assert result.views == ["board"]


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


def test_a_kinby_turns_takeover_ends_its_attempt_asked_and_finishing_it_resumes_the_turn(
    tmp_path,
):
    browser = FakeBrowser()
    model = TurnModel(
        _calling("browser_takeover", reason="Sign in to Moodle."),
        AIMessageChunk(content="Signed in."),
    )
    run_id = uuid4()

    asked = _run(tmp_path, model, run_id=run_id, browser=browser)
    assert isinstance(asked.ask, WaitingTakeover)
    finished = AnsweredAsk(ask=asked.ask, answer=StepAnswer(decision=ApprovalDecision.APPROVE))
    resumed = _run(
        tmp_path,
        model,
        run_id=run_id,
        step=STEP.model_copy(update={"answered": finished}),
        attempt=2,
        browser=browser,
    )

    assert asked.ending is StepEnding.ASKED
    assert asked.ask.request.reason == "Sign in to Moodle."
    assert "take over" in asked.summary
    assert (resumed.ending, resumed.summary) == (StepEnding.CLEAN, "Signed in.")
    assert "The user finished the takeover." in _tool_output(model)
    assert browser.opened == [asked.ask.turn_id]
    assert browser.closed == [asked.ask.turn_id]


def test_a_kinby_turn_on_a_workbench_with_no_browser_has_no_browser_tools(tmp_path):
    model = TurnModel(
        _calling("browser_takeover", reason="Sign in to Moodle."),
        AIMessageChunk(content="I cannot."),
    )

    result = _run(tmp_path, model)

    assert (result.ending, result.summary) == (StepEnding.CLEAN, "I cannot.")
    assert "browser_takeover" in _tool_output(model)


def test_a_kinby_turn_on_a_workbench_delegates_to_no_coding_client(tmp_path, monkeypatch):
    calls = stub_client(tmp_path, monkeypatch, "claude", streaming(CLAUDE_STREAM))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "token")
    model = TurnModel(
        _calling("delegate_to_coding_client", task="Fix it."),
        AIMessageChunk(content="I cannot."),
    )

    result = _run(tmp_path, model)

    assert (result.ending, result.summary) == (StepEnding.CLEAN, "I cannot.")
    assert "delegate_to_coding_client" in _tool_output(model)
    assert not calls.exists()


async def _on_workbench(owned: Owned, monkeypatch: pytest.MonkeyPatch) -> FactoryRun:
    """A run on a workbench, on a line *owned*'s home owns, whose hub route the workbench's
    process holds."""
    workbench_id = await _started_workbench(owned)
    run = await _hub_run(owned, workbench_id)
    monkeypatch.setenv(HUB_URL_VARIABLE, f"{owned.address}/instances/{workbench_id}/hub")
    monkeypatch.setenv(HUB_SCOPES_VARIABLE, Scope.WORKBENCH_MEMORY)
    monkeypatch.setenv("KINBY_CONTROL_TOKEN", control_token(owned.hub, str(workbench_id)))
    return run


@pytest.fixture
def chores(tmp_path: Path) -> None:
    write_factory(tmp_path / "hub" / "factories" / "chores", {"factory.yaml": CHORES})


@pytest.mark.usefixtures("chores")
def test_a_kinby_turn_on_a_workbench_searches_and_opens_its_line_owners_memory(
    tmp_path, monkeypatch
):
    async def scenario() -> tuple[TurnModel, StepResult, dict[str | None, NodeId], FactoryRun]:
        async with owned(tmp_path, monkeypatch) as hub:
            lessons = _lessons(hub.instance, "cec-backend", None)
            run = await _on_workbench(hub, monkeypatch)
            model = TurnModel(
                _calling("memory_search", query="install"),
                _calling("memory_open", call="call-2", node=lessons[None]),
                AIMessageChunk(content="Install first."),
            )
            result = await _ran(
                tmp_path, model, run_id=run.run_id, factory="chores", at="draft", attempt=2
            )
            return model, result, lessons, run

    model, result, lessons, run = asyncio.run(scenario())

    assert (result.ending, result.summary) == (StepEnding.CLEAN, "Install first.")
    owner = GraphStore(tmp_path / "home")
    assert _tool_output(model) == hits_json(owner.recall("install", workbench="cec-backend"))
    assert _tool_output(model, 2) == open_memory(owner, lessons[None])
    recalls = [
        (event.thread_id, event.payload.step, event.payload.attempt)
        for event in every_event(EventLog(tmp_path / "home" / ".state"))
        if isinstance(event.payload, MemoryRecalled)
    ]
    assert recalls == [(run.run_id, "draft", 2), (run.run_id, "draft", 2)]


@pytest.mark.usefixtures("chores")
def test_a_kinby_turn_on_a_workbench_is_told_of_its_read_only_memory_and_cannot_write_it(
    tmp_path, monkeypatch
):
    async def scenario() -> TurnModel:
        async with owned(tmp_path, monkeypatch) as hub:
            run = await _on_workbench(hub, monkeypatch)
            model = TurnModel(AIMessageChunk(content="Done."))
            await _ran(tmp_path, model, run_id=run.run_id, factory="chores", at="draft")
            return model

    model = asyncio.run(scenario())

    assert {"memory_search", "memory_open"} <= set(model.tools)
    assert not {"remember", "forget"} & set(model.tools)
    system = model.calls[0][0].text
    assert "# Memory" in system
    assert "Before you act, search" in system


@pytest.mark.usefixtures("chores")
def test_a_kinby_turn_whose_line_owner_is_stopped_goes_on_without_memory(tmp_path, monkeypatch):
    async def scenario() -> tuple[TurnModel, StepResult]:
        async with owned(tmp_path, monkeypatch) as hub:
            [lesson] = _lessons(hub.instance, None).values()
            run = await _on_workbench(hub, monkeypatch)
            del hub.runtime.addresses[str(hub.home)]
            model = TurnModel(
                _calling("memory_search", query="install"),
                _calling("memory_open", call="call-2", node=lesson),
                AIMessageChunk(content="Done without memory."),
            )
            result = await _ran(tmp_path, model, run_id=run.run_id, factory="chores", at="draft")
            return model, result

    model, result = asyncio.run(scenario())

    assert (result.ending, result.summary) == (StepEnding.CLEAN, "Done without memory.")
    for told in (_tool_output(model), _tool_output(model, 2)):
        assert told.startswith('The memory of "home", which owns the line of the run, is ')
        assert told.endswith("Go on without memory.")


def test_a_kinby_turn_on_a_workbench_with_no_memory_scope_has_no_memory_tools(tmp_path):
    model = TurnModel(AIMessageChunk(content="Done."))

    _run(tmp_path, model)

    assert not {"memory_search", "memory_open", "remember", "forget"} & set(model.tools)
    assert "# Memory" not in model.calls[0][0].text
