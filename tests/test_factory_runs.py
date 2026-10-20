"""Factory runs on the hub: intake, the per-instance step queue, attempts and run events."""

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
    FACTORY_RUN_INTAKE,
    FACTORY_RUN_LIST,
    FACTORY_RUN_RETRY,
    FACTORY_RUN_SEND_BACK,
    FACTORY_RUN_SUBSCRIBE,
    INSTANCE_CREATE,
    INSTANCE_LIST,
    INSTANCE_RECREATE,
    INSTANCE_REMOVE,
    INSTANCE_START,
    AgentStepRun,
    ClientStepRun,
    CodeStepRun,
    CodingClient,
    CommandStepRun,
    ErrorCode,
    ErrorEnvelope,
    FactoryRole,
    FactoryRun,
    FactoryRunCancelCommand,
    FactoryRunDetail,
    FactoryRunGetCommand,
    FactoryRunIntakeCommand,
    FactoryRunListCommand,
    FactoryRunListResult,
    FactoryRunOrigin,
    FactoryRunRetryCommand,
    FactoryRunSendBackCommand,
    FactoryRunStatus,
    FactoryRunSubscribeCommand,
    InstanceCreateCommand,
    InstanceListCommand,
    InstanceListResult,
    InstanceRecreateCommand,
    InstanceRemoveCommand,
    InstanceStartCommand,
    LifecycleOperationResult,
    OperationState,
    ReasoningEffort,
    RepositoryCommands,
    RunStop,
    StepEnding,
    StepResult,
    StepRunCommand,
    ToolResult,
    TurnFailed,
)
from kinby.hub import ControlConnectionLost, ControlUndelivered, ControlUnreachable, Hub
from kinby.hub.registry import HubRegistry
from kinby.hub.runs import FactoryRuns, _QueuedReport
from kinby.plugins.intake import hand_to_factory, intake_tools
from tests.test_hub import (
    FakeControl,
    FakeImages,
    FakeRuntime,
    created_instance,
    finished_operation,
    hub_client,
    instance_environment,
)
from tests.test_hub_factories import write_factory
from tests.test_hub_server import served, url
from tests.test_routines import RoutineModel, fire, instance_at, routine_file
from tests.test_steps import hold_commands

FACTORY = """\
name: checks
instances:
  coder: {}
intake: { instance: coder, routine: scan }
work_item: { issue: int }
steps:
  - id: test
    kind: command
    in: coder
    run: ["uv run pytest", "uv run ruff check ."]
    timeout: 10m
"""
FILES = {
    "factory.yaml": FACTORY,
    "instances/coder/routines/scan/ROUTINE.md": "---\ndescription: Scan\n---\nScan.\n",
}


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


async def started_coder(hub: Hub, runtime: FakeRuntime) -> UUID:
    """Create the factory's coder and start it, which answers on its control endpoint."""
    return (await started_roles(hub, runtime, "coder"))["coder"]


async def started_roles(hub: Hub, runtime: FakeRuntime, *roles: str) -> dict[str, UUID]:
    """Create an instance for each of the factory's *roles*, named after it, and start each."""
    return {role: await created_in(hub, runtime, role, f"checks-{role}") for role in roles}


async def created_in(hub: Hub, runtime: FakeRuntime, role: str, name: str) -> UUID:
    """Create an instance named *name* that picks the factory's *role*, and start it."""
    client = hub_client(hub)
    accepted = await client.call(
        INSTANCE_CREATE,
        InstanceCreateCommand(
            manifest_id=name,
            factory_role=FactoryRole(factory="checks", name=role),
            model="openai:gpt-5",
            secrets={"api_key": "sk-test"},
        ),
    )
    assert isinstance(accepted, LifecycleOperationResult), accepted
    return await started(client, runtime, accepted)


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


def intake_client(hub: Hub, instance_id: UUID) -> ContractClient:
    """What one instance holds on the hub: its own hub route."""
    scopes, dispatcher = hub.hub_route(instance_id)
    return ContractClient(dispatcher.dispatch, dispatcher.subscribe, scopes)


async def handed_in(hub: Hub, instance_id: UUID, issue: int, routine: str = "scan") -> FactoryRun:
    run = await intake_client(hub, instance_id).call(
        FACTORY_RUN_INTAKE,
        FactoryRunIntakeCommand(routine=routine, work_item={"issue": issue}),
    )
    assert isinstance(run, FactoryRun), run
    return run


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


def test_an_intake_hands_a_work_item_to_a_one_step_factory_that_runs_to_done(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        async with run_events(hub) as events:
            run = await handed_in(hub, coder, 7)
            finished = await settled(hub, run.run_id)

        assert (run.factory, run.work_item, run.step) == ("checks", {"issue": 7}, "test")
        assert finished.run.instance_id == coder
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
        )
        assert endpoint.token == instance_environment(hub, coder)["KINBY_CONTROL_TOKEN"]
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


def test_handing_in_the_work_item_of_an_unfinished_run_returns_that_run(tmp_path):
    control = FakeControl()
    control.step_release.clear()
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        first = await handed_in(hub, coder, 7)
        again = await handed_in(hub, coder, 7)
        other = await handed_in(hub, coder, 8)
        control.step_release.set()
        await settled(hub, first.run_id)
        await settled(hub, other.run_id)
        after_done = await handed_in(hub, coder, 7)

        assert again.run_id == first.run_id
        assert other.run_id != first.run_id
        assert after_done.run_id not in {first.run_id, other.run_id}
        assert [command.work_item for _, command in control.steps[:2]] == [
            {"issue": 7},
            {"issue": 8},
        ]

    asyncio.run(scenario())


def test_only_the_factorys_intake_routine_hands_in_work_items_the_factory_declares(tmp_path):
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", FakeControl(), runtime)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        client = intake_client(hub, coder)
        other_routine = await client.call(
            FACTORY_RUN_INTAKE,
            FactoryRunIntakeCommand(routine="triage", work_item={"issue": 7}),
        )
        wrong_values = await client.call(
            FACTORY_RUN_INTAKE,
            FactoryRunIntakeCommand(routine="scan", work_item={"issue": "7", "repo": "kinby"}),
        )
        listed = await hub_client(hub).call(
            FACTORY_RUN_LIST, FactoryRunListCommand(factory="checks")
        )
        from_the_user = await hub_client(hub).call(
            FACTORY_RUN_INTAKE,
            FactoryRunIntakeCommand(routine="scan", work_item={"issue": 7}),
        )

        assert isinstance(other_routine, ErrorEnvelope)
        assert other_routine.code is ErrorCode.PERMISSION_DENIED
        assert isinstance(wrong_values, ErrorEnvelope)
        assert wrong_values.code is ErrorCode.INVALID_ARGUMENT
        assert wrong_values.fields == {
            "issue": "Expected a value of type int.",
            "repo": "The factory's work item carries no value by this name.",
        }
        assert listed == FactoryRunListResult(runs=[])
        assert isinstance(from_the_user, ErrorEnvelope)
        assert from_the_user.code is ErrorCode.NOT_FOUND

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
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
        assert stopped == FactoryRunListResult(runs=[finished.run])
        assert done == FactoryRunListResult(runs=[])

    asyncio.run(scenario())


def test_a_step_its_instance_did_not_take_waits_for_it_and_keeps_its_retries(tmp_path, monkeypatch):
    monkeypatch.setattr("kinby.hub.runs.BACKOFF_SECONDS", 0.01)
    control = FakeControl()
    control.step_results = [
        ControlUndelivered(
            "The instance refused the step: The instance is draining. "
            "It takes no new work until it starts again."
        ),
        ControlUndelivered("Cannot connect to host kinby-instance:8787"),
        StepResult(ending=StepEnding.FAILED, summary="Tests failed."),
    ]
    runtime = FakeRuntime()
    hub = factory_hub(
        tmp_path / "hub", control, runtime, FILES | {"factory.yaml": FACTORY + "    retry: 1\n"}
    )

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.DONE
        assert [(attempt.attempt, attempt.ending) for attempt in finished.attempts] == [
            (1, StepEnding.FAILED),
            (2, StepEnding.CLEAN),
        ]
        assert [command.attempt for _, command in control.steps] == [1, 1, 1, 2]

    asyncio.run(scenario())


def test_a_step_waiting_for_an_unreachable_instance_stays_queued_across_a_restart(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("kinby.hub.runs.BACKOFF_SECONDS", 60)
    control = FakeControl()
    runtime = FakeRuntime()
    directory = tmp_path / "hub"
    hub = factory_hub(directory, control, runtime)

    async def before_the_restart() -> tuple[UUID, UUID, str]:
        coder = await started_coder(hub, runtime)
        address = runtime.addresses.pop(str(coder))
        run = await handed_in(hub, coder, 7)
        await asyncio.sleep(0.05)
        waiting = await detail(hub, run.run_id)

        assert waiting.run.status is FactoryRunStatus.QUEUED
        assert waiting.attempts == []
        return run.run_id, coder, address

    run_id, coder, address = asyncio.run(before_the_restart())
    hub.close()
    runtime.addresses[str(coder)] = address
    restarted = Hub(
        directory,
        runtime=runtime,
        images=FakeImages(),
        control=control,
        shipped_factories=directory / "shipped",
    )

    async def after_the_restart() -> None:
        await restarted.recover()
        finished = await settled(restarted, run_id)

        assert finished.run.status is FactoryRunStatus.DONE
        assert [(attempt.attempt, attempt.ending) for attempt in finished.attempts] == [
            (1, StepEnding.CLEAN)
        ]

    asyncio.run(after_the_restart())


def test_a_step_whose_socket_drops_after_the_call_was_sent_fails_its_attempt(tmp_path):
    control = FakeControl()
    control.step_results = [
        ControlConnectionLost("The control socket closed before it answered (WSMsgType.CLOSED).")
    ]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.NEEDS_HUMAN
        [attempt] = finished.attempts
        assert attempt.ending is StepEnding.FAILED
        assert attempt.summary == (
            "The control socket closed before it answered (WSMsgType.CLOSED)."
        )

    asyncio.run(scenario())


def test_ten_runs_for_one_instance_take_their_steps_one_at_a_time_in_order(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        runs = [await handed_in(hub, coder, issue) for issue in range(1, 11)]
        finished = [await settled(hub, run.run_id) for run in runs]

        assert {found.run.status for found in finished} == {FactoryRunStatus.DONE}
        assert [command.work_item["issue"] for _, command in control.steps] == list(range(1, 11))
        assert control.most_steps_running == 1

    asyncio.run(scenario())


def test_an_attempt_running_when_the_hub_stopped_counts_as_failed_and_follows_its_retry(
    tmp_path,
):
    control = FakeControl()
    control.step_release.clear()
    runtime = FakeRuntime()
    directory = tmp_path / "hub"
    hub = factory_hub(
        directory, control, runtime, FILES | {"factory.yaml": FACTORY + "    retry: 1\n"}
    )

    async def before_the_restart() -> tuple[UUID, UUID]:
        coder = await started_coder(hub, runtime)
        held = await handed_in(hub, coder, 7)
        queued = await handed_in(hub, coder, 8)
        async with asyncio.timeout(5):
            while not control.steps:
                await asyncio.sleep(0.01)
        return held.run_id, queued.run_id

    held, queued = asyncio.run(before_the_restart())
    hub.close()
    control.step_release.set()
    restarted = Hub(
        directory,
        runtime=runtime,
        images=FakeImages(),
        control=control,
        shipped_factories=directory / "shipped",
    )

    async def after_the_restart() -> None:
        await restarted.recover()
        retried = await settled(restarted, held)
        waited = await settled(restarted, queued)

        assert retried.run.status is FactoryRunStatus.DONE
        assert [(attempt.attempt, attempt.ending) for attempt in retried.attempts] == [
            (1, StepEnding.INTERRUPTED),
            (2, StepEnding.CLEAN),
        ]
        assert retried.attempts[0].summary == "The hub stopped while this attempt ran."
        assert waited.run.status is FactoryRunStatus.DONE
        assert [command.work_item["issue"] for _, command in control.steps] == [7, 8, 7]

    asyncio.run(after_the_restart())


NEEDS_HUMAN_TOOL = '''\
from kinby.plugins.tools import tool


@tool(write=True)
def notify_human() -> dict[str, int]:
    """Record that this run needs a human."""
    return {}
'''
NEEDS_HUMAN_FILES = FILES | {
    "factory.yaml": FACTORY + "needs_human: { in: coder, call: notify_human }\n",
    "instances/coder/tools/notify.py": NEEDS_HUMAN_TOOL,
}


def test_a_needs_human_report_queued_but_not_delivered_is_sent_after_a_restart(tmp_path):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.FAILED)]
    control.step_release.clear()
    runtime = FakeRuntime()
    directory = tmp_path / "hub"
    hub = factory_hub(directory, control, runtime, NEEDS_HUMAN_FILES)

    def new_hub() -> Hub:
        return Hub(
            directory,
            runtime=runtime,
            images=FakeImages(),
            control=control,
            shipped_factories=directory / "shipped",
        )

    async def before_the_restart() -> UUID:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
    restarted = new_hub()

    async def after_the_restart() -> None:
        await restarted.recover()
        async with asyncio.timeout(5):
            while len(control.steps) < 3:
                await asyncio.sleep(0.01)

        _, report = control.steps[-1]
        assert report.step == CodeStepRun(call="notify_human", summary="")
        assert (report.origin.run_id, report.origin.step) == (run_id, "test")
        restarted.close()

    asyncio.run(after_the_restart())

    again = new_hub()

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
        ControlUnreachable("Cannot connect to host kinby-instance:8787"),
        StepResult(ending=StepEnding.FAILED, summary="GitHub is down."),
    ]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, NEEDS_HUMAN_FILES)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
        coder = await started_coder(hub, runtime)
        await handed_in(hub, coder, 7)
        async with asyncio.timeout(5):
            while len(control.steps) < 2:
                await asyncio.sleep(0.01)
        later = await handed_in(hub, coder, 8)
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
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        await settled(hub, run.run_id)
        async with asyncio.timeout(30):
            while hub.registry.unreported_runs():
                await asyncio.sleep(0.01)

        assert len(reports(control)) == 1101

    asyncio.run(scenario())


def test_a_report_backing_off_goes_to_the_instance_that_replaced_its_own(tmp_path, monkeypatch):
    """A report in another role than the run's instance goes to whichever instance holds that
    role when the report is tried again."""
    retries = held_report_retries(monkeypatch)
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.FAILED, summary="Tests failed."),
        StepResult(ending=StepEnding.FAILED, summary="GitHub is down."),
    ]
    runtime = FakeRuntime()
    factory = FACTORY.replace("  coder: {}", "  coder: {}\n  checker: {}")
    files = FILES | {
        "factory.yaml": factory + "needs_human: { in: checker, call: notify_human }\n",
        "instances/checker/tools/notify.py": NEEDS_HUMAN_TOOL,
    }
    hub = factory_hub(tmp_path / "hub", control, runtime, files)
    client = hub_client(hub)

    async def scenario() -> None:
        instances = await started_roles(hub, runtime, "coder", "checker")
        await handed_in(hub, instances["coder"], 7)
        async with asyncio.timeout(5):
            while len(control.steps) < 2:
                await asyncio.sleep(0.01)
        removal = await client.call(
            INSTANCE_REMOVE, InstanceRemoveCommand(instance_id=instances["checker"])
        )
        assert isinstance(removal, LifecycleOperationResult)
        assert (await finished_operation(client, removal)).state is OperationState.SUCCEEDED
        replacement = await created_in(hub, runtime, "checker", "checks-checker")
        retries.set()
        await reported(hub)

        endpoint, _ = control.steps[-1]
        assert endpoint.address == f"http://kinby-{replacement}:8787"

    asyncio.run(scenario())


def test_an_attempt_that_needs_a_human_once_settled_on_resume_is_reported_only_once(tmp_path):
    """Recovery already queues the report for a run its own settling sends to needs_human.

    The pass over runs left unreported from before the restart must not queue a second one for
    that same run: resume() has to collect that pass before it settles any unfinished attempt.
    """
    control = FakeControl()
    control.step_release.clear()
    runtime = FakeRuntime()
    directory = tmp_path / "hub"
    hub = factory_hub(directory, control, runtime, NEEDS_HUMAN_FILES)

    async def before_the_restart() -> UUID:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        async with asyncio.timeout(5):
            while not control.steps:
                await asyncio.sleep(0.01)
        return run.run_id

    run_id = asyncio.run(before_the_restart())
    hub.close()
    control.step_release.set()
    restarted = Hub(
        directory,
        runtime=runtime,
        images=FakeImages(),
        control=control,
        shipped_factories=directory / "shipped",
    )

    async def after_the_restart() -> None:
        await restarted.recover()
        stopped = await settled(restarted, run_id)
        assert stopped.run.status is FactoryRunStatus.NEEDS_HUMAN
        await asyncio.sleep(0.05)
        reports = [
            command
            for _, command in control.steps
            if isinstance(command.step, CodeStepRun) and command.step.call == "notify_human"
        ]
        assert len(reports) == 1
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


async def told(control: FakeControl, count: int) -> list[RunStop]:
    """The run stops the hub told once *count* of them reached their instance, and no more."""
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
def test_a_run_that_stops_tells_the_instance_that_owns_it_once_with_its_record(
    tmp_path, results, status
):
    control = FakeControl()
    control.step_results = results
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, NEEDS_HUMAN_FILES)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
            )
        ]
        endpoint, _ = control.stops[0]
        assert endpoint.address == f"http://kinby-{coder}:8787"

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
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
    control.stop_failures = [ControlUnreachable("Cannot connect to host kinby-instance:8787")]
    runtime = FakeRuntime()
    directory = tmp_path / "hub"
    hub = factory_hub(directory, control, runtime)

    def new_hub() -> Hub:
        return Hub(
            directory,
            runtime=runtime,
            images=FakeImages(),
            control=control,
            shipped_factories=directory / "shipped",
        )

    async def before_the_restart() -> UUID:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        await settled(hub, run.run_id)
        await told(control, 1)
        return run.run_id

    run_id = asyncio.run(before_the_restart())
    hub.close()
    restarted = new_hub()

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
    once_more = new_hub()

    async def after_the_second_restart() -> None:
        await once_more.recover()
        await asyncio.sleep(0.05)

        assert len(control.stops) == 2
        once_more.close()

    asyncio.run(after_the_second_restart())


@pytest.mark.parametrize(
    "route", ["hub", "intake"], ids=["hub-route", "route-of-a-container-from-before-it"]
)
def test_the_intake_routines_tool_hands_its_work_item_to_the_hub_over_its_own_route(
    tmp_path, monkeypatch, route
):
    runtime = FakeRuntime()
    factory = FACTORY.replace("routine: scan", "routine: news")
    hub = factory_hub(
        tmp_path / "hub",
        FakeControl(),
        runtime,
        {"factory.yaml": factory}
        | {"instances/coder/routines/news/ROUTINE.md": "---\ndescription: News\n---\nNews.\n"},
    )
    (tmp_path / "coder").mkdir()
    instance = instance_at(tmp_path / "coder")
    routine_file(
        instance,
        "description: Intake\nmode: full-access\nrun: hand_to_factory\n"
        'arguments: {"work_item": {"issue": 7}}',
    )

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        environment = instance_environment(hub, coder)
        monkeypatch.setenv("KINBY_CONTROL_TOKEN", environment["KINBY_CONTROL_TOKEN"])
        async with served(hub) as address:
            monkeypatch.setenv("KINBY_HUB_URL", url(address, f"/instances/{coder}/{route}"))
            events = await fire(instance, RoutineModel())
            monkeypatch.setenv("KINBY_CONTROL_TOKEN", "another instance's token")
            refused = await fire(instance, RoutineModel())
        listed = await hub_client(hub).call(
            FACTORY_RUN_LIST, FactoryRunListCommand(factory="checks")
        )

        assert environment["KINBY_HUB_URL"] == f"http://hub:8080/instances/{coder}/hub"
        assert "KINBY_INTAKE_URL" not in environment
        assert isinstance(listed, FactoryRunListResult)
        [run] = listed.runs
        assert run.work_item == {"issue": 7}
        [answer] = [event.payload for event in events if isinstance(event.payload, ToolResult)]
        assert answer.error is False
        assert answer.output == f'Factory run {run.run_id} of "checks" is queued.'
        assert isinstance(refused[-1].payload, TurnFailed)
        assert "The hub refused the intake" in refused[-1].payload.message

    asyncio.run(scenario())


def test_an_instance_the_registry_grants_no_scope_gets_no_hub_url(tmp_path):
    hub = factory_hub(tmp_path / "hub", FakeControl(), FakeRuntime())

    async def scenario() -> None:
        created = await created_instance(hub_client(hub))

        assert "KINBY_HUB_URL" not in instance_environment(hub, created.instance_id)

    asyncio.run(scenario())


def test_a_factory_instance_created_before_its_hub_route_gets_it_on_recreation(tmp_path):
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", FakeControl(), runtime)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        environment = instance_environment(hub, coder)
        del environment["KINBY_HUB_URL"]
        (hub.instances_directory / str(coder) / ".env").write_text(
            "".join(f"{name}='{value}'\n" for name, value in environment.items())
        )
        client = hub_client(hub)
        recreated = await client.call(INSTANCE_RECREATE, InstanceRecreateCommand(instance_id=coder))
        assert isinstance(recreated, LifecycleOperationResult)
        assert (await finished_operation(client, recreated)).state is OperationState.SUCCEEDED

        hub_url = f"http://hub:8080/instances/{coder}/hub"
        assert instance_environment(hub, coder)["KINBY_HUB_URL"] == hub_url
        assert runtime.created[-1].env["KINBY_HUB_URL"] == hub_url

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("variable", "tools"),
    [("KINBY_HUB_URL", (hand_to_factory,)), ("KINBY_INTAKE_URL", ())],
    ids=["hub-url", "intake-url"],
)
def test_only_an_instance_the_hub_gave_a_hub_url_has_the_intake_tool(monkeypatch, variable, tools):
    monkeypatch.setenv(variable, "http://hub:8080/instances/x/hub")

    assert intake_tools() == tools


HANDOFF = """\
name: checks
instances:
  coder: {}
intake: { instance: coder, routine: scan }
work_item: { issue: int }
steps:
  - id: implement
    kind: command
    in: coder
    run: ["make implement"]
    hook: record_branch
    results: { branch: str, committed: bool }
    retry: 1
  - id: open-pr
    kind: code
    in: coder
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
HANDOFF_FILES = FILES | {"factory.yaml": HANDOFF, "instances/coder/tools/github.py": TOOLS}
BRANCH = {"branch": "agent/7", "committed": True}


def test_the_hub_hands_each_step_its_hook_and_the_values_earlier_steps_recorded(tmp_path):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.CLEAN, values=BRANCH),
        StepResult(ending=StepEnding.CLEAN, values={"pr": 42}),
    ]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, HANDOFF_FILES)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.DONE
        assert [command for _, command in control.steps] == [
            StepRunCommand(
                step=CommandStepRun(run=["make implement"]),
                hook="record_branch",
                origin=FactoryRunOrigin(factory="checks", run_id=run.run_id, step="implement"),
                attempt=1,
                work_item={"issue": 7},
            ),
            StepRunCommand(
                step=CodeStepRun(call="open_pull_request"),
                origin=FactoryRunOrigin(factory="checks", run_id=run.run_id, step="open-pr"),
                attempt=1,
                work_item={"issue": 7},
                results=BRANCH,
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
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert finished.attempts[0].summary.startswith("Pushed the branch.")
        assert finished.attempts[0].values == {}
        assert finished.attempts[1].summary == (
            'Step "open-pr" requires "branch", which this step did not record.'
        )

    asyncio.run(scenario())


SWITCH = HANDOFF.replace(
    "  - id: open-pr\n    kind: code\n    in: coder\n    call: open_pull_request\n",
    '  - id: open-pr\n    kind: command\n    in: coder\n    run: ["git switch {{branch}}", '
    '"make publish ISSUE={{issue}}"]\n',
).replace("    results: { pr: int }\ndone_requires: [pr]\n", "")


def test_a_command_names_a_value_of_the_run_as_one_argument(tmp_path):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.CLEAN, values={"branch": "agent/7 dark", "committed": True})
    ]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, FILES | {"factory.yaml": SWITCH})

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        await settled(hub, run.run_id)

        _, publish = control.steps[-1]
        assert publish.step == CommandStepRun(
            run=["git switch 'agent/7 dark'", "make publish ISSUE=7"]
        )

    asyncio.run(scenario())


def test_the_hub_passes_a_list_a_command_step_names_to_its_instance_as_named(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    factory = FACTORY.replace(
        'run: ["uv run pytest", "uv run ruff check ."]',
        'run: ["make {{issue}}", { commands: setup }, "make check"]',
    )
    hub = factory_hub(tmp_path / "hub", control, runtime, FILES | {"factory.yaml": factory})

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        hold_commands(hub.instances_directory / str(coder), 'setup = ["uv sync"]\n')
        run = await handed_in(hub, coder, 7)
        await settled(hub, run.run_id)

        [(_, test)] = control.steps
        assert test.step == CommandStepRun(
            run=["make 7", RepositoryCommands(commands="setup"), "make check"],
            timeout_seconds=600,
        )

    asyncio.run(scenario())


LISTS_FACTORY = FACTORY.replace(
    'run: ["uv run pytest", "uv run ruff check ."]',
    'run: ["make {{issue}}", { commands: setup }, { commands: checks }, { commands: setup }]',
)


def test_intake_refuses_a_work_item_while_its_instance_lacks_a_list_its_roles_steps_name(tmp_path):
    runtime = FakeRuntime()
    files = FILES | {"factory.yaml": LISTS_FACTORY}
    hub = factory_hub(tmp_path / "hub", FakeControl(), runtime, files)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        work_item = FactoryRunIntakeCommand(routine="scan", work_item={"issue": 7})
        none_held = await intake_client(hub, coder).call(FACTORY_RUN_INTAKE, work_item)
        hold_commands(hub.instances_directory / str(coder), 'setup = ["uv sync"]\n')
        one_held = await intake_client(hub, coder).call(FACTORY_RUN_INTAKE, work_item)
        listed = await hub_client(hub).call(
            FACTORY_RUN_LIST, FactoryRunListCommand(factory="checks")
        )

        assert isinstance(none_held, ErrorEnvelope)
        assert none_held.code is ErrorCode.INVALID_ARGUMENT
        assert none_held.message == (
            'This instance holds no repository commands named "setup", "checks". '
            "Ask it in a thread for a repository survey."
        )
        assert isinstance(one_held, ErrorEnvelope)
        assert '"checks"' in one_held.message
        assert '"setup"' not in one_held.message
        assert listed == FactoryRunListResult(runs=[])

    asyncio.run(scenario())


def test_intake_refuses_a_work_item_while_its_instances_manifest_cannot_be_read(tmp_path):
    runtime = FakeRuntime()
    files = FILES | {"factory.yaml": LISTS_FACTORY}
    hub = factory_hub(tmp_path / "hub", FakeControl(), runtime, files)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        (hub.instances_directory / str(coder) / "kinby.toml").write_bytes(b"\xff\xfe")
        refused = await intake_client(hub, coder).call(
            FACTORY_RUN_INTAKE, FactoryRunIntakeCommand(routine="scan", work_item={"issue": 7})
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert '"setup", "checks"' in refused.message

    asyncio.run(scenario())


def test_intake_accepts_the_work_item_it_refused_once_every_list_is_held_an_empty_one_too(
    tmp_path,
):
    runtime = FakeRuntime()
    files = FILES | {"factory.yaml": LISTS_FACTORY}
    hub = factory_hub(tmp_path / "hub", FakeControl(), runtime, files)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        work_item = FactoryRunIntakeCommand(routine="scan", work_item={"issue": 7})
        refused = await intake_client(hub, coder).call(FACTORY_RUN_INTAKE, work_item)
        hold_commands(
            hub.instances_directory / str(coder), 'setup = []\nchecks = ["bun run check"]\n'
        )
        run = await handed_in(hub, coder, 7)

        assert isinstance(refused, ErrorEnvelope)
        assert (await settled(hub, run.run_id)).run.status is FactoryRunStatus.DONE

    asyncio.run(scenario())


def test_a_value_a_command_names_is_required_of_the_step_that_should_have_produced_it(tmp_path):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.CLEAN, values={"committed": True}),
        StepResult(ending=StepEnding.CLEAN, values=BRANCH),
    ]
    runtime = FakeRuntime()
    factory = SWITCH.replace("    requires: [branch, committed]\n", "")
    hub = factory_hub(tmp_path / "hub", control, runtime, FILES | {"factory.yaml": factory})

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert finished.run.step == "open-pr"
        last = finished.attempts[-1]
        assert (last.step, last.ending) == ("open-pr", StepEnding.FAILED)
        assert last.summary == (
            'Opened nothing.\nThe run\'s done_requires needs "pr", which no step recorded.'
        )

    asyncio.run(scenario())


REVIEW = """\
name: checks
instances:
  coder: {}
intake: { instance: coder, routine: scan }
work_item: { issue: int }
steps:
  - id: fix
    kind: command
    in: coder
    run: ["make fix"]
  - id: review
    kind: command
    in: coder
    run: ["make review"]
    hook: read_verdict
    outcomes: { clean: next, changes: { back: fix, max: 3 }, hopeless: stop, merged: done }
  - id: publish
    kind: command
    in: coder
    run: ["make publish"]
"""
VERDICT = '''\
from kinby.plugins.hooks import hook


@hook
def read_verdict() -> None:
    """Read the review's verdict from the workspace."""
'''
REVIEW_FILES = FILES | {"factory.yaml": REVIEW, "instances/coder/hooks/verdict.py": VERDICT}
FIXED = StepResult(ending=StepEnding.CLEAN)


def reviewed(outcome: str) -> StepResult:
    return StepResult(ending=StepEnding.CLEAN, outcome=outcome)


def test_review_sending_work_back_past_its_max_needs_a_human(tmp_path):
    control = FakeControl()
    control.step_results = [FIXED, reviewed("changes")] * 4
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, REVIEW_FILES)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert finished.run.step == "review"
        assert [(a.step, a.attempt, a.outcome) for a in finished.attempts] == [
            (step, number, outcome)
            for number in range(1, 5)
            for step, outcome in (("fix", None), ("review", "changes"))
        ]
        assert finished.attempts[-1].summary == (
            'Step "review" sent the work back to "fix" 3 times, its max.'
        )

    asyncio.run(scenario())


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
    ).replace("    hook: read_verdict\n", "    hook: read_verdict\n    requires: [committed]\n")
    hub = factory_hub(tmp_path / "hub", control, runtime, REVIEW_FILES | {"factory.yaml": factory})

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
        "hook: read_verdict\n", "hook: read_verdict\n    results: { feedback: str }\n"
    )
    hub = factory_hub(tmp_path / "hub", control, runtime, REVIEW_FILES | {"factory.yaml": factory})

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
    "    hook: read_verdict\n"
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
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
    missing = 'This instance holds no repository commands named "checks".'
    control.step_results = [FIXED, StepResult(ending=StepEnding.NEEDS_HUMAN, summary=missing)]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, REPAIR_FILES)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        finished = await settled(hub, run.run_id)

        assert (finished.run.status, finished.run.step) == (FactoryRunStatus.NEEDS_HUMAN, "review")
        assert finished.attempts[-1].ending is StepEnding.FAILED
        assert finished.attempts[-1].summary == (
            'The run\'s done_requires needs "pr", which no step recorded.'
        )

    asyncio.run(scenario())


def test_a_timed_out_attempt_is_never_tried_again(tmp_path):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.TIMED_OUT, summary="The step ran past its 60m timeout.")
    ]
    runtime = FakeRuntime()
    factory = FACTORY + "    retry: 2\n"
    hub = factory_hub(tmp_path / "hub", control, runtime, FILES | {"factory.yaml": factory})

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert [(a.attempt, a.ending) for a in finished.attempts] == [(1, StepEnding.TIMED_OUT)]
        assert len(control.steps) == 1

    asyncio.run(scenario())


AGENT = """\
name: checks
instances:
  coder: {}
intake: { instance: coder, routine: scan }
work_item: { issue: int }
steps:
  - id: implement
    kind: agent
    in: coder
    prompt: prompts/implement.md
    hook: record_branch
"""


def test_the_hub_sends_an_agent_step_its_prompt_and_names_its_run_and_step(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    files = FILES | {"factory.yaml": AGENT, "prompts/implement.md": "Implement it.\n"}
    hub = factory_hub(tmp_path / "hub", control, runtime, files)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.DONE
        [(_, command)] = control.steps
        assert command == StepRunCommand(
            step=AgentStepRun(prompt="Implement it.\n"),
            hook="record_branch",
            origin=FactoryRunOrigin(factory="checks", run_id=run.run_id, step="implement"),
            attempt=1,
            work_item={"issue": 7},
        )

    asyncio.run(scenario())


CLIENT = """\
name: checks
instances:
  coder: {}
intake: { instance: coder, routine: scan }
work_item: { issue: int }
steps:
  - id: implement
    kind: client
    in: coder
    client: claude
    prompt: prompts/implement.md
    hook: record_branch
    timeout: 30m
  - id: fix
    kind: client
    in: coder
    client: claude
    prompt: prompts/fix.md
    resume: implement
    hook: record_branch
    model: claude-opus-5-5
    effort: high
"""
CLIENT_FILES = FILES | {
    "factory.yaml": CLIENT,
    "prompts/implement.md": "Implement it.\n",
    "prompts/fix.md": "Fix it.\n",
}


def test_the_hub_sends_a_client_step_its_prompt_timeout_model_and_the_session_it_resumes(
    tmp_path,
):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.CLEAN, session="claude-1"),
        StepResult(ending=StepEnding.CLEAN, session="claude-1"),
    ]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, CLIENT_FILES)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.DONE
        assert [command.step for _, command in control.steps] == [
            ClientStepRun(
                client=CodingClient.CLAUDE, prompt="Implement it.\n", timeout_seconds=1800
            ),
            ClientStepRun(
                client=CodingClient.CLAUDE,
                prompt="Fix it.\n",
                resume="claude-1",
                timeout_seconds=3600,
                model="claude-opus-5-5",
                effort=ReasoningEffort.HIGH,
            ),
        ]
        assert [attempt.session for attempt in finished.attempts] == ["claude-1", "claude-1"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("recorded", "resumed"),
    [
        (["claude-1"] * 4, ["claude-1"] * 3),
        (["claude-1", "claude-2", "claude-2", "claude-2"], ["claude-1", "claude-2", "claude-2"]),
        (["claude-1", "claude-1", "claude-2", "claude-2"], ["claude-1", "claude-1", "claude-2"]),
    ],
    ids=["same", "answer-new", "check-new"],
)
def test_a_client_step_resumes_the_newest_session_of_any_step_sharing_it(
    tmp_path, recorded, resumed
):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.CLEAN, session=s) for s in recorded]
    runtime = FakeRuntime()
    resuming = """\
  - id: answer
    kind: client
    in: coder
    client: claude
    prompt: prompts/fix.md
    resume: implement
    hook: record_branch
  - id: check
    kind: client
    in: coder
    client: claude
    prompt: prompts/fix.md
    resume: answer
    hook: record_branch
"""
    factory = CLIENT.replace("  - id: fix\n", resuming + "  - id: fix\n")
    hub = factory_hub(tmp_path / "hub", control, runtime, CLIENT_FILES | {"factory.yaml": factory})

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.DONE
        assert [
            (command.origin.step, command.step.resume)
            for _, command in control.steps
            if isinstance(command.step, ClientStepRun)
        ] == list(zip(["implement", "answer", "check", "fix"], [None, *resumed], strict=True))

    asyncio.run(scenario())


def test_a_client_step_whose_resumed_step_left_no_session_fails_that_step(tmp_path):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.CLEAN)] * 2
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, CLIENT_FILES)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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


@pytest.mark.parametrize(("retry", "attempts"), [("", 2), ("    retry: 0\n", 1)])
def test_a_step_a_model_runs_is_tried_once_more_by_default(tmp_path, retry, attempts):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.FAILED)] * 2
    runtime = FakeRuntime()
    files = FILES | {"factory.yaml": AGENT + retry, "prompts/implement.md": "Implement it.\n"}
    hub = factory_hub(tmp_path / "hub", control, runtime, files)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert [(a.step, a.ending) for a in finished.attempts] == [
            ("implement", StepEnding.FAILED)
        ] * attempts

    asyncio.run(scenario())


def test_retrying_a_run_that_needs_a_human_tries_its_step_again_with_its_retries_reset(tmp_path):
    control = FakeControl()
    failed = StepResult(ending=StepEnding.FAILED)
    control.step_results = [failed, failed, failed, StepResult(ending=StepEnding.CLEAN)]
    runtime = FakeRuntime()
    factory = FACTORY + "    retry: 1\n"
    hub = factory_hub(tmp_path / "hub", control, runtime, FILES | {"factory.yaml": factory})

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        stopped = await settled(hub, run.run_id)

        assert (stopped.run.status, stopped.run.step) == (FactoryRunStatus.NEEDS_HUMAN, "publish")
        assert stopped.earlier_steps == ["fix", "review"]

    asyncio.run(scenario())


async def needing_human(hub: Hub) -> dict[UUID, int]:
    """Each listed instance's count of the factory runs that need a human at one of its steps."""
    listed = await hub_client(hub).call(INSTANCE_LIST, InstanceListCommand())
    assert isinstance(listed, InstanceListResult)
    return {instance.instance_id: instance.needs_human for instance in listed.instances}


def test_a_run_that_needs_a_human_counts_toward_its_steps_instance_until_it_moves_on(tmp_path):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.FAILED)] * 2
    runtime = FakeRuntime()
    factory = FACTORY.replace("  coder: {}", "  coder: {}\n  checker: {}").replace(
        "in: coder", "in: checker"
    )
    files = FILES | {"factory.yaml": factory, "instances/checker/AGENTS.md": "Check.\n"}
    hub = factory_hub(tmp_path / "hub", control, runtime, files)

    async def scenario() -> None:
        instances = await started_roles(hub, runtime, "coder", "checker")
        coder, checker = instances["coder"], instances["checker"]
        before = await needing_human(hub)
        first = await handed_in(hub, coder, 7)
        second = await handed_in(hub, coder, 8)
        await settled(hub, first.run_id)
        await settled(hub, second.run_id)
        stopped = await needing_human(hub)
        await hub_client(hub).call(FACTORY_RUN_CANCEL, FactoryRunCancelCommand(run_id=first.run_id))
        await hub_client(hub).call(FACTORY_RUN_RETRY, FactoryRunRetryCommand(run_id=second.run_id))
        await settled(hub, second.run_id)

        assert before == {coder: 0, checker: 0}
        assert stopped == {coder: 0, checker: 2}
        assert await needing_human(hub) == {coder: 0, checker: 0}

    asyncio.run(scenario())


def test_a_run_that_needs_a_human_counts_only_toward_the_instance_its_step_ran_in(tmp_path):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.FAILED)]
    runtime = FakeRuntime()
    factory = FACTORY.replace("  coder: {}", "  coder: {}\n  checker: {}").replace(
        "in: coder", "in: checker"
    )
    files = FILES | {"factory.yaml": factory, "instances/checker/AGENTS.md": "Check.\n"}
    hub = factory_hub(tmp_path / "hub", control, runtime, files)

    async def scenario() -> None:
        instances = await started_roles(hub, runtime, "coder", "checker")
        coder, checker = instances["coder"], instances["checker"]
        spare = await created_in(hub, runtime, "checker", "spare-checker")
        run = await handed_in(hub, coder, 7)
        await settled(hub, run.run_id)

        [(endpoint, _)] = control.steps
        assert endpoint.address == f"http://kinby-{checker}:8787"
        assert await needing_human(hub) == {coder: 0, checker: 1, spare: 0}

    asyncio.run(scenario())


def test_two_coders_each_run_their_own_run_of_one_work_item(tmp_path):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.FAILED)] * 2
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime)
    client = hub_client(hub)

    async def runs_of(instance_id: UUID) -> list[UUID]:
        listed = await client.call(
            FACTORY_RUN_LIST, FactoryRunListCommand(factory="checks", instance_id=instance_id)
        )
        assert isinstance(listed, FactoryRunListResult), listed
        return [run.run_id for run in listed.runs]

    async def scenario() -> None:
        first = await created_in(hub, runtime, "coder", "checks-coder")
        second = await created_in(hub, runtime, "coder", "billing-coder")
        before = await needing_human(hub)
        own = await handed_in(hub, first, 7)
        other = await handed_in(hub, second, 7)
        again = await handed_in(hub, second, 7)
        await settled(hub, own.run_id)
        stopped = await settled(hub, other.run_id)

        assert own.run_id != other.run_id
        assert again.run_id == other.run_id
        assert stopped.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert {command.origin.run_id: endpoint.address for endpoint, command in control.steps} == {
            own.run_id: f"http://kinby-{first}:8787",
            other.run_id: f"http://kinby-{second}:8787",
        }
        assert before == {first: 0, second: 0}
        assert await needing_human(hub) == {first: 1, second: 1}
        assert await runs_of(first) == [own.run_id]
        assert await runs_of(second) == [other.run_id]

    asyncio.run(scenario())


def test_a_run_from_before_runs_named_their_instance_belongs_to_the_factorys_intake(tmp_path):
    runtime = FakeRuntime()
    directory = tmp_path / "hub"
    factory = FACTORY.replace("  coder: {}", "  coder: {}\n  checker: {}")
    files = FILES | {"factory.yaml": factory, "instances/checker/AGENTS.md": "Check.\n"}
    hub = factory_hub(directory, FakeControl(), runtime, files)

    async def before() -> tuple[UUID, UUID]:
        coder = (await started_roles(hub, runtime, "coder", "checker"))["coder"]
        run = await handed_in(hub, coder, 7)
        await settled(hub, run.run_id)
        return coder, run.run_id

    coder, run_id = asyncio.run(before())
    hub.close()
    with sqlite3.connect(directory / "registry.sqlite") as connection:
        connection.execute("ALTER TABLE factory_runs DROP COLUMN instance_id")
    reopened = factory_hub(directory, FakeControl(), FakeRuntime(), files)

    found = asyncio.run(detail(reopened, run_id))

    assert found.run.instance_id == coder


def test_a_value_of_another_type_than_its_step_declares_fails_the_step(tmp_path):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.CLEAN, values=BRANCH),
        StepResult(ending=StepEnding.CLEAN, values={"pr": "42"}),
    ]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, HANDOFF_FILES)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert finished.run.step == "open-pr"
        last = finished.attempts[-1]
        assert (last.step, last.ending) == ("open-pr", StepEnding.FAILED)
        assert last.summary == 'Step "open-pr" declares "pr" as int, but recorded a str.'

    asyncio.run(scenario())
