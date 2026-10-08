"""Factory runs on the hub: intake, the per-instance step queue, attempts and run events."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from functools import partial
from pathlib import Path
from uuid import UUID

import pytest

from kinby.cli.client import ContractClient
from kinby.contracts import (
    FACTORY_INSTALL,
    FACTORY_RUN_CANCEL,
    FACTORY_RUN_GET,
    FACTORY_RUN_INTAKE,
    FACTORY_RUN_LIST,
    FACTORY_RUN_RETRY,
    FACTORY_RUN_SEND_BACK,
    FACTORY_RUN_SUBSCRIBE,
    INSTANCE_LIST,
    INSTANCE_START,
    INTAKE_SCOPES,
    AgentStepRun,
    ClientStepRun,
    CodeStepRun,
    CodingClient,
    CommandStepRun,
    ErrorCode,
    ErrorEnvelope,
    FactoryInstallCommand,
    FactoryInstallResult,
    FactoryInstanceSetup,
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
    InstanceListCommand,
    InstanceListResult,
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
    timeout: 10m
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
    return (await installed(hub, runtime, "coder"))["coder"]


async def installed(hub: Hub, runtime: FakeRuntime, *names: str) -> dict[str, UUID]:
    """Install the factory and start each of its instances, by name."""
    client = hub_client(hub)
    accepted = await client.call(
        FACTORY_INSTALL,
        FactoryInstallCommand(name="checks", instances=dict.fromkeys(names, SETUP)),
    )
    assert isinstance(accepted, FactoryInstallResult)
    for created in accepted.instances.values():
        assert (await finished_operation(client, created)).state is OperationState.SUCCEEDED
        started = await client.call(
            INSTANCE_START, InstanceStartCommand(instance_id=created.instance_id)
        )
        assert isinstance(started, LifecycleOperationResult)
        assert (await finished_operation(client, started)).state is OperationState.SUCCEEDED
        runtime.addresses[str(created.instance_id)] = f"http://kinby-{created.instance_id}:8787"
    return {name: created.instance_id for name, created in accepted.instances.items()}


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
            step=CommandStepRun(run=["uv run pytest", "uv run ruff check ."], timeout_seconds=600),
            origin=FactoryRunOrigin(factory="checks", run_id=run.run_id, step="test"),
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
        coder = await installed_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.DONE
        assert [command for _, command in control.steps] == [
            StepRunCommand(
                step=CommandStepRun(run=["make implement"]),
                hook="record_branch",
                origin=FactoryRunOrigin(factory="checks", run_id=run.run_id, step="implement"),
                work_item={"issue": 7},
            ),
            StepRunCommand(
                step=CodeStepRun(call="open_pull_request"),
                origin=FactoryRunOrigin(factory="checks", run_id=run.run_id, step="open-pr"),
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
        coder = await installed_coder(hub, runtime)
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
        coder = await installed_coder(hub, runtime)
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
        coder = await installed_coder(hub, runtime)
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
        coder = await installed_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
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
    hub = factory_hub(tmp_path / "hub", control, runtime, FILES | {"factory.yaml": factory})

    async def scenario() -> None:
        coder = await installed_coder(hub, runtime)
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
        coder = await installed_coder(hub, runtime)
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
        coder = await installed_coder(hub, runtime)
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
        coder = await installed_coder(hub, runtime)
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
        coder = await installed_coder(hub, runtime)
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
        coder = await installed_coder(hub, runtime)
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
        coder = await installed_coder(hub, runtime)
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
        coder = await installed_coder(hub, runtime)
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
        coder = await installed_coder(hub, runtime)
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
        coder = await installed_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.DONE
        [(_, command)] = control.steps
        assert command == StepRunCommand(
            step=AgentStepRun(prompt="Implement it.\n"),
            hook="record_branch",
            origin=FactoryRunOrigin(factory="checks", run_id=run.run_id, step="implement"),
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
"""
CLIENT_FILES = FILES | {
    "factory.yaml": CLIENT,
    "prompts/implement.md": "Implement it.\n",
    "prompts/fix.md": "Fix it.\n",
}


def test_the_hub_sends_a_client_step_its_prompt_and_timeout_and_the_session_it_resumes(tmp_path):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.CLEAN, session="claude-1"),
        StepResult(ending=StepEnding.CLEAN, session="claude-1"),
    ]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, CLIENT_FILES)

    async def scenario() -> None:
        coder = await installed_coder(hub, runtime)
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
            ),
        ]
        assert [attempt.session for attempt in finished.attempts] == ["claude-1", "claude-1"]

    asyncio.run(scenario())


def test_a_client_step_whose_resumed_step_left_no_session_fails_that_step(tmp_path):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.CLEAN)] * 2
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, CLIENT_FILES)

    async def scenario() -> None:
        coder = await installed_coder(hub, runtime)
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
        coder = await installed_coder(hub, runtime)
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
        coder = await installed_coder(hub, runtime)
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
        coder = await installed_coder(hub, runtime)
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
        coder = await installed_coder(hub, runtime)
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
        coder = await installed_coder(hub, runtime)
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
        coder = await installed_coder(hub, runtime)
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
        instances = await installed(hub, runtime, "coder", "checker")
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


def test_a_value_of_another_type_than_its_step_declares_fails_the_step(tmp_path):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.CLEAN, values=BRANCH),
        StepResult(ending=StepEnding.CLEAN, values={"pr": "42"}),
    ]
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", control, runtime, HANDOFF_FILES)

    async def scenario() -> None:
        coder = await installed_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        finished = await settled(hub, run.run_id)

        assert finished.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert finished.run.step == "open-pr"
        last = finished.attempts[-1]
        assert (last.step, last.ending) == ("open-pr", StepEnding.FAILED)
        assert last.summary == 'Step "open-pr" declares "pr" as int, but recorded a str.'

    asyncio.run(scenario())
