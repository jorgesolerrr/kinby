"""A work item handed to a line runs on the line's workbench, in a folder the hub names for the
run, through the hub contract."""

import asyncio
from pathlib import Path

from kinby.cli.client import ContractClient
from kinby.contracts import (
    FACTORY_RUN_CANCEL,
    FACTORY_RUN_LIST,
    FACTORY_RUN_RETRY,
    LINE_HAND_IN,
    LINE_SETUP,
    WORKBENCH_START,
    CodeStepRun,
    CommandStepRun,
    ErrorCode,
    ErrorEnvelope,
    FactoryRun,
    FactoryRunCancelCommand,
    FactoryRunListCommand,
    FactoryRunListResult,
    FactoryRunRetryCommand,
    FactoryRunStatus,
    Line,
    LineHandInCommand,
    LineSetupCommand,
    LineSetupResult,
    OperationState,
    RunFolder,
    StepEnding,
    StepResult,
    StorageItem,
    StorageKind,
    WorkbenchCreateCommand,
    WorkbenchStartCommand,
)
from kinby.hub import Hub
from kinby.workbench import FACTORIES_MOUNT
from tests.test_factory_runs import settled
from tests.test_hub import FakeControl, FakeRuntime, hub_at, hub_client
from tests.test_hub_factories import write_factory
from tests.test_hub_lines import _home
from tests.test_hub_workbench_workspace import _commands_set
from tests.test_hub_workbenches import CEC_BACKEND, created, finished

TICKETS = {
    "factory.yaml": """\
name: tickets
requires: { image: coder, commands: [checks] }
work_item: { issue: int }
steps:
  - id: test
    kind: command
    run: ["make lint", { commands: checks }]
  - id: label
    kind: code
    call: label_issue
""",
    "tools/label.py": """\
from kinby.plugins.tools import tool


@tool(write=True)
def label_issue(issue: int) -> dict[str, int]:
    \"\"\"Label the issue.\"\"\"
    return {"labelled": issue}
""",
}
TASKS = """\
name: tasks
requires: { image: coder }
work_item: { task: str }
steps:
  - { id: draft, kind: command, run: ["make draft"] }
"""
LANDING = CEC_BACKEND.model_copy(
    update={"name": "landing", "workspace_source": "https://github.com/owner/landing.git"}
)
COURSE = WorkbenchCreateCommand(name="course", recipe="coder")


def _hub(tmp_path: Path, control: FakeControl, runtime: FakeRuntime) -> Hub:
    write_factory(tmp_path / "hub" / "factories" / "tickets", TICKETS)
    write_factory(tmp_path / "hub" / "factories" / "tasks", {"factory.yaml": TASKS})
    return hub_at(
        tmp_path / "hub",
        runtime=runtime,
        control=control,
        shipped_factories=tmp_path / "shipped",
    )


async def _line(
    client: ContractClient,
    runtime: FakeRuntime,
    workbench: WorkbenchCreateCommand = CEC_BACKEND,
    factory: str = "tickets",
) -> Line:
    """The factory set up on a started workbench that holds the tickets factory's checks."""
    workbench_id = (await created(client, workbench)).workbench_id
    await _commands_set(client, workbench_id, "checks", ["uv run pytest"])
    start = await client.call(WORKBENCH_START, WorkbenchStartCommand(workbench_id=workbench_id))
    assert (await finished(client, start)).state is OperationState.SUCCEEDED
    runtime.addresses[str(workbench_id)] = f"http://kinby-{workbench_id}:8787"
    line = await client.call(
        LINE_SETUP, LineSetupCommand(factory=factory, workbench_id=workbench_id)
    )
    assert isinstance(line, LineSetupResult), line
    return line.line


async def _handed_in(client: ContractClient, line: Line, issue: int) -> FactoryRun:
    run = await client.call(
        LINE_HAND_IN, LineHandInCommand(line_id=line.line_id, work_item={"issue": issue})
    )
    assert isinstance(run, FactoryRun), run
    return run


async def _runs(client: ContractClient, line: Line) -> list[FactoryRun]:
    listed = await client.call(
        FACTORY_RUN_LIST, FactoryRunListCommand(factory="tickets", line_id=line.line_id)
    )
    assert isinstance(listed, FactoryRunListResult), listed
    return listed.runs


async def _removed(control: FakeControl) -> list[RunFolder]:
    """The run folders the hub asked a workbench to remove, once it asked for one."""
    async with asyncio.timeout(5):
        while not control.removed_folders:
            await asyncio.sleep(0.01)
    return [command.folder for _, command in control.removed_folders]


def test_a_work_item_handed_to_a_line_runs_its_steps_on_the_workbench_in_a_run_worktree(
    tmp_path,
):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        home = await _home(hub)
        line = await _line(client, runtime)

        run = await _handed_in(client, line, 12)
        done = await settled(hub, run.run_id)
        removed = await _removed(control)

        worktree = RunFolder(branch=f"tickets/issue-12-{run.run_id.hex[:8]}")
        assert (run.line_id, run.instance_id) == (line.line_id, home)
        assert done.run.status is FactoryRunStatus.DONE
        assert [endpoint.address for endpoint, _ in control.steps] == 2 * [
            f"http://kinby-{line.workbench_id}:8787"
        ]
        assert [(command.step, command.folder) for _, command in control.steps] == [
            (CommandStepRun(run=["make lint", "uv run pytest"]), worktree),
            (CodeStepRun(call="label_issue"), worktree),
        ]
        assert removed == [worktree]
        assert control.removed_folders[0][1].run_id == run.run_id

    asyncio.run(scenario())


def test_on_a_plain_workspace_a_run_works_in_a_directory_named_for_it(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime, COURSE)

        run = await _handed_in(client, line, 12)
        await settled(hub, run.run_id)

        assert [command.folder for _, command in control.steps] == 2 * [RunFolder(branch=None)]
        assert await _removed(control) == [RunFolder(branch=None)]

    asyncio.run(scenario())


def test_a_runs_folder_stays_through_a_stop_and_a_retry_and_goes_once_it_is_cancelled(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)
    failed = StepResult(ending=StepEnding.FAILED, summary="make lint exited with code 2.")
    control.step_results = [failed, failed]

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)
        run = await _handed_in(client, line, 12)
        stopped = await settled(hub, run.run_id)

        await client.call(FACTORY_RUN_RETRY, FactoryRunRetryCommand(run_id=run.run_id))
        stopped_again = await settled(hub, run.run_id)
        kept = list(control.removed_folders)
        await client.call(FACTORY_RUN_CANCEL, FactoryRunCancelCommand(run_id=run.run_id))

        assert stopped.run.status is stopped_again.run.status is FactoryRunStatus.NEEDS_HUMAN
        worktree = RunFolder(branch=f"tickets/issue-12-{run.run_id.hex[:8]}")
        assert [command.folder for _, command in control.steps] == 2 * [worktree]
        assert kept == []
        assert await _removed(control) == [worktree]

    asyncio.run(scenario())


def test_work_items_that_differ_only_in_case_and_punctuation_work_in_folders_of_their_own(
    tmp_path,
):
    control = FakeControl()
    control.step_release.clear()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime, factory="tasks")

        for task in ("Fix A", "fix-a"):
            handed = await client.call(
                LINE_HAND_IN, LineHandInCommand(line_id=line.line_id, work_item={"task": task})
            )
            assert isinstance(handed, FactoryRun), handed
        control.step_release.set()
        async with asyncio.timeout(5):
            while len(control.steps) < 2:
                await asyncio.sleep(0.01)

        branches = [
            command.folder.branch if command.folder is not None else None
            for _, command in control.steps
        ]
        assert branches[0] != branches[1]
        assert all(
            branch is not None and branch.startswith("tasks/task-fix-a-") for branch in branches
        )

    asyncio.run(scenario())


def test_two_lines_each_carry_a_work_item_with_the_same_values_in_runs_of_their_own(tmp_path):
    control = FakeControl()
    control.step_release.clear()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        cec_backend = await _line(client, runtime)
        landing = await _line(client, runtime, LANDING)

        first = await _handed_in(client, cec_backend, 12)
        second = await _handed_in(client, landing, 12)

        assert first.run_id != second.run_id
        assert [run.run_id for run in await _runs(client, cec_backend)] == [first.run_id]
        assert [run.run_id for run in await _runs(client, landing)] == [second.run_id]
        control.step_release.set()

    asyncio.run(scenario())


def test_a_work_item_that_matches_an_unfinished_run_of_the_line_returns_that_run(tmp_path):
    control = FakeControl()
    control.step_release.clear()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)

        first = await _handed_in(client, line, 12)
        again = await _handed_in(client, line, 12)

        assert again.run_id == first.run_id
        assert [run.run_id for run in await _runs(client, line)] == [first.run_id]
        control.step_release.set()

    asyncio.run(scenario())


def test_a_line_takes_only_the_work_item_its_factory_declares(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)

        refused = await client.call(
            LINE_HAND_IN, LineHandInCommand(line_id=line.line_id, work_item={"pr": 12})
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert refused.fields == {
            "pr": "The factory's work item carries no value by this name.",
            "issue": "The work item needs this value.",
        }
        assert await _runs(client, line) == []

    asyncio.run(scenario())


def test_a_workbench_reads_the_hubs_factory_folders_where_they_are(tmp_path):
    runtime = FakeRuntime()
    hub = _hub(tmp_path, FakeControl(), runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)

        [container] = [
            created for created in runtime.created if created.instance_id == str(line.workbench_id)
        ]
        assert (
            StorageItem(
                kind=StorageKind.BIND,
                source=str(tmp_path / "hub" / "factories"),
                destination=str(FACTORIES_MOUNT),
                writable=False,
            )
            in container.storage
        )

    asyncio.run(scenario())
