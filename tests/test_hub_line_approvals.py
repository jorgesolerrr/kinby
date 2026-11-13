"""A line's run parked at an approve step tells the user by itself, and a send-back hands the step
it goes to the user's notes."""

import asyncio
from pathlib import Path

import pytest

from kinby.contracts import (
    FACTORY_RUN_APPROVE,
    FACTORY_RUN_SEND_BACK,
    FactoryRun,
    FactoryRunApproveCommand,
    FactoryRunSendBackCommand,
    FactoryRunStatus,
    StepEnding,
    StepResult,
)
from kinby.hub import Hub
from tests.test_factory_waits import awaiting_approval, stopped
from tests.test_hub import FakeControl, FakeRuntime, hub_at, hub_client
from tests.test_hub_factories import write_factory
from tests.test_hub_line_asks import _notifications
from tests.test_hub_line_runs import _handed_in, _line
from tests.test_hub_lines import _home

DRAFTS = {
    "factory.yaml": """\
name: tickets
requires: { image: coder }
work_item: { issue: int }
steps:
  - id: draft
    kind: code
    call: draft
    results: { draft: str }
  - id: review
    kind: approve
    summary: Read {{draft}} for issue {{issue}}.
  - id: publish
    kind: command
    run: ["make publish"]
""",
    "tools/draft.py": """\
from kinby.plugins.tools import tool


@tool(write=True)
def draft(issue: int) -> dict[str, str]:
    \"\"\"Draft the issue's answer.\"\"\"
    return {"draft": f"drafts/{issue}.md"}
""",
}
DRAFTED = StepResult(ending=StepEnding.CLEAN, values={"draft": "drafts/12.md"})


def _hub(tmp_path: Path, control: FakeControl, runtime: FakeRuntime) -> Hub:
    write_factory(tmp_path / "hub" / "factories" / "tickets", DRAFTS)
    return hub_at(
        tmp_path / "hub", runtime=runtime, control=control, shipped_factories=tmp_path / "shipped"
    )


def test_a_run_parked_at_an_approval_raises_one_notification_naming_the_run_and_the_summary(
    tmp_path,
):
    control = FakeControl()
    control.step_results = [DRAFTED]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(hub_client(hub), runtime)
        run = await _handed_in(hub_client(hub), line, 12)
        waiting = await awaiting_approval(hub, run.run_id)
        [notification] = await _notifications(hub, line.owner_id)

        assert waiting.run.step == "review"
        assert waiting.attempts[-1].summary == "Read drafts/12.md for issue 12."
        assert notification.title == "Awaits approval"
        assert notification.text == (
            'The run for issue 12 awaits approval at "review": Read drafts/12.md for issue 12.'
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("notes", "results"),
    [
        ("Cite the changelog.", {"notes": "Cite the changelog."}),
        (None, {}),
    ],
    ids=["notes", "no-notes"],
)
def test_a_run_sent_back_from_its_approval_hands_the_step_it_goes_to_the_users_notes(
    tmp_path, notes, results
):
    control = FakeControl()
    control.step_results = [DRAFTED, DRAFTED]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(hub_client(hub), runtime)
        run = await _handed_in(hub_client(hub), line, 12)
        await awaiting_approval(hub, run.run_id)

        sent = await hub_client(hub).call(
            FACTORY_RUN_SEND_BACK,
            FactoryRunSendBackCommand(run_id=run.run_id, step="draft", notes=notes),
        )
        await awaiting_approval(hub, run.run_id, attempt=2)

        assert isinstance(sent, FactoryRun), sent
        [_, (_, redraft)] = control.steps
        assert redraft.origin.step == "draft"
        assert redraft.results == results

    asyncio.run(scenario())


def test_a_run_that_needs_a_human_sent_back_with_notes_hands_them_to_the_step_it_goes_to(
    tmp_path,
):
    control = FakeControl()
    control.step_results = [DRAFTED, StepResult(ending=StepEnding.FAILED), DRAFTED]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(hub_client(hub), runtime)
        run = await _handed_in(hub_client(hub), line, 12)
        await awaiting_approval(hub, run.run_id)
        await hub_client(hub).call(FACTORY_RUN_APPROVE, FactoryRunApproveCommand(run_id=run.run_id))
        assert (await stopped(hub, run.run_id)).run.status is FactoryRunStatus.NEEDS_HUMAN

        sent = await hub_client(hub).call(
            FACTORY_RUN_SEND_BACK,
            FactoryRunSendBackCommand(run_id=run.run_id, step="draft", notes="Ask for access."),
        )
        assert isinstance(sent, FactoryRun), sent
        await awaiting_approval(hub, run.run_id, attempt=2)

        [_, _, (_, redraft)] = control.steps
        assert redraft.origin.step == "draft"
        assert redraft.results == {"notes": "Ask for access."}

    asyncio.run(scenario())
