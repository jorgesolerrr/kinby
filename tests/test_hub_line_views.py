"""A view a step of a line's run writes belongs to the line's owner, among its views, and the run's
detail names it, through the hub contract."""

import asyncio
from datetime import UTC, datetime

from kinby.contracts import (
    LINE_HAND_IN,
    LINE_SETUP,
    FactoryRun,
    FactoryRunStatus,
    LineHandInCommand,
    LineSetupCommand,
    LineSetupResult,
    StepEnding,
    StepResult,
    View,
    ViewListResult,
    ViewSpec,
)
from tests.test_factory_runs import settled
from tests.test_hub_factories import write_factory
from tests.test_hub_line_intake import Owned, _started_workbench, owned
from tests.test_hub_line_runs import TASKS
from tests.test_scheduler import call
from tests.test_views import BOARD, DATA

DRAFTED = StepResult(
    ending=StepEnding.CLEAN,
    views=[
        View(
            id="board",
            title="Applications",
            spec=ViewSpec.model_validate(BOARD),
            data=DATA,
            updated_at=datetime(2026, 11, 14, tzinfo=UTC),
        )
    ],
)


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
            assert (held.spec, held.data) == (DRAFTED.views[0].spec, DATA)

    asyncio.run(scenario())


def test_a_view_its_owner_does_not_take_is_named_in_the_attempts_summary(tmp_path, monkeypatch):
    write_factory(tmp_path / "hub" / "factories" / "tasks", {"factory.yaml": TASKS})

    async def scenario() -> None:
        async with owned(tmp_path, monkeypatch) as hub:
            hub.control.step_results = [DRAFTED.model_copy(update={"summary": "Drafted."})]
            del hub.runtime.addresses[str(hub.home)]

            run = await _handed_in(hub)
            done = await settled(hub.hub, run.run_id)

            assert done.run.status is FactoryRunStatus.DONE
            [attempt] = done.attempts
            assert attempt.views == []
            assert attempt.summary.startswith('Drafted.\nThe owner did not take view "board": ')

    asyncio.run(scenario())
