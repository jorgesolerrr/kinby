"""Run one factory step in this instance, then the hook that records its result."""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path
from uuid import uuid4

from kinby.contracts import (
    AgentStepRun,
    ClientStepRun,
    CodeStepRun,
    CommandStepRun,
    Event,
    HookName,
    MessageDelta,
    RepositoryCommands,
    RunDelegated,
    StepEnding,
    StepResult,
    StepRunCommand,
    ToolCall,
    TurnCompleted,
    TurnFailed,
)
from kinby.core.coding_clients import own_tokens, run_coding_client
from kinby.core.errors import CoreError
from kinby.core.events import EventLog
from kinby.core.prompt import render_client_message, render_step_message
from kinby.core.session_recalls import take_recalls
from kinby.core.turns import Turns
from kinby.instance import Instance, ManifestError, repository_commands
from kinby.instance.permissions import PermissionsError
from kinby.plugins.errors import exception_message
from kinby.plugins.hooks import HookResult, StepEnd, load_hooks
from kinby.plugins.registry import ToolRegistry
from kinby.plugins.routines import SharedCodeStep, resolve_code_step
from kinby.plugins.skills import load_skills
from kinby.plugins.tools import ToolContext

#: How much of a failed command's output its step's summary keeps, from the end.
_OUTPUT_TAIL = 2_000
#: The longest summary a step result keeps.
_SUMMARY_CHARACTERS = 16_000
#: The largest step result the instance sends, serialized. Far under the hub's frame limit.
_RESULT_BYTES = 1024 * 1024


async def run_step(
    command: StepRunCommand, instance: Instance, turns: Turns, log: EventLog
) -> StepResult:
    """Run the step, then its hook however the step ended. The hook's values are the result's."""
    match command.step:
        case AgentStepRun() as step:
            result = await _run_agent_step(step, command, turns)
        case ClientStepRun() as step:
            result = await _run_client_step(step, command, instance, log)
        case CommandStepRun() as step:
            result = await run_command_step(step, instance)
        case CodeStepRun() as step:
            result = await _run_code_step(step, command, instance)
    if command.hook is not None:
        result = await _recorded(command.hook, result, command, instance)
    return _bounded(result)


def _bounded(result: StepResult) -> StepResult:
    """The result with a long summary cut to its start and its end. One still too large to
    send fails the step, and says how large it is."""
    if len(result.summary) > _SUMMARY_CHARACTERS:
        kept = _SUMMARY_CHARACTERS // 2
        cut = len(result.summary) - 2 * kept
        summary = f"{result.summary[:kept]}\n[{cut:,} characters cut]\n{result.summary[-kept:]}"
        result = result.model_copy(update={"summary": summary})
    size = len(result.model_dump_json().encode())
    if size > _RESULT_BYTES:
        return _failed(
            f"The step's result is {size:,} bytes, over the {_RESULT_BYTES:,}-byte limit.", result
        )
    return result


async def _run_agent_step(step: AgentStepRun, command: StepRunCommand, turns: Turns) -> StepResult:
    """Run the step's turn on a new thread. The agent's last message is only the summary."""
    title = " · ".join(
        (
            command.origin.factory,
            command.origin.step,
            *(f"{name} {value}" for name, value in command.work_item.items()),
        )
    )
    message = render_step_message(step.prompt, command.work_item, command.results)
    try:
        events = await turns.step_turn(title, message, command.origin)
    except (CoreError, ManifestError, PermissionsError) as exc:
        return _failed(f"The turn could not start: {exc}")
    said = StepResult(ending=StepEnding.FAILED, summary=_last_message(events))
    match events[-1].payload:
        case TurnCompleted():
            return said.model_copy(update={"ending": StepEnding.CLEAN})
        case TurnFailed(message=reason):
            return _failed(f"The turn failed: {reason}", said)
        case _:
            return _failed("The turn was interrupted.", said)


async def _run_client_step(
    step: ClientStepRun, command: StepRunCommand, instance: Instance, log: EventLog
) -> StepResult:
    """Run the coding client in the workspace, and record its delegated run and its session's
    recalls.

    What the client said last is only the summary.
    """
    skills, warnings = load_skills(instance)
    for warning in warnings:
        await log.append(command.origin.run_id, uuid4(), warning)
    ran = await run_coding_client(
        step.client,
        render_client_message(step.prompt, command.work_item, command.results, skills),
        instance.manifest.workspace.path,
        instance=instance.path,
        state_dir=instance.manifest.state_dir,
        origin=command.origin,
        attempt=command.attempt,
        resume=step.resume,
        timeout_seconds=step.timeout_seconds,
        model=step.model,
        effort=step.effort,
    )
    if ran.reported is not None:
        earlier = (
            event.payload.run
            for event in log.all_events()
            if isinstance(event.payload, RunDelegated)
        )
        # The run belongs to no thread or turn. Its events go under the factory run's id.
        await log.append(
            command.origin.run_id,
            uuid4(),
            RunDelegated(
                run=own_tokens(ran.reported, earlier),
                step=command.origin.step,
                attempt=command.attempt,
            ),
        )
    for recall in take_recalls(instance.manifest.state_dir, command.origin.run_id):
        await log.append(
            command.origin.run_id, uuid4(), recall.model_copy(update={"session": ran.session})
        )
    return StepResult(ending=ran.ending, summary=ran.summary, session=ran.session)


def _last_message(events: Sequence[Event]) -> str:
    """What the agent said after its last tool call."""
    text = ""
    for event in events:
        match event.payload:
            case MessageDelta(text=delta):
                text += delta
            case ToolCall():
                text = ""
    return text.strip()


async def run_command_step(step: CommandStepRun, instance: Instance) -> StepResult:
    """Run each command in the instance's workspace without a shell. The first that exits
    non-zero fails.

    A command still running when the step's timeout passes is killed, and the step times out.
    """
    commands = _commands(step, instance.path)
    if isinstance(commands, StepResult):
        return commands
    try:
        async with asyncio.timeout(step.timeout_seconds):
            for command in commands:
                failed = await _run_command(command, instance.manifest.workspace.path)
                if failed is not None:
                    return failed
    except TimeoutError:
        return StepResult(
            ending=StepEnding.TIMED_OUT,
            summary=f"The commands ran past the step's timeout of {step.timeout_seconds}s.",
        )
    return StepResult(ending=StepEnding.CLEAN, summary="Every command exited with code 0.")


def _commands(step: CommandStepRun, instance: Path) -> list[str] | StepResult:
    """The step's commands, with each list of repository commands it names read from the
    instance now and put in its place.

    A list the instance does not hold needs a human, since no retry or repair can supply it.
    """
    if all(isinstance(entry, str) for entry in step.run):
        held = {}
    else:
        try:
            held = repository_commands(instance)
        except ManifestError as exc:
            return _failed(f"The repository commands cannot be read: {exc}")
    commands: list[str] = []
    for entry in step.run:
        match entry:
            case str():
                commands.append(entry)
            case RepositoryCommands(commands=name) if name in held:
                commands.extend(held[name])
            case RepositoryCommands(commands=name):
                return StepResult(
                    ending=StepEnding.NEEDS_HUMAN,
                    summary=f'This instance holds no repository commands named "{name}". '
                    "Ask it in a thread for a repository survey.",
                )
    return commands


async def _run_code_step(
    step: CodeStepRun, command: StepRunCommand, instance: Instance
) -> StepResult:
    """Call the step's tool as a routine's code step is called, with the run's values.

    A mapping the tool returns is the result's values. A HookResult also names its outcome.
    """
    tools, _ = ToolRegistry(instance.path, defaults=instance.manifest.tools.defaults).refresh()
    try:
        tool = resolve_code_step(SharedCodeStep(step.call), tools)
    except ValueError:
        return _failed(f'Tool "{step.call}" is not one of this instance\'s tools.')
    held = {**command.work_item, **command.results}
    if step.summary is not None:
        # The factory's needs_human call: the step the run stopped at, and how it ended.
        held |= {"step": command.origin.step, "summary": step.summary}
    arguments = {name: value for name, value in held.items() if name in tool.runnable.args}
    # A code step runs on no thread, so its tool's context names one no turn opens.
    context = ToolContext(instance=instance, thread_id=uuid4())
    try:
        returned = await tool.ainvoke_value(arguments, context)
        if isinstance(returned, HookResult):
            return StepResult(
                ending=StepEnding.CLEAN,
                summary=returned.summary,
                outcome=returned.outcome,
                values=dict(returned.values),
            )
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
    the instance does not have, fails a step that ended clean and keeps any other ending.
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
        return result.model_copy(
            update={"outcome": recorded.outcome, "values": dict(recorded.values)}
        )
    except Exception as exc:
        # Hooks are user code. Whatever goes wrong fails the step, and says why.
        return _failed(f'Hook "{name}" failed: {exception_message(exc)}', result)


def _failed(reason: str, ended: StepResult | None = None) -> StepResult:
    """A failed result that says why, after the summary of the step as it *ended*.

    A step that ended otherwise than clean keeps its ending, so a timeout is never tried again.
    """
    summary = reason if ended is None or not ended.summary else f"{ended.summary}\n{reason}"
    if ended is None or ended.ending is StepEnding.CLEAN:
        return StepResult(ending=StepEnding.FAILED, summary=summary)
    return StepResult(ending=ended.ending, summary=summary)


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
