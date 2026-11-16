"""Factory runs on the hub: hand-in to a line, the per-workbench step queue, attempts and run
events."""

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from functools import partial
from pathlib import Path
from uuid import UUID

import pytest

from kinby.cli.client import ContractClient
from kinby.contracts import (
    FACTORY_RUN_CANCEL,
    FACTORY_RUN_GET,
    FACTORY_RUN_LIST,
    FACTORY_RUN_RETRY,
    FACTORY_RUN_SEND_BACK,
    FACTORY_RUN_SUBSCRIBE,
    INSTANCE_RECREATE,
    INSTANCE_START,
    LINE_HAND_IN,
    LINE_SETUP,
    WORKBENCH_START,
    Capability,
    ClientStepRun,
    CodeStepRun,
    CommandStepRun,
    DeclaredSetup,
    ErrorCode,
    ErrorEnvelope,
    FactoryRun,
    FactoryRunCancelCommand,
    FactoryRunDetail,
    FactoryRunGetCommand,
    FactoryRunListCommand,
    FactoryRunListResult,
    FactoryRunOrigin,
    FactoryRunRetryCommand,
    FactoryRunSendBackCommand,
    FactoryRunStatus,
    FactoryRunSubscribeCommand,
    InstanceRecreateCommand,
    InstanceStartCommand,
    LifecycleOperationResult,
    Line,
    LineHandInCommand,
    LineSetupCommand,
    LineSetupResult,
    OperationState,
    RunFolder,
    RunStop,
    StepEnding,
    StepResult,
    StepRunCommand,
    WorkbenchCreateCommand,
    WorkbenchStartCommand,
)
from kinby.hub import ControlConnectionLost, ControlUndelivered, ControlUnreachable, Hub
from kinby.hub.registry import HubRegistry
from kinby.hub.runs import FactoryRuns, _QueuedReport
from kinby.plugins.intake import intake_tools, run_line_intake
from tests.test_hub import (
    FakeControl,
    FakeImages,
    FakeRuntime,
    created_instance,
    finished_operation,
    hub_client,
    instance_environment,
    started_instance,
)
from tests.test_hub_factories import write_factory
from tests.test_hub_workbenches import CEC_BACKEND, created, finished

FACTORY = """\
name: checks
requires: { image: coder }
work_item: { issue: int }
steps:
  - id: test
    kind: command
    run: ["uv run pytest", "uv run ruff check ."]
    timeout: 10m
"""
FILES = {"factory.yaml": FACTORY}


def factory_hub(
    directory: Path,
    control: FakeControl,
    runtime: FakeRuntime,
    files: dict[str, str] = FILES,
) -> Hub:
    write_factory(directory / "factories" / "checks", files)
    return Hub(
        directory,
        runtime=runtime,
        images=FakeImages(),
        control=control,
        shipped_factories=directory / "shipped",
        private_url="http://hub:8080",
    )


async def home(hub: Hub) -> UUID:
    """Start the hub, which creates its home instance, and wait for that creation."""
    created = (await hub.recover()).home
    assert created is not None
    assert (await finished_operation(hub_client(hub), created)).state is OperationState.SUCCEEDED
    return created.instance_id


async def started_line(
    hub: Hub,
    runtime: FakeRuntime,
    workbench: WorkbenchCreateCommand = CEC_BACKEND,
    factory: str = "checks",
) -> Line:
    """The home instance, and *factory* set up under it on *workbench*, created and started, which
    answers on its control endpoint."""
    if not any(record.home for record in hub.registry.managed_instances()):
        await home(hub)
    client = hub_client(hub)
    workbench_id = (await created(client, workbench)).workbench_id
    start = await client.call(WORKBENCH_START, WorkbenchStartCommand(workbench_id=workbench_id))
    assert (await finished(client, start)).state is OperationState.SUCCEEDED
    runtime.addresses[str(workbench_id)] = f"http://kinby-{workbench_id}:8787"
    setup = await client.call(
        LINE_SETUP, LineSetupCommand(factory=factory, workbench_id=workbench_id)
    )
    assert isinstance(setup, LineSetupResult), setup
    return setup.line


async def handed_in(hub: Hub, line: Line, issue: int) -> FactoryRun:
    run = await hub_client(hub).call(
        LINE_HAND_IN, LineHandInCommand(line_id=line.line_id, work_item={"issue": issue})
    )
    assert isinstance(run, FactoryRun), run
    return run


async def started(
    client: ContractClient, runtime: FakeRuntime, created: LifecycleOperationResult
) -> UUID:
    """Wait for the creation, then start the instance, which answers on its control endpoint."""
    assert (await finished_operation(client, created)).state is OperationState.SUCCEEDED
    start = await client.call(INSTANCE_START, InstanceStartCommand(instance_id=created.instance_id))
    assert isinstance(start, LifecycleOperationResult)
    assert (await finished_operation(client, start)).state is OperationState.SUCCEEDED
    runtime.addresses[str(created.instance_id)] = f"http://kinby-{created.instance_id}:8787"
    return created.instance_id


async def started_home(hub: Hub, runtime: FakeRuntime) -> UUID:
    """Start the hub, then its home instance, which owns its lines and so is told their runs'
    stops on its control endpoint."""
    created = (await hub.recover()).home
    assert created is not None
    return await started(hub_client(hub), runtime, created)


def intake_client(hub: Hub, instance_id: UUID) -> ContractClient:
    """What one instance holds on the hub: its own hub route."""
    scopes, dispatcher = hub.hub_route(instance_id)
    return ContractClient(dispatcher.dispatch, dispatcher.subscribe, scopes)


async def detail(hub: Hub, run_id: UUID) -> FactoryRunDetail:
    found = await hub_client(hub).call(FACTORY_RUN_GET, FactoryRunGetCommand(run_id=run_id))
    assert isinstance(found, FactoryRunDetail), found
    return found


async def settled(hub: Hub, run_id: UUID) -> FactoryRunDetail:
    """The run once it stops moving: done, needing a human, or cancelled."""
    async with asyncio.timeout(5):
        while True:
            found = await detail(hub, run_id)
            if found.run.status not in {FactoryRunStatus.QUEUED, FactoryRunStatus.RUNNING}:
                return found
            await asyncio.sleep(0.01)


@asynccontextmanager
async def run_events(hub: Hub) -> AsyncIterator[list[FactoryRun]]:
    """Collect every run state change the hub publishes while the body runs."""
    stream = await hub_client(hub).subscribe(FACTORY_RUN_SUBSCRIBE, FactoryRunSubscribeCommand())
    assert not isinstance(stream, ErrorEnvelope)
    events: list[FactoryRun] = []

    async def collect() -> None:
        async for item in stream.items:
            assert isinstance(item, FactoryRun)
            events.append(item)

    collecting = asyncio.create_task(collect())
    try:
        yield events
    finally:
        await asyncio.sleep(0.01)
        collecting.cancel()
        with suppress(asyncio.CancelledError):
            await collecting
        await stream.aclose()


def restarted_hub(directory: Path, control: FakeControl, runtime: FakeRuntime) -> Hub:
    """The hub at *directory* opened again, as after a restart."""
    return Hub(
        directory,
        runtime=runtime,
        images=FakeImages(),
        control=control,
        shipped_factories=directory / "shipped",
    )


def test_a_work_item_handed_to_a_line_runs_a_one_step_factory_to_done(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        async with run_events(hub) as events:
            run = await handed_in(hub, line, 7)
            finished = await settled(hub, run.run_id)

        assert (run.factory, run.work_item, run.step) == ("checks", {"issue": 7}, "test")
        assert finished.run.status is FactoryRunStatus.DONE
        assert finished.run.step is None
        [attempt] = finished.attempts
        assert (attempt.step, attempt.attempt, attempt.ending) == ("test", 1, StepEnding.CLEAN)
        assert attempt.ended_at is not None
        [(endpoint, command)] = control.steps
        assert command == StepRunCommand(
            step=CommandStepRun(run=["uv run pytest", "uv run ruff check ."], timeout_seconds=600),
            origin=FactoryRunOrigin(factory="checks", run_id=run.run_id, step="test"),
            attempt=1,
            work_item={"issue": 7},
            folder=RunFolder(branch=f"checks/issue-7-{run.run_id.hex[:8]}"),
        )
        assert endpoint.token == instance_environment(hub, line.workbench_id)["KINBY_CONTROL_TOKEN"]
        assert [(event.run_id, event.status) for event in events] == [
            (run.run_id, FactoryRunStatus.QUEUED),
            (run.run_id, FactoryRunStatus.RUNNING),
            (run.run_id, FactoryRunStatus.DONE),
        ]
        listed = await hub_client(hub).call(
            FACTORY_RUN_LIST, FactoryRunListCommand(factory="checks")
        )
        assert isinstance(listed, FactoryRunListResult)
        assert listed.runs == [finished.run]

    asyncio.run(scenario())


@pytest.mark.parametrize(("retry", "attempts"), [(None, 1), (2, 3)])
def test_a_failing_step_is_tried_again_under_its_retry_then_needs_a_human(
    tmp_path, retry, attempts
):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.FAILED, summary='"uv run pytest" exited with code 1.')
    ] * 3
    runtime = FakeRuntime()
    factory = FACTORY if retry is None else FACTORY + f"    retry: {retry}\n"
    hub = factory_hub(tmp_path / "hub", control, runtime, FILES | {"factory.yaml": factory})

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)
        stopped = await hub_client(hub).call(
            FACTORY_RUN_LIST,
            FactoryRunListCommand(factory="checks", status=FactoryRunStatus.NEEDS_HUMAN),
        )
        done = await hub_client(hub).call(
            FACTORY_RUN_LIST,
            FactoryRunListCommand(factory="checks", status=FactoryRunStatus.DONE),
        )

        assert finished.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert finished.run.step == "test"
        assert [(attempt.attempt, attempt.ending) for attempt in finished.attempts] == [
            (number, StepEnding.FAILED) for number in range(1, attempts + 1)
        ]
        assert finished.attempts[-1].summary == '"uv run pytest" exited with code 1.'
        assert [command.attempt for _, command in control.steps] == list(range(1, attempts + 1))
        assert stopped == FactoryRunListResult(runs=[finished.run], cursor=None)
        assert done == FactoryRunListResult(runs=[], cursor=None)

    asyncio.run(scenario())


def test_a_step_its_workbench_did_not_take_waits_for_it_and_keeps_its_retries(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("kinby.hub.runs.BACKOFF_SECONDS", 0.01)
    control = FakeControl()
    control.step_results = [
        ControlUndelivered(
            "The workbench refused the step: It is draining. It takes no new work until it "
            "starts again."
        ),
        ControlUndelivered("Cannot connect to host kinby-workbench:8787"),
        StepResult(ending=StepEnding.FAILED, summary="Tests failed."),
    ]
    runtime = FakeRuntime()
    hub = factory_hub(
        tmp_path / "hub", control, runtime, FILES | {"factory.yaml": FACTORY + "    retry: 1\n"}
    )

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.DONE
        assert [(attempt.attempt, attempt.ending) for attempt in finished.attempts] == [
            (1, StepEnding.FAILED),
            (2, StepEnding.CLEAN),
        ]
        assert [command.attempt for _, command in control.steps] == [1, 1, 1, 2]

    asyncio.run(scenario())


def test_a_step_waiting_for_an_unreachable_workbench_stays_queued_across_a_restart(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("kinby.hub.runs.BACKOFF_SECONDS", 60)
    control = FakeControl()
    runtime = FakeRuntime()
    directory = tmp_path / "hub"
    hub = factory_hub(directory, control, runtime)

    async def before_the_restart() -> tuple[UUID, UUID, str]:
        line = await started_line(hub, runtime)
        address = runtime.addresses.pop(str(line.workbench_id))
        run = await handed_in(hub, line, 7)
        await asyncio.sleep(0.05)
        waiting = await detail(hub, run.run_id)

        assert waiting.run.status is FactoryRunStatus.QUEUED
        assert waiting.attempts == []
        return run.run_id, line.workbench_id, address

    run_id, workbench_id, address = asyncio.run(before_the_restart())
    hub.close()
    runtime.addresses[str(workbench_id)] = address
    restarted = restarted_hub(directory, control, runtime)

    async def after_the_restart() -> None:
        await restarted.recover()
        finished = await settled(restarted, run_id)

        assert finished.run.status is FactoryRunStatus.DONE
        assert [(attempt.attempt, attempt.ending) for attempt in finished.attempts] == [
            (1, StepEnding.CLEAN)
        ]

    asyncio.run(after_the_restart())


def test_a_step_whose_answer_a_dropped_socket_lost_is_sent_again_for_the_workbenchs_answer(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("kinby.hub.runs.BACKOFF_SECONDS", 0.01)
    control = FakeControl(capabilities=[Capability.WS, Capability.DRAIN, Capability.STEP_ONCE])
    control.step_results = [
        ControlConnectionLost("The control socket closed before it answered (WSMsgType.CLOSED)."),
        ControlConnectionLost("The control socket closed before it answered (WSMsgType.CLOSED)."),
        StepResult(ending=StepEnding.FAILED, summary="Tests failed."),
    ]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert [(attempt.attempt, attempt.ending) for attempt in finished.attempts] == [
            (1, StepEnding.FAILED)
        ]
        assert finished.attempts[0].summary == "Tests failed."
        assert [command for _, command in control.steps] == [control.steps[0][1]] * 3
        assert control.steps[0][1].attempt == 1

    asyncio.run(scenario())


def test_a_step_whose_socket_drops_fails_its_attempt_on_a_workbench_that_holds_no_answer(
    tmp_path,
):
    control = FakeControl()
    control.step_results = [
        ControlConnectionLost("The control socket closed before it answered (WSMsgType.CLOSED).")
    ]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.NEEDS_HUMAN
        [attempt] = finished.attempts
        assert attempt.ending is StepEnding.FAILED
        assert attempt.summary == (
            "The control socket closed before it answered (WSMsgType.CLOSED)."
        )

    asyncio.run(scenario())


def test_ten_runs_on_one_line_take_its_workbench_one_step_at_a_time_in_order(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        runs = [await handed_in(hub, line, issue) for issue in range(1, 11)]
        finished = [await settled(hub, run.run_id) for run in runs]

        assert {found.run.status for found in finished} == {FactoryRunStatus.DONE}
        assert [command.work_item["issue"] for _, command in control.steps] == list(range(1, 11))
        assert control.most_steps_running == 1

    asyncio.run(scenario())


NEEDS_HUMAN_TOOL = '''\
from kinby.plugins.tools import tool


@tool(write=True)
def notify_human() -> dict[str, int]:
    """Record that this run needs a human."""
    return {}
'''
NEEDS_HUMAN_FILES = FILES | {
    "factory.yaml": FACTORY + "needs_human: { call: notify_human }\n",
    "tools/notify.py": NEEDS_HUMAN_TOOL,
}
CODE_STEP_FILES = FILES | {
    "factory.yaml": FACTORY.replace(
        '    kind: command\n    run: ["uv run pytest", "uv run ruff check ."]\n    timeout: 10m\n',
        "    kind: code\n    call: notify_human\n",
    ),
    "tools/notify.py": NEEDS_HUMAN_TOOL,
}


@pytest.mark.parametrize("files", [FILES, CODE_STEP_FILES], ids=["command", "code"])
def test_an_attempt_the_hub_stopped_during_is_no_attempt_and_its_step_is_offered_again(
    tmp_path, files
):
    control = FakeControl()
    control.step_release.clear()
    runtime = FakeRuntime()
    directory = tmp_path / "hub"
    hub = factory_hub(directory, control, runtime, files)

    async def before_the_restart() -> tuple[UUID, UUID]:
        line = await started_line(hub, runtime)
        held = await handed_in(hub, line, 7)
        queued = await handed_in(hub, line, 8)
        async with asyncio.timeout(5):
            while not control.steps:
                await asyncio.sleep(0.01)
        return held.run_id, queued.run_id

    held, queued = asyncio.run(before_the_restart())
    hub.close()
    control.step_release.set()
    restarted = restarted_hub(directory, control, runtime)

    async def after_the_restart() -> None:
        await restarted.recover()
        offered_again = await settled(restarted, held)
        waited = await settled(restarted, queued)

        assert offered_again.run.status is FactoryRunStatus.DONE
        assert [(attempt.attempt, attempt.ending) for attempt in offered_again.attempts] == [
            (1, StepEnding.CLEAN)
        ]
        assert waited.run.status is FactoryRunStatus.DONE
        assert [(command.work_item["issue"], command.attempt) for _, command in control.steps] == [
            (7, 1),
            (8, 1),
            (7, 1),
        ]

    asyncio.run(after_the_restart())


def test_a_needs_human_report_queued_but_not_delivered_is_sent_after_a_restart(tmp_path):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.FAILED)]
    control.step_release.clear()
    runtime = FakeRuntime()
    directory = tmp_path / "hub"
    hub = factory_hub(directory, control, runtime, NEEDS_HUMAN_FILES)

    async def before_the_restart() -> UUID:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        async with asyncio.timeout(5):
            while len(control.steps) < 1:
                await asyncio.sleep(0.01)
            # Let the failed test step through, and hold the report it queues right behind it.
            control.step_release.set()
            control.step_release.clear()
            while len(control.steps) < 2:
                await asyncio.sleep(0.01)
        stopped = await detail(hub, run.run_id)
        assert stopped.run.status is FactoryRunStatus.NEEDS_HUMAN
        return run.run_id

    run_id = asyncio.run(before_the_restart())
    hub.close()
    control.step_release.set()
    restarted = restarted_hub(directory, control, runtime)

    async def after_the_restart() -> None:
        await restarted.recover()
        async with asyncio.timeout(5):
            while len(control.steps) < 3:
                await asyncio.sleep(0.01)
        # The fake records the call as it starts, and the hub marks it delivered once it returns.
        await reported(restarted)

        _, report = control.steps[-1]
        assert report.step == CodeStepRun(call="notify_human", summary="")
        assert (report.origin.run_id, report.origin.step) == (run_id, "test")
        restarted.close()

    asyncio.run(after_the_restart())

    again = restarted_hub(directory, control, runtime)

    async def after_the_second_restart() -> None:
        await again.recover()
        await asyncio.sleep(0.05)
        assert len(control.steps) == 3
        again.close()

    asyncio.run(after_the_second_restart())


def reports(control: FakeControl) -> list[str | None]:
    return [
        command.step.summary
        for _, command in control.steps
        if isinstance(command.step, CodeStepRun) and command.step.call == "notify_human"
    ]


async def reported(hub: Hub) -> None:
    async with asyncio.timeout(5):
        while hub.registry.unreported_runs():
            await asyncio.sleep(0.01)


def held_report_retries(monkeypatch: pytest.MonkeyPatch) -> asyncio.Event:
    """Hold every failed report's retry, in place of its backoff, until the event is set.

    A test then decides when a retry lands, however slow the machine running it.
    """
    release = asyncio.Event()

    async def report_later(runs: FactoryRuns, report: _QueuedReport) -> None:
        await release.wait()
        runs._put_report(report)

    monkeypatch.setattr(FactoryRuns, "_report_later", report_later)
    return release


def test_a_failed_needs_human_call_is_tried_again_until_it_is_delivered(tmp_path, monkeypatch):
    monkeypatch.setattr("kinby.hub.runs.BACKOFF_SECONDS", 0.01)
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.FAILED, summary="Tests failed."),
        ControlUnreachable("Cannot connect to host kinby-workbench:8787"),
        StepResult(ending=StepEnding.FAILED, summary="GitHub is down."),
    ]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, NEEDS_HUMAN_FILES)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        await settled(hub, run.run_id)
        await reported(hub)

        assert reports(control) == ["Tests failed."] * 3

    asyncio.run(scenario())


def test_a_report_for_a_stop_the_user_already_retried_is_not_sent_again(tmp_path, monkeypatch):
    retries = held_report_retries(monkeypatch)
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.FAILED, summary="First failure."),
        StepResult(ending=StepEnding.FAILED, summary="GitHub is down."),
        StepResult(ending=StepEnding.FAILED, summary="Second failure."),
        StepResult(ending=StepEnding.FAILED, summary="GitHub is down."),
    ]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, NEEDS_HUMAN_FILES)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        async with asyncio.timeout(5):
            while len(control.steps) < 2:
                await asyncio.sleep(0.01)
        await hub_client(hub).call(FACTORY_RUN_RETRY, FactoryRunRetryCommand(run_id=run.run_id))
        async with asyncio.timeout(5):
            while len(control.steps) < 4:
                await asyncio.sleep(0.01)
        # Both failed reports retry now, the first one's stop already retried by the user.
        retries.set()
        await reported(hub)

        assert reports(control) == ["First failure.", "Second failure.", "Second failure."]

    asyncio.run(scenario())


def test_a_report_backing_off_does_not_delay_a_step_queued_after_it(tmp_path, monkeypatch):
    monkeypatch.setattr("kinby.hub.runs.BACKOFF_SECONDS", 60)
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.FAILED, summary="Tests failed."),
        StepResult(ending=StepEnding.FAILED, summary="GitHub is down."),
    ]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, NEEDS_HUMAN_FILES)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        await handed_in(hub, line, 7)
        async with asyncio.timeout(5):
            while len(control.steps) < 2:
                await asyncio.sleep(0.01)
        later = await handed_in(hub, line, 8)
        finished = await settled(hub, later.run_id)

        assert finished.run.status is FactoryRunStatus.DONE

    asyncio.run(scenario())


def test_a_needs_human_call_that_fails_a_thousand_times_is_still_tried_again(tmp_path, monkeypatch):
    monkeypatch.setattr("kinby.hub.runs.BACKOFF_SECONDS", 0.0)
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.FAILED, summary="Tests failed.")] + [
        StepResult(ending=StepEnding.FAILED, summary="GitHub is down.")
    ] * 1100
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, NEEDS_HUMAN_FILES)

    async def scenario() -> None:
        await started_home(hub, runtime)
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        await settled(hub, run.run_id)
        async with asyncio.timeout(30):
            while hub.registry.unreported_runs():
                await asyncio.sleep(0.01)

        assert len(reports(control)) == 1101

    asyncio.run(scenario())


def test_a_run_its_resume_cannot_queue_is_reported_and_told_once(tmp_path):
    """Resume reads the reports and stops left from before the restart ahead of queueing runs.

    A recovered run whose step is gone stops while resume queues it, which queues that fresh
    stop's report and telling, so the pass over leftovers must not queue them a second time.
    """
    control = FakeControl()
    control.step_release.clear()
    runtime = FakeRuntime()
    directory = tmp_path / "hub"
    hub = factory_hub(directory, control, runtime, NEEDS_HUMAN_FILES)

    async def before_the_restart() -> UUID:
        await started_home(hub, runtime)
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        async with asyncio.timeout(5):
            while not control.steps:
                await asyncio.sleep(0.01)
        return run.run_id

    run_id = asyncio.run(before_the_restart())
    hub.close()
    control.step_release.set()
    renamed = NEEDS_HUMAN_FILES["factory.yaml"].replace("  - id: test\n", "  - id: tests\n")
    write_factory(directory / "factories" / "checks", {"factory.yaml": renamed})
    restarted = restarted_hub(directory, control, runtime)

    async def after_the_restart() -> None:
        await restarted.recover()
        stopped = await settled(restarted, run_id)
        stops = await told(control, 1)

        assert stopped.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert reports(control) == ['Factory "checks" has no step "test" any more.']
        assert [stop.run_id for stop in stops] == [run_id]
        restarted.close()

    asyncio.run(after_the_restart())


def test_a_reports_delivery_only_marks_reported_its_own_stop(tmp_path):
    """A report that completes late never marks a fresher, still-undelivered stop as reported.

    The user can retry while a run's needs_human report is still in flight. If that retry fails
    again before the report completes, the late report must not clear the new stop's flag.
    """
    registry = HubRegistry(tmp_path / "hub")
    run, _ = registry.open_run(UUID(int=1), "checks", {"issue": 7}, "test")

    registry.begin_attempt(run.run_id, "test")
    registry.end_attempt(run.run_id, StepResult(ending=StepEnding.FAILED, summary="first failure"))
    registry.move_run(run.run_id, FactoryRunStatus.NEEDS_HUMAN, "test")
    stale_generation = registry.stop_generation(run.run_id)
    assert registry.unreported_runs() == [registry.run(run.run_id)]

    registry.restart_run(run.run_id, "test")
    registry.begin_attempt(run.run_id, "test")
    registry.end_attempt(run.run_id, StepResult(ending=StepEnding.FAILED, summary="second failure"))
    registry.move_run(run.run_id, FactoryRunStatus.NEEDS_HUMAN, "test")

    # The first report, for the stop the retry already left behind, finally completes.
    registry.mark_reported(run.run_id, stale_generation)
    assert registry.unreported_runs() == [registry.run(run.run_id)]

    registry.mark_reported(run.run_id, registry.stop_generation(run.run_id))
    assert registry.unreported_runs() == []


ENDED_TOOL = '''\
from kinby.plugins.tools import tool


@tool(write=True)
def report_ended() -> dict[str, int]:
    """Record that this run ended."""
    return {}
'''
ENDED_FILES = NEEDS_HUMAN_FILES | {
    "factory.yaml": FACTORY
    + "    outcomes: { nothing: stop }\n"
    + "needs_human: { call: notify_human }\n"
    + "ended: { call: report_ended }\n",
    "tools/ended.py": ENDED_TOOL,
}


def ended_calls(control: FakeControl) -> list[StepRunCommand]:
    return [
        command for _, command in control.steps if command.step == CodeStepRun(call="report_ended")
    ]


async def called_ended(hub: Hub, control: FakeControl) -> list[StepRunCommand]:
    """The factory's ended calls, once the hub holds none still to make."""
    async with asyncio.timeout(5):
        while not ended_calls(control) or hub.registry.uncalled_ends():
            await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    return ended_calls(control)


@pytest.mark.parametrize(
    ("result", "status"),
    [
        (StepResult(ending=StepEnding.CLEAN, values={"pr": 12}), FactoryRunStatus.DONE),
        (
            StepResult(ending=StepEnding.CLEAN, outcome="nothing", values={"pr": 12}),
            FactoryRunStatus.CANCELLED,
        ),
    ],
    ids=["done", "stop-outcome"],
)
def test_a_run_that_ends_gets_the_factorys_ended_call_once_with_its_values(
    tmp_path, result, status
):
    control = FakeControl()
    control.step_results = [result]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, ENDED_FILES)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)
        [ended] = await called_ended(hub, control)

        assert finished.run.status is status
        assert (ended.origin.run_id, ended.origin.step) == (run.run_id, "test")
        assert (ended.work_item, ended.results) == ({"issue": 7}, {"pr": 12})
        assert (ended.hook, ended.attempt) == (None, None)

    asyncio.run(scenario())


def test_an_ended_call_not_made_before_the_hub_stopped_is_made_after_a_restart(tmp_path):
    control = FakeControl()
    control.step_release.clear()
    runtime = FakeRuntime()
    directory = tmp_path / "hub"
    hub = factory_hub(directory, control, runtime, ENDED_FILES)

    async def before_the_restart() -> UUID:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        async with asyncio.timeout(5):
            while len(control.steps) < 1:
                await asyncio.sleep(0.01)
            # Let the test step through, and hold the ended call it queues right behind it.
            control.step_release.set()
            control.step_release.clear()
            while len(control.steps) < 2:
                await asyncio.sleep(0.01)
        return run.run_id

    run_id = asyncio.run(before_the_restart())
    hub.close()
    control.step_release.set()
    restarted = restarted_hub(directory, control, runtime)

    async def after_the_restart() -> None:
        await restarted.recover()
        first, again = await called_ended(restarted, control)

        assert first == again
        assert again.origin.run_id == run_id
        restarted.close()

    asyncio.run(after_the_restart())
    once_more = restarted_hub(directory, control, runtime)

    async def after_the_second_restart() -> None:
        await once_more.recover()
        await asyncio.sleep(0.05)

        assert len(ended_calls(control)) == 2
        once_more.close()

    asyncio.run(after_the_second_restart())


async def told(control: FakeControl, count: int) -> list[RunStop]:
    """The run stops the hub told once *count* of them reached their owner, and no more."""
    async with asyncio.timeout(5):
        while len(control.stops) < count:
            await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    return [stop for _, stop in control.stops]


@pytest.mark.parametrize(
    ("results", "status"),
    [
        ([], FactoryRunStatus.DONE),
        ([StepResult(ending=StepEnding.FAILED)], FactoryRunStatus.NEEDS_HUMAN),
    ],
)
def test_a_run_that_stops_tells_the_lines_owner_once_with_its_record(tmp_path, results, status):
    control = FakeControl()
    control.step_results = results
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, NEEDS_HUMAN_FILES)

    async def scenario() -> None:
        await started_home(hub, runtime)
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is status
        assert await told(control, 1) == [
            RunStop(
                run_id=run.run_id,
                factory="checks",
                work_item={"issue": 7},
                status=status,
                generation=1,
                attempts=finished.attempts,
                workbench="cec-backend",
            )
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("method", "status", "after_the_restart"),
    [
        (FACTORY_RUN_RETRY, FactoryRunStatus.DONE, 1),
        (FACTORY_RUN_CANCEL, FactoryRunStatus.CANCELLED, 0),
    ],
)
def test_a_run_that_stops_again_after_needing_a_human_tells_only_its_new_attempts(
    tmp_path, method, status, after_the_restart
):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.FAILED, summary="Tests failed.")]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        await started_home(hub, runtime)
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        await settled(hub, run.run_id)
        await told(control, 1)
        await hub_client(hub).call(method, method.command(run_id=run.run_id))
        finished = await settled(hub, run.run_id)
        first, second = await told(control, 2)

        assert (first.status, first.generation) == (FactoryRunStatus.NEEDS_HUMAN, 1)
        assert first.attempts == finished.attempts[:1]
        assert (second.status, second.generation) == (status, 2)
        assert second.attempts == finished.attempts[1:]
        assert len(second.attempts) == after_the_restart

    asyncio.run(scenario())


def test_a_run_stopped_before_run_stops_were_recorded_tells_only_attempts_after_that_stop(tmp_path):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.FAILED, summary="Tests failed.")]
    runtime = FakeRuntime()
    directory = tmp_path / "hub"
    hub = factory_hub(directory, control, runtime)

    async def before_the_upgrade() -> UUID:
        await started_home(hub, runtime)
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        await settled(hub, run.run_id)
        await told(control, 1)
        return run.run_id

    run_id = asyncio.run(before_the_upgrade())
    hub.close()
    with sqlite3.connect(directory / "registry.sqlite") as connection:
        connection.execute("DROP TABLE run_stops")
    control.stops.clear()
    upgraded = factory_hub(directory, control, runtime)

    async def after_the_upgrade() -> None:
        await upgraded.recover()
        await hub_client(upgraded).call(FACTORY_RUN_RETRY, FactoryRunRetryCommand(run_id=run_id))
        finished = await settled(upgraded, run_id)
        [stop] = await told(control, 1)

        assert (stop.status, stop.generation) == (FactoryRunStatus.DONE, 2)
        assert stop.attempts == finished.attempts[1:]
        upgraded.close()

    asyncio.run(after_the_upgrade())


def test_a_run_stop_not_told_before_the_hub_stopped_is_told_after_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("kinby.hub.runs.BACKOFF_SECONDS", 60)
    control = FakeControl()
    control.stop_failures = [ControlUnreachable("Cannot connect to host kinby-home:8787")]
    runtime = FakeRuntime()
    directory = tmp_path / "hub"
    hub = factory_hub(directory, control, runtime)

    async def before_the_restart() -> UUID:
        await started_home(hub, runtime)
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        await settled(hub, run.run_id)
        await told(control, 1)
        return run.run_id

    run_id = asyncio.run(before_the_restart())
    hub.close()
    restarted = restarted_hub(directory, control, runtime)

    async def after_the_restart() -> None:
        await restarted.recover()
        _, again = await told(control, 2)

        assert (again.run_id, again.status, again.generation) == (
            run_id,
            FactoryRunStatus.DONE,
            1,
        )
        restarted.close()

    asyncio.run(after_the_restart())
    once_more = restarted_hub(directory, control, runtime)

    async def after_the_second_restart() -> None:
        await once_more.recover()
        await asyncio.sleep(0.05)

        assert len(control.stops) == 2
        once_more.close()

    asyncio.run(after_the_second_restart())


def test_an_instance_gets_a_hub_url_to_hand_its_lines_work_and_raise_notifications(tmp_path):
    hub = factory_hub(tmp_path / "hub", FakeControl(), FakeRuntime())

    async def scenario() -> None:
        created = await created_instance(hub_client(hub))
        environment = instance_environment(hub, created.instance_id)

        assert (
            environment["KINBY_HUB_URL"] == f"http://hub:8080/instances/{created.instance_id}/hub"
        )
        assert environment["KINBY_HUB_SCOPES"] == "line:intake,line:owner,notification:raise"

    asyncio.run(scenario())


def test_an_instance_created_before_its_hub_route_gets_it_on_recreation(tmp_path):
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", FakeControl(), runtime)

    async def scenario() -> None:
        client = hub_client(hub)
        alice = (await started_instance(client, hub)).instance_id
        environment = instance_environment(hub, alice)
        del environment["KINBY_HUB_URL"]
        (hub.instances_directory / str(alice) / ".env").write_text(
            "".join(f"{name}='{value}'\n" for name, value in environment.items())
        )
        recreated = await client.call(INSTANCE_RECREATE, InstanceRecreateCommand(instance_id=alice))
        assert isinstance(recreated, LifecycleOperationResult)
        assert (await finished_operation(client, recreated)).state is OperationState.SUCCEEDED

        hub_url = f"http://hub:8080/instances/{alice}/hub"
        assert instance_environment(hub, alice)["KINBY_HUB_URL"] == hub_url
        assert runtime.created[-1].env["KINBY_HUB_URL"] == hub_url

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("variable", "scopes", "tools"),
    [
        ("KINBY_HUB_URL", "line:intake,notification:raise", (run_line_intake,)),
        ("KINBY_HUB_URL", "notification:raise", ()),
        ("KINBY_INTAKE_URL", "line:intake", ()),
    ],
    ids=["line-intake-scope", "other-scope", "intake-url"],
)
def test_only_an_instance_its_hub_grants_an_intake_scope_has_its_intake_tool(
    monkeypatch, variable, scopes, tools
):
    monkeypatch.setenv(variable, "http://hub:8080/instances/x/hub")
    monkeypatch.setenv("KINBY_HUB_SCOPES", scopes)

    assert intake_tools() == tools


HANDOFF = """\
name: checks
requires: { image: coder }
work_item: { issue: int }
steps:
  - id: implement
    kind: command
    run: ["make implement"]
    check: read_branch
    results: { branch: str, committed: bool }
    retry: 1
  - id: open-pr
    kind: code
    call: open_pull_request
    requires: [branch, committed]
    results: { pr: int }
done_requires: [pr]
"""
TOOLS = '''\
from kinby.plugins.tools import tool


@tool(write=True)
def open_pull_request(branch: str) -> dict[str, int]:
    """Open the pull request of the branch."""
    return {"pr": 42}
'''
READ_BRANCH = '''\
from kinby.plugins.hooks import StepEnd, hook


@hook
def read_branch(end: StepEnd) -> None:
    """Read the branch the step pushed."""
'''
HANDOFF_FILES = FILES | {
    "factory.yaml": HANDOFF,
    "tools/github.py": TOOLS,
    "hooks/branch.py": READ_BRANCH,
}
BRANCH = {"branch": "agent/7", "committed": True}


def test_the_hub_hands_each_step_its_check_and_the_values_earlier_steps_recorded(tmp_path):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.CLEAN, values=BRANCH),
        StepResult(ending=StepEnding.CLEAN, values={"pr": 42}),
    ]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, HANDOFF_FILES)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)

        folder = RunFolder(branch=f"checks/issue-7-{run.run_id.hex[:8]}")
        assert finished.run.status is FactoryRunStatus.DONE
        assert [command for _, command in control.steps] == [
            StepRunCommand(
                step=CommandStepRun(run=["make implement"]),
                hook="read_branch",
                origin=FactoryRunOrigin(factory="checks", run_id=run.run_id, step="implement"),
                attempt=1,
                work_item={"issue": 7},
                folder=folder,
            ),
            StepRunCommand(
                step=CodeStepRun(call="open_pull_request"),
                origin=FactoryRunOrigin(factory="checks", run_id=run.run_id, step="open-pr"),
                attempt=1,
                work_item={"issue": 7},
                results=BRANCH,
                folder=folder,
            ),
        ]
        assert [attempt.values for attempt in finished.attempts] == [BRANCH, {"pr": 42}]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("recorded", "summary"),
    [
        (
            {"committed": True},
            'Step "open-pr" requires "branch", which this step did not record.',
        ),
        (
            {"branch": "agent/7", "committed": False},
            'Step "open-pr" requires "committed", which this step recorded as false.',
        ),
    ],
)
def test_a_failing_requires_check_fails_the_step_that_should_have_produced_the_value(
    tmp_path, recorded, summary
):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.CLEAN, values=recorded),
        StepResult(ending=StepEnding.CLEAN, values=BRANCH),
        StepResult(ending=StepEnding.CLEAN, values={"pr": 42}),
    ]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, HANDOFF_FILES)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.DONE
        assert [(a.step, a.attempt, a.ending) for a in finished.attempts] == [
            ("implement", 1, StepEnding.CLEAN),
            ("implement", 2, StepEnding.FAILED),
            ("implement", 3, StepEnding.CLEAN),
            ("open-pr", 1, StepEnding.CLEAN),
        ]
        assert finished.attempts[1].summary == summary
        assert [command.step.kind for _, command in control.steps] == [
            "command",
            "command",
            "code",
        ]

    asyncio.run(scenario())


def test_a_requires_check_that_fails_past_the_producing_steps_retry_needs_a_human(tmp_path):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.CLEAN)] * 2
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, HANDOFF_FILES)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert finished.run.step == "implement"
        assert {attempt.step for attempt in finished.attempts} == {"implement"}
        assert [command.step.kind for _, command in control.steps] == ["command", "command"]

    asyncio.run(scenario())


def test_a_value_is_never_read_from_a_step_results_summary(tmp_path):
    control = FakeControl()
    control.step_results = [
        StepResult(
            ending=StepEnding.CLEAN,
            summary='Pushed the branch.\n{"branch": "agent/7", "committed": true}',
        )
    ] * 2
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, HANDOFF_FILES)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert finished.attempts[0].summary.startswith("Pushed the branch.")
        assert finished.attempts[0].values == {}
        assert finished.attempts[1].summary == (
            'Step "open-pr" requires "branch", which this step did not record.'
        )

    asyncio.run(scenario())


SWITCH = HANDOFF.replace(
    "  - id: open-pr\n    kind: code\n    call: open_pull_request\n",
    '  - id: open-pr\n    kind: command\n    run: ["git switch {{branch}}", '
    '"make publish ISSUE={{issue}}"]\n',
).replace("    results: { pr: int }\ndone_requires: [pr]\n", "")


def test_a_command_names_a_value_of_the_run_as_one_argument(tmp_path):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.CLEAN, values={"branch": "agent/7 dark", "committed": True})
    ]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, HANDOFF_FILES | {"factory.yaml": SWITCH})

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        await settled(hub, run.run_id)

        _, publish = control.steps[-1]
        assert publish.step == CommandStepRun(
            run=["git switch 'agent/7 dark'", "make publish ISSUE=7"]
        )

    asyncio.run(scenario())


def test_a_value_a_command_names_is_required_of_the_step_that_should_have_produced_it(tmp_path):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.CLEAN, values={"committed": True}),
        StepResult(ending=StepEnding.CLEAN, values=BRANCH),
    ]
    runtime = FakeRuntime()
    factory = SWITCH.replace("    requires: [branch, committed]\n", "")
    hub = factory_hub(tmp_path / "hub", control, runtime, HANDOFF_FILES | {"factory.yaml": factory})

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.DONE
        assert [(a.step, a.ending) for a in finished.attempts] == [
            ("implement", StepEnding.CLEAN),
            ("implement", StepEnding.FAILED),
            ("implement", StepEnding.CLEAN),
            ("open-pr", StepEnding.CLEAN),
        ]
        assert finished.attempts[1].summary == (
            'Step "open-pr" requires "branch", which this step did not record.'
        )

    asyncio.run(scenario())


def test_a_run_whose_done_requires_fails_does_not_finish_as_done(tmp_path):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.CLEAN, values=BRANCH),
        StepResult(ending=StepEnding.CLEAN, summary="Opened nothing."),
    ]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, HANDOFF_FILES)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert finished.run.step == "open-pr"
        last = finished.attempts[-1]
        assert (last.step, last.ending) == ("open-pr", StepEnding.FAILED)
        assert last.summary == (
            'Opened nothing.\nThe run\'s done_requires needs "pr", which no step recorded.'
        )

    asyncio.run(scenario())


def test_a_run_from_before_runs_named_their_instance_belongs_to_its_factorys_newest_instance(
    tmp_path,
):
    directory = tmp_path / "hub"
    hub = factory_hub(directory, FakeControl(), FakeRuntime())
    client = hub_client(hub)

    async def before() -> tuple[UUID, UUID]:
        older = (await created_instance(client, name="older")).instance_id
        coder = (await created_instance(client, name="coder")).instance_id
        with sqlite3.connect(directory / "registry.sqlite") as connection:
            connection.execute(
                "CREATE TABLE factory_members (instance_id TEXT PRIMARY KEY, factory TEXT NOT NULL,"
                " name TEXT NOT NULL, recipe TEXT, setup TEXT NOT NULL, baseline TEXT)"
            )
            connection.executemany(
                "INSERT INTO factory_members (instance_id, factory, name, recipe, setup) "
                "VALUES (?, 'checks', 'coder', NULL, ?)",
                [
                    (str(member), DeclaredSetup(setup_fields=[]).model_dump_json())
                    for member in (older, coder)
                ],
            )
        run, _ = hub.registry.open_run(coder, "checks", {"issue": 7}, "test")
        return coder, run.run_id

    coder, run_id = asyncio.run(before())
    hub.close()
    with sqlite3.connect(directory / "registry.sqlite") as connection:
        connection.execute("DROP INDEX factory_runs_by_instance")
        connection.execute("DROP INDEX factory_runs_by_creation")
        connection.execute("ALTER TABLE factory_runs DROP COLUMN instance_id")
    reopened = factory_hub(directory, FakeControl(), FakeRuntime())

    found = asyncio.run(detail(reopened, run_id))

    assert found.run.instance_id == coder
    with sqlite3.connect(directory / "registry.sqlite") as connection:
        tables = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'factory_members'"
        ).fetchall()
    assert tables == []


def test_a_value_of_another_type_than_its_step_declares_fails_the_step(tmp_path):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.CLEAN, values=BRANCH),
        StepResult(ending=StepEnding.CLEAN, values={"pr": "42"}),
    ]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, HANDOFF_FILES)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert finished.run.step == "open-pr"
        last = finished.attempts[-1]
        assert (last.step, last.ending) == ("open-pr", StepEnding.FAILED)
        assert last.summary == 'Step "open-pr" declares "pr" as int, but recorded a str.'

    asyncio.run(scenario())


REVIEW = """\
name: checks
requires: { image: coder }
work_item: { issue: int }
steps:
  - id: fix
    kind: command
    run: ["make fix"]
  - id: review
    kind: command
    run: ["make review"]
    check: read_verdict
    outcomes: { clean: next, changes: { back: fix, max: 3 }, hopeless: stop, merged: done }
  - id: publish
    kind: command
    run: ["make publish"]
"""
VERDICT = '''\
from kinby.plugins.hooks import StepEnd, hook


@hook
def read_verdict(end: StepEnd) -> None:
    """Read the review's verdict from the workspace."""
'''
REVIEW_FILES = FILES | {"factory.yaml": REVIEW, "hooks/verdict.py": VERDICT}
FIXED = StepResult(ending=StepEnding.CLEAN)


def reviewed(outcome: str) -> StepResult:
    return StepResult(ending=StepEnding.CLEAN, outcome=outcome)


def test_work_sent_back_holds_no_value_from_the_pass_it_left(tmp_path):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.CLEAN, values={"committed": True}),
        reviewed("changes"),
        FIXED,
    ]
    runtime = FakeRuntime()
    factory = REVIEW.replace(
        '    run: ["make fix"]\n', '    run: ["make fix"]\n    results: { committed: bool }\n'
    ).replace("    check: read_verdict\n", "    check: read_verdict\n    requires: [committed]\n")
    hub = factory_hub(tmp_path / "hub", control, runtime, REVIEW_FILES | {"factory.yaml": factory})

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert finished.run.step == "fix"
        assert [(a.step, a.ending) for a in finished.attempts] == [
            ("fix", StepEnding.CLEAN),
            ("review", StepEnding.CLEAN),
            ("fix", StepEnding.CLEAN),
            ("fix", StepEnding.FAILED),
        ]
        assert finished.attempts[-1].summary == (
            'Step "review" requires "committed", which this step did not record.'
        )
        assert [command.results for _, command in control.steps] == [{}, {"committed": True}, {}]

    asyncio.run(scenario())


def test_every_attempt_at_the_step_work_went_back_to_holds_the_values_sent_with_it(tmp_path):
    control = FakeControl()
    failed = StepResult(ending=StepEnding.FAILED)
    control.step_results = [
        FIXED,
        StepResult(ending=StepEnding.CLEAN, outcome="changes", values={"feedback": "Rename it."}),
        failed,
        failed,
        FIXED,
    ]
    runtime = FakeRuntime()
    factory = REVIEW.replace('run: ["make fix"]\n', 'run: ["make fix"]\n    retry: 1\n').replace(
        "check: read_verdict\n", "check: read_verdict\n    results: { feedback: str }\n"
    )
    hub = factory_hub(tmp_path / "hub", control, runtime, REVIEW_FILES | {"factory.yaml": factory})

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        stopped = await settled(hub, run.run_id)
        await hub_client(hub).call(FACTORY_RUN_RETRY, FactoryRunRetryCommand(run_id=run.run_id))
        finished = await settled(hub, run.run_id)

        assert (stopped.run.status, stopped.run.step) == (FactoryRunStatus.NEEDS_HUMAN, "fix")
        assert finished.run.status is FactoryRunStatus.DONE
        sent = {"feedback": "Rename it."}
        assert [(command.origin.step, command.results) for _, command in control.steps] == [
            ("fix", {}),
            ("review", {}),
            ("fix", sent),
            ("fix", sent),
            ("fix", sent),
            ("review", sent),
            ("publish", sent),
        ]

    asyncio.run(scenario())


REPAIR = REVIEW.replace(
    "    check: read_verdict\n"
    "    outcomes: { clean: next, changes: { back: fix, max: 3 }, hopeless: stop, merged: done }\n",
    "    failed: { back: fix, max: 2 }\n",
)
REPAIR_FILES = FILES | {"factory.yaml": REPAIR}


def failing(summary: str) -> StepResult:
    return StepResult(ending=StepEnding.FAILED, summary=summary)


def test_a_failure_goes_back_where_its_step_sends_it_with_what_failed_up_to_its_max(tmp_path):
    control = FakeControl()
    control.step_results = [
        FIXED,
        failing('"ruff check ." exited with code 1.'),
        FIXED,
        failing('"pytest" exited with code 1.'),
        FIXED,
        failing('"pytest" exited with code 1.'),
    ]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, REPAIR_FILES)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        stopped = await settled(hub, run.run_id)

        assert (stopped.run.status, stopped.run.step) == (FactoryRunStatus.NEEDS_HUMAN, "review")
        assert [
            command.results for _, command in control.steps if command.origin.step == "fix"
        ] == [
            {},
            {"failure": '"ruff check ." exited with code 1.'},
            {"failure": '"pytest" exited with code 1.'},
        ]
        assert stopped.attempts[-1].summary == (
            '"pytest" exited with code 1.\n'
            'Step "review" sent its failures back to "fix" 2 times, its max.'
        )

    asyncio.run(scenario())


def test_a_step_that_needs_a_human_stops_the_run_even_where_its_failures_go_back(tmp_path):
    control = FakeControl()
    missing = 'Workbench "cec-backend" holds no repository commands named "checks".'
    control.step_results = [FIXED, StepResult(ending=StepEnding.NEEDS_HUMAN, summary=missing)]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, REPAIR_FILES)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        stopped = await settled(hub, run.run_id)

        assert (stopped.run.status, stopped.run.step) == (FactoryRunStatus.NEEDS_HUMAN, "review")
        assert [(a.step, a.ending) for a in stopped.attempts] == [
            ("fix", StepEnding.CLEAN),
            ("review", StepEnding.NEEDS_HUMAN),
        ]
        assert stopped.attempts[-1].summary == missing

    asyncio.run(scenario())


def test_a_failure_sent_back_reaches_every_attempt_at_its_step_and_a_pass_moves_on(tmp_path):
    control = FakeControl()
    failure = '"pytest" exited with code 1.'
    control.step_results = [FIXED, failing(failure), failing("Fix failed."), FIXED, FIXED, FIXED]
    runtime = FakeRuntime()
    factory = REPAIR.replace('run: ["make fix"]\n', 'run: ["make fix"]\n    retry: 1\n')
    hub = factory_hub(tmp_path / "hub", control, runtime, FILES | {"factory.yaml": factory})

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.DONE
        assert [command.origin.step for _, command in control.steps] == [
            "fix",
            "review",
            "fix",
            "fix",
            "review",
            "publish",
        ]
        assert [command.results for _, command in control.steps][2:4] == [{"failure": failure}] * 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("verdict", "status", "step", "attempted"),
    [
        ("hopeless", FactoryRunStatus.CANCELLED, "review", ["fix", "review"]),
        ("merged", FactoryRunStatus.DONE, None, ["fix", "review"]),
        ("clean", FactoryRunStatus.DONE, None, ["fix", "review", "publish"]),
        (None, FactoryRunStatus.DONE, None, ["fix", "review", "publish"]),
    ],
)
def test_a_steps_outcome_moves_the_run_on_finishes_it_or_cancels_it(
    tmp_path, verdict, status, step, attempted
):
    control = FakeControl()
    control.step_results = [FIXED, StepResult(ending=StepEnding.CLEAN, outcome=verdict)]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, REVIEW_FILES)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)

        assert (finished.run.status, finished.run.step) == (status, step)
        assert [attempt.step for attempt in finished.attempts] == attempted

    asyncio.run(scenario())


def test_an_outcome_the_step_does_not_declare_fails_its_attempt(tmp_path):
    control = FakeControl()
    control.step_results = [FIXED, reviewed("lgtm"), reviewed("clean")]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, REVIEW_FILES)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert finished.run.step == "review"
        last = finished.attempts[-1]
        assert (last.step, last.ending, last.outcome) == ("review", StepEnding.FAILED, "lgtm")
        assert last.summary == 'Step "review" has no outcome "lgtm".'

    asyncio.run(scenario())


def test_a_done_outcome_finishes_the_run_only_when_done_requires_holds(tmp_path):
    control = FakeControl()
    control.step_results = [FIXED, reviewed("merged")]
    runtime = FakeRuntime()
    factory = REVIEW + "    results: { pr: int }\ndone_requires: [pr]\n"
    hub = factory_hub(tmp_path / "hub", control, runtime, REVIEW_FILES | {"factory.yaml": factory})

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)

        assert (finished.run.status, finished.run.step) == (FactoryRunStatus.NEEDS_HUMAN, "review")
        assert finished.attempts[-1].ending is StepEnding.FAILED
        assert finished.attempts[-1].summary == (
            'The run\'s done_requires needs "pr", which no step recorded.'
        )

    asyncio.run(scenario())


AGENT = """\
name: checks
requires: { image: coder }
work_item: { issue: int }
steps:
  - id: implement
    kind: agent
    engine: claude
    prompt: prompts/implement.md
"""


@pytest.mark.parametrize(("retry", "attempts"), [("", 2), ("    retry: 0\n", 1)])
def test_an_agent_step_is_tried_once_more_by_default(tmp_path, retry, attempts):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.FAILED)] * 2
    runtime = FakeRuntime()
    files = FILES | {"factory.yaml": AGENT + retry, "prompts/implement.md": "Implement it.\n"}
    hub = factory_hub(tmp_path / "hub", control, runtime, files)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert [(a.step, a.ending) for a in finished.attempts] == [
            ("implement", StepEnding.FAILED)
        ] * attempts

    asyncio.run(scenario())


CLIENT = """\
name: checks
requires: { image: coder }
work_item: { issue: int }
steps:
  - id: implement
    kind: agent
    engine: claude
    prompt: prompts/implement.md
    timeout: 30m
  - id: fix
    kind: agent
    engine: claude
    prompt: prompts/fix.md
    resume: implement
    model: claude-opus-5-5
    effort: high
"""
CLIENT_FILES = FILES | {
    "factory.yaml": CLIENT,
    "prompts/implement.md": "Implement it.\n",
    "prompts/fix.md": "Fix it.\n",
}


@pytest.mark.parametrize(
    ("recorded", "resumed"),
    [
        (["claude-1"] * 4, ["claude-1"] * 3),
        (["claude-1", "claude-2", "claude-2", "claude-2"], ["claude-1", "claude-2", "claude-2"]),
        (["claude-1", "claude-1", "claude-2", "claude-2"], ["claude-1", "claude-1", "claude-2"]),
    ],
    ids=["same", "answer-new", "check-new"],
)
def test_a_coding_client_step_resumes_the_newest_session_of_any_step_sharing_it(
    tmp_path, recorded, resumed
):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.CLEAN, session=s) for s in recorded]
    runtime = FakeRuntime()
    resuming = """\
  - id: answer
    kind: agent
    engine: claude
    prompt: prompts/fix.md
    resume: implement
  - id: check
    kind: agent
    engine: claude
    prompt: prompts/fix.md
    resume: answer
"""
    factory = CLIENT.replace("  - id: fix\n", resuming + "  - id: fix\n")
    hub = factory_hub(tmp_path / "hub", control, runtime, CLIENT_FILES | {"factory.yaml": factory})

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.DONE
        assert [
            (command.origin.step, command.step.resume)
            for _, command in control.steps
            if isinstance(command.step, ClientStepRun)
        ] == list(zip(["implement", "answer", "check", "fix"], [None, *resumed], strict=True))

    asyncio.run(scenario())


def test_a_coding_client_step_whose_resumed_step_left_no_session_fails_that_step(tmp_path):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.CLEAN)] * 2
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, CLIENT_FILES)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert [(a.step, a.ending) for a in finished.attempts] == [
            ("implement", StepEnding.CLEAN),
            ("implement", StepEnding.FAILED),
            ("implement", StepEnding.CLEAN),
            ("implement", StepEnding.FAILED),
        ]
        assert finished.attempts[-1].summary == (
            'Step "fix" resumes the session of "implement", which this step did not record.'
        )
        assert [command.origin.step for _, command in control.steps] == ["implement"] * 2

    asyncio.run(scenario())


def test_retrying_a_run_that_needs_a_human_tries_its_step_again_with_its_retries_reset(tmp_path):
    control = FakeControl()
    failed = StepResult(ending=StepEnding.FAILED)
    control.step_results = [failed, failed, failed, StepResult(ending=StepEnding.CLEAN)]
    runtime = FakeRuntime()
    factory = FACTORY + "    retry: 1\n"
    hub = factory_hub(tmp_path / "hub", control, runtime, FILES | {"factory.yaml": factory})

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        stopped = await settled(hub, run.run_id)
        async with run_events(hub) as events:
            retried = await hub_client(hub).call(
                FACTORY_RUN_RETRY, FactoryRunRetryCommand(run_id=run.run_id)
            )
            finished = await settled(hub, run.run_id)

        assert stopped.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert isinstance(retried, FactoryRun)
        assert (retried.status, retried.step) == (FactoryRunStatus.QUEUED, "test")
        assert finished.run.status is FactoryRunStatus.DONE
        assert [(a.attempt, a.ending) for a in finished.attempts] == [
            (1, StepEnding.FAILED),
            (2, StepEnding.FAILED),
            (3, StepEnding.FAILED),
            (4, StepEnding.CLEAN),
        ]
        assert events[0] == retried

    asyncio.run(scenario())


def test_sending_a_run_that_needs_a_human_back_starts_its_send_backs_afresh(tmp_path):
    control = FakeControl()
    control.step_results = [FIXED, reviewed("changes")] * 4 + [
        FIXED,
        reviewed("changes"),
        FIXED,
        reviewed("clean"),
    ]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, REVIEW_FILES)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        await settled(hub, run.run_id)
        sent = await hub_client(hub).call(
            FACTORY_RUN_SEND_BACK, FactoryRunSendBackCommand(run_id=run.run_id, step="fix")
        )
        finished = await settled(hub, run.run_id)

        assert isinstance(sent, FactoryRun)
        assert (sent.status, sent.step) == (FactoryRunStatus.QUEUED, "fix")
        assert finished.run.status is FactoryRunStatus.DONE
        assert [(a.step, a.attempt) for a in finished.attempts[8:]] == [
            ("fix", 5),
            ("review", 5),
            ("fix", 6),
            ("review", 6),
            ("publish", 1),
        ]

    asyncio.run(scenario())


def test_cancelling_a_run_that_needs_a_human_ends_it_where_it_stopped(tmp_path):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.FAILED)]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        await settled(hub, run.run_id)
        async with run_events(hub) as events:
            cancelled = await hub_client(hub).call(
                FACTORY_RUN_CANCEL, FactoryRunCancelCommand(run_id=run.run_id)
            )
        found = await detail(hub, run.run_id)

        assert isinstance(cancelled, FactoryRun)
        assert (cancelled.status, cancelled.step) == (FactoryRunStatus.CANCELLED, "test")
        assert found.run == cancelled
        assert events == [cancelled]
        assert len(found.attempts) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("method", "command"),
    [
        (FACTORY_RUN_RETRY, FactoryRunRetryCommand),
        (FACTORY_RUN_SEND_BACK, partial(FactoryRunSendBackCommand, step="fix")),
        (FACTORY_RUN_CANCEL, FactoryRunCancelCommand),
    ],
)
def test_only_a_run_that_needs_a_human_is_retried_sent_back_or_cancelled(tmp_path, method, command):
    control = FakeControl()
    control.step_results = [FIXED, reviewed("clean")]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, REVIEW_FILES)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        done = await settled(hub, run.run_id)
        refused = await hub_client(hub).call(method, command(run_id=run.run_id))
        missing = await hub_client(hub).call(method, command(run_id=UUID(int=0)))

        assert done.run.status is FactoryRunStatus.DONE
        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert refused.message == f'Factory run "{run.run_id}" is done, so it needs no human.'
        assert (await detail(hub, run.run_id)) == done
        assert isinstance(missing, ErrorEnvelope)
        assert missing.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


@pytest.mark.parametrize("step", ["review", "publish", "deploy"])
def test_a_run_is_only_sent_back_to_an_earlier_step(tmp_path, step):
    control = FakeControl()
    control.step_results = [FIXED, reviewed("changes")] * 4
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, REVIEW_FILES)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        stopped = await settled(hub, run.run_id)
        refused = await hub_client(hub).call(
            FACTORY_RUN_SEND_BACK, FactoryRunSendBackCommand(run_id=run.run_id, step=step)
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert refused.message == f'Step "{step}" is not a step before "review".'
        assert (await detail(hub, run.run_id)) == stopped

    asyncio.run(scenario())


def test_a_run_names_the_steps_before_its_own_it_can_be_sent_back_to(tmp_path):
    control = FakeControl()
    control.step_results = [FIXED, reviewed("clean"), StepResult(ending=StepEnding.FAILED)]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, REVIEW_FILES)

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        stopped = await settled(hub, run.run_id)

        assert (stopped.run.status, stopped.run.step) == (FactoryRunStatus.NEEDS_HUMAN, "publish")
        assert stopped.earlier_steps == ["fix", "review"]

    asyncio.run(scenario())
