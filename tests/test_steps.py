"""Factory steps in the instance: step.run through the instance's dispatcher and control socket."""

import asyncio
from pathlib import Path

from kinby.contracts import (
    CommandStepRun,
    ContractModel,
    ErrorCode,
    ErrorEnvelope,
    InstanceDrainCommand,
    StepEnding,
    StepResult,
    StepRunCommand,
)
from kinby.core.dispatcher import Dispatcher
from kinby.core.runtime import InstanceRuntime
from kinby.hub import ControlEndpoint, HttpInstanceControl
from tests.test_contract_server import TOKEN, served_dispatcher
from tests.test_drain import WaitingRunner, booted, call, opened_thread
from tests.test_routines import instance_at


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
