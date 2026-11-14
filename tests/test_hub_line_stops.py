"""Runs on a line take their workbench one step at a time across its lines, hold none while
parked, go back as their outcomes send them, and stop as runs did in instances, through the hub
contract."""

import asyncio
from pathlib import Path
from uuid import UUID

import pytest

from kinby.contracts import (
    FACTORY_RUN_APPROVE,
    FACTORY_RUN_CANCEL,
    FACTORY_RUN_RETRY,
    FACTORY_RUN_SEND_BACK,
    INSTANCE_START,
    LINE_SETUP,
    WORKBENCH_DELETE,
    CodeStepRun,
    ErrorCode,
    ErrorEnvelope,
    FactoryRunApproveCommand,
    FactoryRunCancelCommand,
    FactoryRunRetryCommand,
    FactoryRunSendBackCommand,
    FactoryRunStatus,
    InstanceStartCommand,
    LifecycleOperationResult,
    Line,
    LineSetupCommand,
    OperationState,
    RunFolder,
    StepEnding,
    StepResult,
    WorkbenchDeleteCommand,
)
from kinby.hub import Hub
from tests.test_factory_runs import detail, settled, told
from tests.test_factory_waits import awaiting_approval, parked, signalled, stopped
from tests.test_hub import FakeControl, FakeRuntime, finished_operation, hub_at, hub_client
from tests.test_hub_factories import write_factory
from tests.test_hub_line_runs import TICKETS, _handed_in, _line, _removed
from tests.test_hub_lines import _home

REVIEWS = """\
name: reviews
requires: { image: coder }
work_item: { issue: int }
steps:
  - id: build
    kind: command
    run: ["make build"]
  - {park}
  - id: publish
    kind: command
    run: ["make publish"]
"""
APPROVE = "{ id: read, kind: approve, summary: Read the build. }"
WAIT = "{ id: read, kind: wait, signal: [{ routine: github }], deadline: 7d }"
STOPS = """\
needs_human: { call: report_stop }
ended: { call: report_end }
closing:
  done:
    - headers.X-GitHub-Event: pull_request
      body.action: closed
      body.pull_request.number: "{{issue}}"
      body.pull_request.merged: true
  cancelled:
    - headers.X-GitHub-Event: issues
      body.action: closed
      body.issue.number: "{{issue}}"
"""
REPORT = """\
from kinby.plugins.tools import tool


@tool(write=True)
def report_stop(issue: int, step: str, summary: str) -> None:
    \"\"\"Say on the issue why the run stopped.\"\"\"


@tool(write=True)
def report_end(issue: int) -> None:
    \"\"\"Say on the issue that the run ended.\"\"\"
"""
REDO = """\
  - id: review
    kind: code
    call: label_issue
    outcomes:
      redo: { back: test, max: 2 }
"""


def _hub(tmp_path: Path, control: FakeControl, runtime: FakeRuntime, **factories: str) -> Hub:
    """A hub with the tickets factory, its file extended by *tickets*, and the other factories by
    name."""
    folders = tmp_path / "hub" / "factories"
    write_factory(
        folders / "tickets",
        TICKETS
        | {
            "factory.yaml": TICKETS["factory.yaml"] + factories.pop("tickets", ""),
            "tools/report.py": REPORT,
        },
    )
    for name, file in factories.items():
        write_factory(folders / name, {"factory.yaml": file})
    return hub_at(
        tmp_path / "hub", runtime=runtime, control=control, shipped_factories=tmp_path / "shipped"
    )


async def _started_home(hub: Hub) -> UUID:
    """The home instance, started, so a signal reaches it."""
    home = await _home(hub)
    client = hub_client(hub)
    start = await client.call(INSTANCE_START, InstanceStartCommand(instance_id=home))
    assert isinstance(start, LifecycleOperationResult), start
    assert (await finished_operation(client, start)).state is OperationState.SUCCEEDED
    return home


async def _beside(hub: Hub, line: Line, factory: str) -> Line:
    """*factory* set up on the workbench of *line*."""
    second = await hub_client(hub).call(
        LINE_SETUP, LineSetupCommand(factory=factory, workbench_id=line.workbench_id)
    )
    assert isinstance(second, Line), second
    return second


def test_two_runs_on_two_lines_of_one_workbench_never_run_a_step_at_once(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, reviews=REVIEWS.replace("{park}", APPROVE))
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        tickets = await _line(client, runtime)
        reviews = await _beside(hub, tickets, "reviews")

        first = await _handed_in(client, tickets, 12)
        second = await _handed_in(client, reviews, 12)
        await settled(hub, first.run_id)
        await awaiting_approval(hub, second.run_id)

        assert len(control.steps) == 3
        assert control.most_steps_running == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("park", [APPROVE, WAIT], ids=["approve", "wait"])
def test_a_run_parked_on_its_workbench_lets_another_lines_run_take_its_steps(tmp_path, park):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, reviews=REVIEWS.replace("{park}", park))
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        tickets = await _line(client, runtime)
        reviews = await _beside(hub, tickets, "reviews")
        waiting = await _handed_in(client, reviews, 7)
        await parked(hub, waiting.run_id)

        run = await _handed_in(client, tickets, 12)
        done = await settled(hub, run.run_id)

        assert done.run.status is FactoryRunStatus.DONE
        assert (await detail(hub, waiting.run_id)).run.status is FactoryRunStatus.PARKED

    asyncio.run(scenario())


def test_a_send_back_moves_the_run_to_its_first_step_in_a_fresh_folder_up_to_its_max(tmp_path):
    control = FakeControl()
    redo = StepResult(ending=StepEnding.CLEAN, outcome="redo")
    passed = StepResult(ending=StepEnding.CLEAN)
    control.step_results = [passed, passed, redo, passed, passed, redo, passed, passed, redo]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, tickets=REDO)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)

        run = await _handed_in(client, line, 12)
        stopped = await settled(hub, run.run_id)

        assert (stopped.run.status, stopped.run.step) == (FactoryRunStatus.NEEDS_HUMAN, "review")
        assert [(command.origin.step, command.fresh_folder) for _, command in control.steps] == [
            ("test", False),
            ("label", False),
            ("review", False),
            ("test", True),
            ("label", False),
            ("review", False),
            ("test", True),
            ("label", False),
            ("review", False),
        ]

    asyncio.run(scenario())


def test_the_user_sending_a_run_back_to_its_first_step_cuts_its_folder_again(tmp_path):
    control = FakeControl()
    failed = StepResult(ending=StepEnding.FAILED, summary="The label is gone.")
    control.step_results = [StepResult(ending=StepEnding.CLEAN), failed, failed]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)
        run = await _handed_in(client, line, 12)
        await settled(hub, run.run_id)

        await client.call(FACTORY_RUN_RETRY, FactoryRunRetryCommand(run_id=run.run_id))
        await settled(hub, run.run_id)
        await client.call(
            FACTORY_RUN_SEND_BACK, FactoryRunSendBackCommand(run_id=run.run_id, step="test")
        )
        done = await settled(hub, run.run_id)

        assert done.run.status is FactoryRunStatus.DONE
        assert [(command.origin.step, command.fresh_folder) for _, command in control.steps] == [
            ("test", False),
            ("label", False),
            ("label", False),
            ("test", True),
            ("label", False),
        ]

    asyncio.run(scenario())


def test_a_run_sent_back_from_an_approval_to_its_first_step_cuts_its_folder_again(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, tickets="  - " + APPROVE + "\n")
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)
        run = await _handed_in(client, line, 12)
        await awaiting_approval(hub, run.run_id)

        await client.call(
            FACTORY_RUN_SEND_BACK, FactoryRunSendBackCommand(run_id=run.run_id, step="test")
        )
        await awaiting_approval(hub, run.run_id, attempt=2)
        await client.call(FACTORY_RUN_APPROVE, FactoryRunApproveCommand(run_id=run.run_id))
        done = await settled(hub, run.run_id)

        assert done.run.status is FactoryRunStatus.DONE
        assert [command.fresh_folder for _, command in control.steps] == [False, False, True, False]

    asyncio.run(scenario())


def test_a_run_that_needs_a_human_calls_the_factorys_needs_human_tool_on_its_workbench(tmp_path):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.FAILED, summary="make lint failed.")]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, tickets=STOPS)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)

        run = await _handed_in(client, line, 12)
        await settled(hub, run.run_id)
        async with asyncio.timeout(5):
            while len(control.steps) < 2:
                await asyncio.sleep(0.01)

        endpoint, call = control.steps[-1]
        assert endpoint.address == f"http://kinby-{line.workbench_id}:8787"
        assert call.step == CodeStepRun(call="report_stop", summary="make lint failed.")
        worktree = RunFolder(branch=f"tickets/issue-12-{run.run_id.hex[:8]}")
        assert (call.origin.step, call.attempt, call.folder) == ("test", None, worktree)

    asyncio.run(scenario())


def test_a_run_that_needs_a_human_tells_the_lines_owner_once_for_that_stop(tmp_path):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.FAILED, summary="make lint failed.")]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        home = await _started_home(hub)
        runtime.addresses[str(home)] = f"http://kinby-{home}:8787"
        line = await _line(client, runtime)

        run = await _handed_in(client, line, 12)
        stopped = await settled(hub, run.run_id)

        assert stopped.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert [(stop.run_id, stop.status) for stop in await told(control, 1)] == [
            (run.run_id, FactoryRunStatus.NEEDS_HUMAN)
        ]
        [(endpoint, _)] = control.stops
        assert endpoint.address == f"http://kinby-{home}:8787"

    asyncio.run(scenario())


def test_a_run_that_ends_calls_the_factorys_ended_tool_on_its_workbench_then_loses_its_folder(
    tmp_path,
):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, tickets=STOPS)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)

        run = await _handed_in(client, line, 12)
        await settled(hub, run.run_id)
        removed = await _removed(control)

        endpoint, call = control.steps[-1]
        assert call.step == CodeStepRun(call="report_end")
        assert endpoint.address == f"http://kinby-{line.workbench_id}:8787"
        worktree = RunFolder(branch=f"tickets/issue-12-{run.run_id.hex[:8]}")
        assert (call.origin.step, call.folder) == ("label", worktree)
        assert removed == [worktree]
        assert (await _handed_in(client, line, 12)).run_id != run.run_id

    asyncio.run(scenario())


def test_a_run_whose_ended_call_fails_keeps_its_folder_until_the_call_goes_through(tmp_path):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.FAILED, summary="make lint failed."),
        StepResult(ending=StepEnding.CLEAN),
        StepResult(ending=StepEnding.FAILED, summary="GitHub is down."),
    ]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, tickets=STOPS)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)
        run = await _handed_in(client, line, 12)
        await settled(hub, run.run_id)
        async with asyncio.timeout(5):
            while len(control.steps) < 2:
                await asyncio.sleep(0.01)

        await client.call(FACTORY_RUN_CANCEL, FactoryRunCancelCommand(run_id=run.run_id))
        async with asyncio.timeout(5):
            while len(control.steps) < 3:
                await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)

        assert control.steps[-1][1].step == CodeStepRun(call="report_end")
        assert control.removed_folders == []

    asyncio.run(scenario())


def test_a_work_item_whose_run_still_owes_its_ended_call_returns_that_run(tmp_path):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.CLEAN),
        StepResult(ending=StepEnding.CLEAN),
        StepResult(ending=StepEnding.FAILED, summary="GitHub is down."),
    ]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, tickets=STOPS)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)
        run = await _handed_in(client, line, 12)
        await settled(hub, run.run_id)
        async with asyncio.timeout(5):
            while len(control.steps) < 3:
                await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)

        again = await _handed_in(client, line, 12)

        assert (again.run_id, again.status) == (run.run_id, FactoryRunStatus.DONE)
        assert len(control.steps) == 3

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("event", "body", "status"),
    [
        (
            "pull_request",
            b'{"action": "closed", "pull_request": {"number": 12, "merged": true}}',
            FactoryRunStatus.DONE,
        ),
        ("issues", b'{"action": "closed", "issue": {"number": 12}}', FactoryRunStatus.CANCELLED),
        ("issues", b'{"action": "closed", "issue": {"number": 13}}', FactoryRunStatus.NEEDS_HUMAN),
    ],
    ids=["done", "cancelled", "another-work-item"],
)
def test_a_closing_signal_to_the_lines_owner_ends_a_run_that_needs_a_human(
    tmp_path, event, body, status
):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.FAILED, summary="make lint failed.")]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, tickets=STOPS)
    client = hub_client(hub)

    async def scenario() -> None:
        home = await _started_home(hub)
        line = await _line(client, runtime)
        run = await _handed_in(client, line, 12)
        await settled(hub, run.run_id)

        await signalled(hub, runtime, home, body, event=event)
        await asyncio.sleep(0.05)

        assert (await detail(hub, run.run_id)).run.status is status

    asyncio.run(scenario())


def test_a_signal_to_the_lines_owner_moves_a_run_parked_on_a_wait_on(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, tickets="  - " + WAIT + "\n")
    client = hub_client(hub)

    async def scenario() -> None:
        home = await _started_home(hub)
        line = await _line(client, runtime)
        run = await _handed_in(client, line, 12)
        await parked(hub, run.run_id)

        await signalled(hub, runtime, home, b"{}")
        done = await stopped(hub, run.run_id)

        assert done.run.status is FactoryRunStatus.DONE

    asyncio.run(scenario())


def test_deleting_a_workbench_whose_line_has_unfinished_runs_refuses_and_names_them(tmp_path):
    control = FakeControl()
    control.step_release.clear()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)
        run = await _handed_in(client, line, 12)

        refused = await client.call(
            WORKBENCH_DELETE, WorkbenchDeleteCommand(workbench_id=line.workbench_id)
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert refused.message == (
            'Workbench "cec-backend" has unfinished runs: tickets for issue 12 '
            f"({run.run_id}). Finish or cancel them before deleting it."
        )
        control.step_release.set()

    asyncio.run(scenario())
