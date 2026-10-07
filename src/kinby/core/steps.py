"""Run one factory step in this instance, then the hook that records its result."""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4

from kinby.contracts import (
    CodeStepRun,
    CommandStepRun,
    HookName,
    StepEnding,
    StepResult,
    StepRunCommand,
)
from kinby.instance import Instance
from kinby.packages import PackageConfigError, instance_package_config
from kinby.plugins.errors import exception_message
from kinby.plugins.hooks import StepEnd, load_hooks
from kinby.plugins.registry import ToolRegistry
from kinby.plugins.routines import SharedCodeStep, resolve_code_step
from kinby.plugins.tools import ToolContext

#: How much of a failed command's output its step's summary keeps, from the end.
_OUTPUT_TAIL = 2_000


async def run_step(command: StepRunCommand, instance: Instance) -> StepResult:
    """Run the step, then its hook however the step ended. The hook's values are the result's."""
    match command.step:
        case CommandStepRun() as step:
            result = await run_command_step(step, instance.manifest.workspace.path)
        case CodeStepRun() as step:
            result = await _run_code_step(step, command, instance)
    if command.hook is None:
        return result
    return await _recorded(command.hook, result, command, instance)


async def run_command_step(step: CommandStepRun, workspace: Path) -> StepResult:
    """Run each command in *workspace* without a shell. The first that exits non-zero fails.

    A command still running when the step's timeout passes is killed, and the step times out.
    """
    try:
        async with asyncio.timeout(step.timeout_seconds):
            for command in step.run:
                failed = await _run_command(command, workspace)
                if failed is not None:
                    return failed
    except TimeoutError:
        return StepResult(
            ending=StepEnding.TIMED_OUT,
            summary=f"The commands ran past the step's timeout of {step.timeout_seconds}s.",
        )
    return StepResult(ending=StepEnding.CLEAN, summary="Every command exited with code 0.")


async def _run_code_step(
    step: CodeStepRun, command: StepRunCommand, instance: Instance
) -> StepResult:
    """Call the step's tool as a routine's code step is called, with the run's values."""
    tools, _ = ToolRegistry(instance.path, defaults=instance.manifest.tools.defaults).refresh()
    try:
        tool = resolve_code_step(SharedCodeStep(step.call), tools)
    except ValueError:
        return _failed(f'Tool "{step.call}" is not one of this instance\'s tools.')
    held = {**command.work_item, **command.results}
    arguments = {name: value for name, value in held.items() if name in tool.runnable.args}
    try:
        package_config = instance_package_config(instance)
    except PackageConfigError as exc:
        return _failed(f'Tool "{step.call}" could not read the package config: {exc}')
    # A code step runs on no thread, so its tool's context names one no turn opens.
    context = ToolContext(instance=instance, thread_id=uuid4(), package_config=package_config)
    try:
        returned = await tool.ainvoke_value(arguments, context)
        if isinstance(returned, Mapping):
            return StepResult(ending=StepEnding.CLEAN, values=dict(returned))
    except Exception as exc:
        # Tools are user code. Whatever goes wrong fails the step, and says why.
        return _failed(f'Tool "{step.call}" failed: {exception_message(exc)}')
    return StepResult(ending=StepEnding.CLEAN, summary="" if returned is None else str(returned))


async def _recorded(
    name: HookName, result: StepResult, command: StepRunCommand, instance: Instance
) -> StepResult:
    """The step's result with the values and outcome its hook records.

    A hook that records nothing leaves the result as the step ended. One that fails, or that
    the instance does not have, fails the step.
    """
    hooks, warnings = load_hooks(instance.path)
    found = next((hook for hook in hooks if hook.name == name), None)
    if found is None:
        unloaded = "".join(f"\n{warning.sources[0]}: {warning.message}" for warning in warnings)
        return _failed(f'Hook "{name}" is not one of this instance\'s hooks.{unloaded}', result)
    end = StepEnd(
        instance=instance,
        ending=result.ending,
        work_item=command.work_item,
        results=command.results,
    )
    try:
        recorded = await found.record(end)
        if recorded is None:
            return result
        return StepResult(
            ending=result.ending,
            outcome=recorded.outcome,
            values=dict(recorded.values),
            summary=result.summary,
        )
    except Exception as exc:
        # Hooks are user code. Whatever goes wrong fails the step, and says why.
        return _failed(f'Hook "{name}" failed: {exception_message(exc)}', result)


def _failed(reason: str, ended: StepResult | None = None) -> StepResult:
    """A failed result that says why, after the summary of the step as it *ended*."""
    summary = reason if ended is None or not ended.summary else f"{ended.summary}\n{reason}"
    return StepResult(ending=StepEnding.FAILED, summary=summary)


async def _run_command(command: str, workspace: Path) -> StepResult | None:
    """Run one command to its end, and return the failed step result unless it exits with 0."""
    try:
        process = await asyncio.create_subprocess_exec(
            *shlex.split(command),
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except (OSError, ValueError) as exc:
        return StepResult(ending=StepEnding.FAILED, summary=f'"{command}" could not start: {exc}')
    try:
        output, _ = await process.communicate()
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise
    if process.returncode != 0:
        tail = output.decode(errors="replace").strip()[-_OUTPUT_TAIL:]
        return StepResult(
            ending=StepEnding.FAILED,
            summary=f'"{command}" exited with code {process.returncode}.\n{tail}'.strip(),
        )
    return None
