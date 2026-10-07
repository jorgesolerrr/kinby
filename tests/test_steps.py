"""Factory steps in the instance: step.run through the instance's dispatcher and control socket."""

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from kinby.contracts import (
    CodeStepRun,
    CommandStepRun,
    ContractModel,
    ErrorCode,
    ErrorEnvelope,
    InstanceDrainCommand,
    RoutineListResult,
    RoutineRunOutcome,
    StepEnding,
    StepResult,
    StepRunCommand,
    ThreadListResult,
)
from kinby.core.dispatcher import Dispatcher, ScheduledDispatcher
from kinby.core.runtime import InstanceRuntime
from kinby.hub import ControlEndpoint, HttpInstanceControl
from tests.test_contract_server import TOKEN, served_dispatcher
from tests.test_drain import WaitingRunner, booted, call, opened_thread
from tests.test_routines import RoutineModel, fire, instance_at, routine_file, signal_runtime


def step_instance(
    tmp_path: Path, runner: WaitingRunner | None = None
) -> tuple[InstanceRuntime, Path]:
    """A booted instance over a scripted runner, and its workspace."""
    instance = instance_at(tmp_path)
    workspace = instance.manifest.workspace.path
    workspace.mkdir(parents=True, exist_ok=True)
    return booted(instance, runner or WaitingRunner()), workspace


async def run_step(dispatcher: Dispatcher, *commands: str) -> ContractModel:
    command = StepRunCommand(step=CommandStepRun(run=list(commands)), work_item={"issue": 7})
    return await call(dispatcher, "step.run", **command.model_dump(mode="json"))


def test_a_command_step_runs_each_command_in_the_workspace_without_a_shell(tmp_path):
    runtime, workspace = step_instance(tmp_path)

    async def scenario() -> None:
        result = await run_step(runtime.dispatcher, "touch made", "echo $HOME > redirected")

        assert isinstance(result, StepResult)
        assert result.ending is StepEnding.CLEAN
        assert (workspace / "made").is_file()
        assert not (workspace / "redirected").exists()

    asyncio.run(scenario())


def test_a_command_step_fails_at_the_first_command_that_exits_with_another_code(tmp_path):
    runtime, workspace = step_instance(tmp_path)

    async def scenario() -> None:
        result = await run_step(
            runtime.dispatcher, "touch made", "sh -c 'echo broken >&2; exit 3'", "touch never"
        )

        assert isinstance(result, StepResult)
        assert result.ending is StepEnding.FAILED
        assert result.summary == "\"sh -c 'echo broken >&2; exit 3'\" exited with code 3.\nbroken"
        assert (workspace / "made").is_file()
        assert not (workspace / "never").exists()

    asyncio.run(scenario())


def test_a_command_still_running_at_the_steps_timeout_is_killed_and_the_step_times_out(tmp_path):
    runtime, workspace = step_instance(tmp_path)

    async def scenario() -> None:
        command = StepRunCommand(
            step=CommandStepRun(run=["sleep 30", "touch never"], timeout_seconds=1),
            work_item={},
        )
        async with asyncio.timeout(10):
            result = await call(runtime.dispatcher, "step.run", **command.model_dump(mode="json"))

        assert isinstance(result, StepResult)
        assert result.ending is StepEnding.TIMED_OUT
        assert result.summary == "The commands ran past the step's timeout of 1s."
        assert not (workspace / "never").exists()

    asyncio.run(scenario())


def test_a_command_that_cannot_start_fails_the_step(tmp_path):
    runtime, _ = step_instance(tmp_path)

    async def scenario() -> None:
        result = await run_step(runtime.dispatcher, "kinby-no-such-command --help")

        assert isinstance(result, StepResult)
        assert result.ending is StepEnding.FAILED
        assert result.summary.startswith('"kinby-no-such-command --help" could not start: ')

    asyncio.run(scenario())


def test_a_step_waits_for_a_running_turn_and_holds_the_instance_like_one(tmp_path):
    runner = WaitingRunner()
    runtime, workspace = step_instance(tmp_path, runner)

    async def scenario() -> None:
        thread = await opened_thread(runtime.dispatcher)
        await call(runtime.dispatcher, "thread.turn.start", thread_id=str(thread), message="Hi")
        await asyncio.wait_for(runner.started.wait(), timeout=5)
        stepping = asyncio.create_task(
            run_step(runtime.dispatcher, "touch started", "sleep 0.3", "touch finished")
        )
        await asyncio.sleep(0.2)
        waited = not (workspace / "started").exists()
        runner.release.set()
        async with asyncio.timeout(5):
            while not (workspace / "started").exists():
                await asyncio.sleep(0.01)
        second = await opened_thread(runtime.dispatcher)
        refused = await call(
            runtime.dispatcher, "thread.turn.start", thread_id=str(second), message="Hi"
        )
        draining = asyncio.create_task(runtime.drain(InstanceDrainCommand()))
        await asyncio.sleep(0)
        drain_waited = not draining.done()
        result = await asyncio.wait_for(stepping, timeout=5)
        await asyncio.wait_for(draining, timeout=5)

        assert waited is True
        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INSTANCE_BUSY
        assert drain_waited is True
        assert isinstance(result, StepResult)
        assert result.ending is StepEnding.CLEAN
        assert (workspace / "finished").is_file()

    asyncio.run(scenario())


def test_a_draining_instance_takes_no_step(tmp_path):
    runtime, workspace = step_instance(tmp_path)

    async def scenario() -> None:
        await runtime.drain(InstanceDrainCommand())
        refused = await run_step(runtime.dispatcher, "touch made")

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INSTANCE_DRAINING
        assert not (workspace / "made").exists()

    asyncio.run(scenario())


def test_the_hub_runs_a_step_over_the_instances_control_socket(tmp_path):
    runtime, workspace = step_instance(tmp_path)

    async def scenario() -> None:
        async with served_dispatcher(runtime.dispatcher) as address:
            result = await HttpInstanceControl().run_step(
                ControlEndpoint(f"http://{address.host}:{address.port}", TOKEN),
                StepRunCommand(step=CommandStepRun(run=["touch made"]), work_item={}),
            )

        assert result.ending is StepEnding.CLEAN
        assert (workspace / "made").is_file()

    asyncio.run(scenario())


VERDICT_HOOK = """\
from kinby.plugins.hooks import HookResult, StepEnd, hook


@hook
def read_verdict(end: StepEnd) -> HookResult:
    verdict = (end.workspace / "verdict").read_text().strip()
    return HookResult(
        values={"verdict": verdict, "ending": end.ending.value, "issue": end.work_item["issue"]},
        outcome=verdict,
    )


@hook
async def record_nothing(end: StepEnd) -> None:
    return None


@hook
def broken(end: StepEnd) -> HookResult:
    raise RuntimeError("the repository is gone")
"""


def hooked_instance(tmp_path: Path) -> tuple[InstanceRuntime, Path]:
    runtime, workspace = step_instance(tmp_path)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "review.py").write_text(VERDICT_HOOK)
    return runtime, workspace


async def run_hooked_step(dispatcher: Dispatcher, hook: str, *commands: str) -> ContractModel:
    command = StepRunCommand(
        step=CommandStepRun(run=list(commands)), hook=hook, work_item={"issue": 7}
    )
    return await call(dispatcher, "step.run", **command.model_dump(mode="json"))


@pytest.mark.parametrize(
    ("command", "ending"),
    [("touch made", StepEnding.CLEAN), ("sh -c 'exit 1'", StepEnding.FAILED)],
)
def test_the_hook_records_the_result_however_the_step_ended(tmp_path, command, ending):
    runtime, workspace = hooked_instance(tmp_path)
    (workspace / "verdict").write_text("changes\n")

    async def scenario() -> None:
        result = await run_hooked_step(runtime.dispatcher, "read_verdict", command)

        assert isinstance(result, StepResult)
        assert result.ending is ending
        assert result.outcome == "changes"
        assert result.values == {"verdict": "changes", "ending": ending.value, "issue": 7}

    asyncio.run(scenario())


def test_a_hook_that_records_nothing_leaves_the_steps_own_result(tmp_path):
    runtime, _ = hooked_instance(tmp_path)

    async def scenario() -> None:
        result = await run_hooked_step(runtime.dispatcher, "record_nothing", "touch made")

        assert result == StepResult(
            ending=StepEnding.CLEAN, summary="Every command exited with code 0."
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("hook", "summary"),
    [
        ("broken", 'Hook "broken" failed: RuntimeError: the repository is gone'),
        ("read_review", 'Hook "read_review" is not one of this instance\'s hooks.'),
    ],
)
def test_a_hook_that_fails_or_is_missing_fails_the_step(tmp_path, hook, summary):
    runtime, _ = hooked_instance(tmp_path)

    async def scenario() -> None:
        result = await run_hooked_step(runtime.dispatcher, hook, "touch made")

        assert isinstance(result, StepResult)
        assert result.ending is StepEnding.FAILED
        assert result.summary == f"Every command exited with code 0.\n{summary}"

    asyncio.run(scenario())


def test_kinbys_default_hooks_record_the_branch_and_find_its_pull_request(tmp_path, monkeypatch):
    runtime, workspace = step_instance(tmp_path)
    git = ("git", "-C", str(workspace))
    subprocess.run((*git, "init", "--quiet", "--initial-branch", "agent/7"), check=True)
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    gh = bin_directory / "gh"
    gh.write_text('#!/bin/sh\necho "$@" > "$0.args"\necho \'{"number": 42}\'\n')
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_directory}{os.pathsep}{os.environ['PATH']}")

    async def scenario() -> None:
        branch = await run_hooked_step(runtime.dispatcher, "record_branch", "true")
        command = StepRunCommand(
            step=CommandStepRun(run=["true"]),
            hook="find_pull_request",
            work_item={"issue": 7},
            results={"branch": "agent/7"},
        )
        pull_request = await call(runtime.dispatcher, "step.run", **command.model_dump(mode="json"))

        assert isinstance(branch, StepResult)
        assert branch.values == {"branch": "agent/7"}
        assert isinstance(pull_request, StepResult)
        assert pull_request.values == {"pr": 42}
        assert (bin_directory / "gh.args").read_text() == "pr view agent/7 --json number\n"

    asyncio.run(scenario())


def test_an_instance_hook_is_never_offered_to_the_model(tmp_path):
    instance = instance_at(tmp_path)
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "review.py").write_text(VERDICT_HOOK)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "github.py").write_text(GITHUB_TOOL)
    routine_file(instance, "description: News")
    model = RoutineModel()

    asyncio.run(fire(instance, model))

    assert "open_pull_request" in model.tools
    assert not {"read_verdict", "record_nothing", "broken", "record_branch"} & set(model.tools)


GITHUB_TOOL = '''\
from kinby.plugins.tools import tool


@tool(write=True)
def open_pull_request(branch: str, issue: int) -> dict[str, int]:
    """Open the pull request of the branch."""
    if branch == "broken":
        raise RuntimeError("GitHub refused the pull request")
    return {"pr": issue * 10}
'''


def code_runtime(tmp_path: Path) -> tuple[ScheduledDispatcher, RoutineModel]:
    """An instance with a model, so a test can see the model is never asked."""
    instance = instance_at(tmp_path)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "github.py").write_text(GITHUB_TOOL)
    model = RoutineModel()
    dispatcher, _ = signal_runtime(instance, model)
    return dispatcher, model


async def run_code_step(
    dispatcher: Dispatcher, tool: str, results: dict[str, str]
) -> ContractModel:
    command = StepRunCommand(
        step=CodeStepRun(call=tool), work_item={"issue": 7, "repo": "kinby"}, results=results
    )
    return await call(dispatcher, "step.run", **command.model_dump(mode="json"))


def test_a_code_step_calls_its_tool_with_the_runs_values_and_no_model(tmp_path):
    dispatcher, model = code_runtime(tmp_path)

    async def scenario() -> None:
        result = await run_code_step(dispatcher, "open_pull_request", {"branch": "agent/7"})
        threads = await call(dispatcher, "thread.list")

        assert isinstance(result, StepResult)
        assert result.ending is StepEnding.CLEAN
        assert result.values == {"pr": 70}
        assert model.messages == []
        assert model.tools == []
        assert isinstance(threads, ThreadListResult)
        assert threads.threads == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("tool", "summary"),
    [
        ("open_pull_request", 'Tool "open_pull_request" failed: RuntimeError: GitHub refused'),
        ("merge", 'Tool "merge" is not one of this instance\'s tools.'),
    ],
)
def test_a_code_step_whose_tool_fails_or_is_missing_fails(tmp_path, tool, summary):
    dispatcher, _ = code_runtime(tmp_path)

    async def scenario() -> None:
        result = await run_code_step(dispatcher, tool, {"branch": "broken"})

        assert isinstance(result, StepResult)
        assert result.ending is StepEnding.FAILED
        assert result.summary.startswith(summary)

    asyncio.run(scenario())


def test_a_factory_runs_failed_steps_never_count_toward_its_intake_routines_failure_streak(
    tmp_path,
):
    dispatcher, model = code_runtime(tmp_path)
    instance = instance_at(tmp_path)
    routine_file(instance, "description: Intake\nmode: full-access")

    async def scenario() -> None:
        await fire(instance, model)
        failed = [
            await run_code_step(dispatcher, "open_pull_request", {"branch": "broken"}),
            await run_step(dispatcher, "sh -c 'exit 1'"),
        ]
        listed = await call(dispatcher, "routine.list")

        assert {result.ending for result in failed if isinstance(result, StepResult)} == {
            StepEnding.FAILED
        }
        assert isinstance(listed, RoutineListResult)
        [intake] = listed.routines
        assert (intake.failure_count, intake.enabled) == (0, True)
        assert intake.last_run is not None
        assert intake.last_run.outcome is RoutineRunOutcome.WORK

    asyncio.run(scenario())
