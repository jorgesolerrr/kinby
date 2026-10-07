"""Factory runs on the hub: intake, the per-instance step queue, attempts and run events."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from uuid import UUID

import pytest

from kinby.cli.client import ContractClient
from kinby.contracts import (
    FACTORY_INSTALL,
    FACTORY_RUN_GET,
    FACTORY_RUN_INTAKE,
    FACTORY_RUN_LIST,
    FACTORY_RUN_SUBSCRIBE,
    INSTANCE_START,
    INTAKE_SCOPES,
    CommandStepRun,
    ErrorCode,
    ErrorEnvelope,
    FactoryInstallCommand,
    FactoryInstallResult,
    FactoryInstanceSetup,
    FactoryRun,
    FactoryRunDetail,
    FactoryRunGetCommand,
    FactoryRunIntakeCommand,
    FactoryRunListCommand,
    FactoryRunListResult,
    FactoryRunStatus,
    FactoryRunSubscribeCommand,
    InstanceStartCommand,
    LifecycleOperationResult,
    OperationState,
    StepEnding,
    StepResult,
    StepRunCommand,
    ToolResult,
    TurnFailed,
)
from kinby.hub import Hub
from kinby.plugins.intake import intake_tools
from tests.test_hub import (
    FakeControl,
    FakeImages,
    FakeRuntime,
    finished_operation,
    hub_client,
    instance_environment,
)
from tests.test_hub_factories import write_factory
from tests.test_hub_server import served, url
from tests.test_routines import RoutineModel, fire, instance_at, routine_file

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
"""
FILES = {
    "factory.yaml": FACTORY,
    "instances/coder/routines/scan/ROUTINE.md": "---\ndescription: Scan\n---\nScan.\n",
}
SETUP = FactoryInstanceSetup(model="openai:gpt-5", secrets={"api_key": "sk-test"})


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


async def installed_coder(hub: Hub, runtime: FakeRuntime) -> UUID:
    """Install the factory and start its coder, which answers on its control endpoint."""
    client = hub_client(hub)
    accepted = await client.call(
        FACTORY_INSTALL, FactoryInstallCommand(name="checks", instances={"coder": SETUP})
    )
    assert isinstance(accepted, FactoryInstallResult)
    created = accepted.instances["coder"]
    assert (await finished_operation(client, created)).state is OperationState.SUCCEEDED
    started = await client.call(
        INSTANCE_START, InstanceStartCommand(instance_id=created.instance_id)
    )
    assert isinstance(started, LifecycleOperationResult)
    assert (await finished_operation(client, started)).state is OperationState.SUCCEEDED
    runtime.addresses[str(created.instance_id)] = f"http://kinby-{created.instance_id}:8787"
    return created.instance_id


def intake_client(hub: Hub, instance_id: UUID) -> ContractClient:
    """What one instance holds on the hub: its own intake route."""
    dispatcher = hub.intake(instance_id)
    return ContractClient(dispatcher.dispatch, dispatcher.subscribe, INTAKE_SCOPES)


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
        coder = await installed_coder(hub, runtime)
        async with run_events(hub) as events:
            run = await handed_in(hub, coder, 7)
            finished = await settled(hub, run.run_id)

        assert (run.factory, run.work_item, run.step) == ("checks", {"issue": 7}, "test")
        assert finished.run.status is FactoryRunStatus.DONE
        assert finished.run.step is None
        [attempt] = finished.attempts
        assert (attempt.step, attempt.attempt, attempt.ending) == ("test", 1, StepEnding.CLEAN)
        assert attempt.ended_at is not None
        [(endpoint, command)] = control.steps
        assert command == StepRunCommand(
            step=CommandStepRun(run=["uv run pytest", "uv run ruff check ."]),
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
        coder = await installed_coder(hub, runtime)
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
        coder = await installed_coder(hub, runtime)
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
        coder = await installed_coder(hub, runtime)
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
        assert stopped == FactoryRunListResult(runs=[finished.run])
        assert done == FactoryRunListResult(runs=[])

    asyncio.run(scenario())


def test_a_step_in_an_instance_the_hub_cannot_reach_fails_its_attempt(tmp_path):
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", FakeControl(reachable=False), runtime)

    async def scenario() -> None:
        coder = await installed_coder(hub, runtime)
        runtime.addresses.clear()
        run = await handed_in(hub, coder, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.NEEDS_HUMAN
        [attempt] = finished.attempts
        assert attempt.ending is StepEnding.FAILED
        assert attempt.summary == "The instance's lifecycle endpoint cannot be reached."

    asyncio.run(scenario())


def test_ten_runs_for_one_instance_take_their_steps_one_at_a_time_in_order(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime)

    async def scenario() -> None:
        coder = await installed_coder(hub, runtime)
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
        coder = await installed_coder(hub, runtime)
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


def test_the_intake_routines_tool_hands_its_work_item_to_the_hub_over_its_own_route(
    tmp_path, monkeypatch
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
        coder = await installed_coder(hub, runtime)
        environment = instance_environment(hub, coder)
        monkeypatch.setenv("KINBY_CONTROL_TOKEN", environment["KINBY_CONTROL_TOKEN"])
        async with served(hub) as address:
            monkeypatch.setenv("KINBY_INTAKE_URL", url(address, f"/instances/{coder}/intake"))
            events = await fire(instance, RoutineModel())
            monkeypatch.setenv("KINBY_CONTROL_TOKEN", "another instance's token")
            refused = await fire(instance, RoutineModel())
        listed = await hub_client(hub).call(
            FACTORY_RUN_LIST, FactoryRunListCommand(factory="checks")
        )

        assert environment["KINBY_INTAKE_URL"] == f"http://hub:8080/instances/{coder}/intake"
        assert isinstance(listed, FactoryRunListResult)
        [run] = listed.runs
        assert run.work_item == {"issue": 7}
        [answer] = [event.payload for event in events if isinstance(event.payload, ToolResult)]
        assert answer.error is False
        assert answer.output == f'Factory run {run.run_id} of "checks" is queued.'
        assert isinstance(refused[-1].payload, TurnFailed)
        assert "The hub refused the intake" in refused[-1].payload.message

    asyncio.run(scenario())


def test_an_instance_the_hub_gave_no_intake_url_has_no_intake_tool(monkeypatch):
    monkeypatch.delenv("KINBY_INTAKE_URL", raising=False)

    assert intake_tools() == ()
