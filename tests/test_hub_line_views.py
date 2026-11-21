"""A view a step of a line's run writes belongs to the line's owner, among its views, and the run's
detail names it, through the hub contract."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import JsonValue

from kinby.contracts import (
    LINE_HAND_IN,
    LINE_SETUP,
    AgentStepRun,
    ApprovalDecision,
    ApprovalRequested,
    FactoryRun,
    FactoryRunStatus,
    GateRule,
    LineHandInCommand,
    LineSetupCommand,
    LineSetupResult,
    PageWrite,
    StepEnding,
    StepResult,
    View,
    ViewListResult,
    WaitingApproval,
)
from kinby.instance.views import write_view
from tests.test_factory_runs import detail, settled
from tests.test_factory_waits import parked
from tests.test_hub_factories import write_factory
from tests.test_hub_line_asks import KEYED
from tests.test_hub_line_intake import Owned, _started_workbench, owned
from tests.test_hub_line_runs import TASKS
from tests.test_scheduler import call
from tests.test_views import DATA, PAGE

BOARD_VIEW = View(
    id="board",
    title="Applications",
    page=PAGE,
    data=DATA,
    updated_at=datetime(2026, 11, 14, tzinfo=UTC),
)
DRAFTED = StepResult(ending=StepEnding.CLEAN, views=["board"])


async def _handed_in(owned: Owned) -> FactoryRun:
    """A task handed to a line of the tasks factory, which the home instance owns."""
    workbench_id = await _started_workbench(owned)
    set_up = await owned.client.call(
        LINE_SETUP, LineSetupCommand(factory="tasks", workbench_id=workbench_id)
    )
    assert isinstance(set_up, LineSetupResult), set_up
    line = set_up.line
    run = await owned.client.call(
        LINE_HAND_IN, LineHandInCommand(line_id=line.line_id, work_item={"task": "jobs"})
    )
    assert isinstance(run, FactoryRun), run
    return run


def test_a_view_a_step_writes_appears_among_its_owners_views_and_its_run_names_it(
    tmp_path, monkeypatch
):
    write_factory(tmp_path / "hub" / "factories" / "tasks", {"factory.yaml": TASKS})

    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            hub.control.step_results = [DRAFTED]
            hub.control.views = {"board": BOARD_VIEW}

            run = await _handed_in(hub)
            done = await settled(hub.hub, run.run_id)

            assert done.run.status is FactoryRunStatus.DONE
            assert done.run.instance_id == hub.home
            assert [attempt.views for attempt in done.attempts] == [["board"]]
            listed = await call(hub.dispatcher, "view.list")
            assert isinstance(listed, ViewListResult), listed
            assert [(view.id, view.title) for view in listed.views] == [("board", "Applications")]
            held = await call(hub.dispatcher, "view.get", id="board")
            assert isinstance(held, View), held
            assert (held.page, held.data) == (PAGE, DATA)
            [(workbench, _)] = hub.control.steps
            assert [(endpoint, read.id) for endpoint, read in hub.control.views_read] == [
                (workbench, "board")
            ]

    asyncio.run(scenario())


def test_a_view_its_owner_does_not_take_is_named_in_the_attempts_summary(tmp_path, monkeypatch):
    write_factory(tmp_path / "hub" / "factories" / "tasks", {"factory.yaml": TASKS})

    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            hub.control.step_results = [DRAFTED.model_copy(update={"summary": "Drafted."})]
            hub.control.views = {"board": BOARD_VIEW}
            del hub.runtime.addresses[str(hub.home)]

            run = await _handed_in(hub)
            done = await settled(hub.hub, run.run_id)

            assert done.run.status is FactoryRunStatus.DONE
            [attempt] = done.attempts
            assert attempt.views == []
            assert attempt.summary.startswith('Drafted.\nThe owner did not take view "board": ')

    asyncio.run(scenario())


BOARDS = {
    "factory.yaml": """\
name: boards
requires: { image: coder }
work_item: { task: str }
steps:
  - id: draft
    kind: agent
    engine: kinby
    prompt: prompts/draft.md
    model: anthropic:claude-opus-5-5
""",
    "prompts/draft.md": "Keep the board.\n",
}
REFRESHED: dict[str, JsonValue] = {**DATA, "postings": [{"company": "Initech"}]}


def _asks_to_write(page: str) -> StepResult:
    """The attempt of a turn the gate parked on its ask to write the board with *page*."""
    request = ApprovalRequested(
        approval_id=uuid4(),
        name="view_write",
        arguments={"id": "board", "title": "Applications", "page": page, "data": REFRESHED},
        rule=GateRule("mode.ask.write"),
        # Drawn against the workbench's own store, which holds no board.
        page_write=PageWrite(title="Applications", page=page, data=REFRESHED, stored_page=None),
    )
    return StepResult(
        ending=StepEnding.ASKED,
        summary="The turn asks to call view_write.",
        ask=WaitingApproval(turn_id=uuid4(), request=request),
    )


async def _board_handed_in(owned: Owned) -> FactoryRun:
    """A task handed to a line of the boards factory, whose kinby step keeps the board."""
    workbench_id = await _started_workbench(owned, KEYED)
    set_up = await owned.client.call(
        LINE_SETUP, LineSetupCommand(factory="boards", workbench_id=workbench_id)
    )
    assert isinstance(set_up, LineSetupResult), set_up
    run = await owned.client.call(
        LINE_HAND_IN, LineHandInCommand(line_id=set_up.line.line_id, work_item={"task": "jobs"})
    )
    assert isinstance(run, FactoryRun), run
    return run


def test_an_agent_steps_write_of_only_the_data_of_its_owners_view_runs_without_parking(
    tmp_path, monkeypatch
):
    write_factory(tmp_path / "hub" / "factories" / "boards", BOARDS)

    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            write_view(hub.instance, "board", "Applications", PAGE, DATA)
            hub.control.step_results = [_asks_to_write(PAGE), DRAFTED]
            hub.control.views = {"board": BOARD_VIEW.model_copy(update={"data": REFRESHED})}

            run = await _board_handed_in(hub)
            done = await settled(hub.hub, run.run_id)

            assert done.run.status is FactoryRunStatus.DONE
            assert [attempt.ending for attempt in done.attempts] == [
                StepEnding.ASKED,
                StepEnding.CLEAN,
            ]
            [_, (_, resumed)] = hub.control.steps
            assert isinstance(resumed.step, AgentStepRun)
            assert resumed.step.answered is not None
            assert resumed.step.answered.answer.decision is ApprovalDecision.APPROVE
            held = await call(hub.dispatcher, "view.get", id="board")
            assert isinstance(held, View), held
            assert (held.page, held.data) == (PAGE, REFRESHED)

    asyncio.run(scenario())


@pytest.mark.parametrize("held", ["<h1>Applications</h1>", None])
def test_an_agent_steps_write_of_a_page_its_owner_does_not_hold_parks_its_run(
    tmp_path, monkeypatch, held
):
    write_factory(tmp_path / "hub" / "factories" / "boards", BOARDS)

    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            if held is not None:
                write_view(hub.instance, "board", "Applications", held, DATA)
            hub.control.step_results = [_asks_to_write(PAGE)]

            run = await _board_handed_in(hub)
            await parked(hub.hub, run.run_id)

            assert len(hub.control.steps) == 1
            [attempt] = (await detail(hub.hub, run.run_id)).attempts
            assert isinstance(attempt.ask, WaitingApproval)
            assert attempt.ask.request.page_write == PageWrite(
                title="Applications", page=PAGE, data=REFRESHED, stored_page=held
            )

    asyncio.run(scenario())
