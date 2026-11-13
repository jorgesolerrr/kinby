"""Run one factory step in this instance, then the hook that records its result."""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from uuid import uuid4

from pydantic import JsonValue

from kinby.contracts import (
    AgentStepRun,
    ClientStepRun,
    CodeStepRun,
    CommandStepRun,
    DelegatedRun,
    DelegatedRunOutcome,
    Event,
    EventType,
    HookName,
    MemoryRecalled,
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
from kinby.core.run_worktrees import RunWorktreeError, run_worktree
from kinby.core.session_recalls import take_recalls
from kinby.core.turns import Turns
from kinby.instance import Instance, ManifestError, repository_commands
from kinby.instance.permissions import PermissionsError
from kinby.memory import GraphStore, MemoryDelivery, deliver_memory
from kinby.plugins.errors import exception_message
from kinby.plugins.hooks import Hook, HookResult, StepEnd, load_hooks
from kinby.plugins.registry import ToolRegistry
from kinby.plugins.routines import SharedCodeStep, resolve_code_step
from kinby.plugins.skills import load_skills
from kinby.plugins.tools import Tool, ToolContext

#: How much of a failed command's output its step's summary keeps, from the end.
_OUTPUT_TAIL = 2_000
#: The longest summary a step result keeps.
_SUMMARY_CHARACTERS = 16_000
#: The largest step result the instance sends, serialized. Far under the hub's frame limit.
_RESULT_BYTES = 1024 * 1024


async def run_step(
    command: StepRunCommand,
    instance: Instance,
    turns: Turns,
    log: EventLog,
    memory_delivery: MemoryDelivery = MemoryDelivery.NONE,
) -> StepResult:
    """Run the step, then its hook however the step ended. The hook's values are the result's.
    A client step gets its memory by *memory_delivery*.

    A step that names a branch runs in that branch's run worktree, and its hook reads it there.
    When the run worktree cannot be had, the step fails before it starts and runs no hook.
    """
    workspace = instance.manifest.workspace.path
    if isinstance(command.step, ClientStepRun | CommandStepRun) and command.step.branch is not None:
        try:
            workspace = await asyncio.to_thread(
                run_worktree,
                instance.path,
                workspace,
                command.step.branch,
                str(command.origin.run_id),
            )
        except RunWorktreeError as exc:
            return step_failed(str(exc))
    match command.step:
        case AgentStepRun() as step:
            result = await _run_agent_step(step, command, turns)
        case ClientStepRun() as step:
            result = await _run_client_step(
                step, command, instance, workspace, log, memory_delivery
            )
        case CommandStepRun() as step:
            result = await run_command_step(step, instance, workspace)
        case CodeStepRun() as step:
            result = await _run_code_step(step, command, instance)
    if command.hook is not None:
        result = await _recorded(command.hook, result, command, instance, workspace)
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
        return step_failed(
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
        return step_failed(f"The turn could not start: {exc}")
    said = StepResult(ending=StepEnding.FAILED, summary=last_message(events))
    match events[-1].payload:
        case TurnCompleted():
            return said.model_copy(update={"ending": StepEnding.CLEAN})
        case TurnFailed(message=reason):
            return step_failed(f"The turn failed: {reason}", said)
        case _:
            return step_failed("The turn was interrupted.", said)


async def _run_client_step(
    step: ClientStepRun,
    command: StepRunCommand,
    instance: Instance,
    workspace: Path,
    log: EventLog,
    memory_delivery: MemoryDelivery,
) -> StepResult:
    """Run the coding client in *workspace*, and record its delegated run, the facts a pre-search
    put in its message once it started, and its session's recalls.

    What the client said last is only the summary. A run the plan refused ends the step limited,
    whatever the client exited with.
    """
    skills, warnings = load_skills(instance)
    for warning in warnings:
        await log.append(command.origin.run_id, uuid4(), warning)
    # What an execution the instance abandoned kept belongs to no recorded run.
    take_recalls(instance.manifest.state_dir, command.origin.run_id)
    delivered = deliver_memory(memory_delivery, GraphStore(instance.path), step.prompt)
    ran = await run_coding_client(
        step.client,
        render_client_message(
            step.prompt,
            command.work_item,
            command.results,
            skills,
            None if delivered is None else delivered.text,
        ),
        workspace,
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
        reported = ran.reported

        def own() -> DelegatedRun:
            with log.reader() as reader:
                earlier = (
                    event.payload.run
                    for event in reader.events_of({EventType.RUN_DELEGATED})
                    if isinstance(event.payload, RunDelegated)
                )
                return own_tokens(reported, earlier)

        # The run belongs to no thread or turn. Its events go under the factory run's id.
        await log.append(
            command.origin.run_id,
            uuid4(),
            RunDelegated(
                run=await asyncio.to_thread(own),
                step=command.origin.step,
                attempt=command.attempt,
            ),
        )
    if ran.reported is not None and delivered is not None and delivered.recalled:
        await log.append(
            command.origin.run_id,
            uuid4(),
            MemoryRecalled(
                nodes=delivered.recalled,
                step=command.origin.step,
                attempt=command.attempt,
                session=ran.transcript,
            ),
        )
    for recall in take_recalls(instance.manifest.state_dir, command.origin.run_id):
        await log.append(
            command.origin.run_id, uuid4(), recall.model_copy(update={"session": ran.transcript})
        )
    if ran.reported is not None and ran.reported.outcome is DelegatedRunOutcome.LIMITED:
        return StepResult(
            ending=StepEnding.LIMITED,
            summary=ran.summary,
            session=ran.session,
            resets_at=ran.reported.resets_at,
        )
    return StepResult(ending=ran.ending, summary=ran.summary, session=ran.session)


def last_message(events: Sequence[Event]) -> str:
    """What the agent said after its last tool call."""
    text = ""
    for event in events:
        match event.payload:
            case MessageDelta(text=delta):
                text += delta
            case ToolCall():
                text = ""
    return text.strip()


async def run_command_step(step: CommandStepRun, instance: Instance, workspace: Path) -> StepResult:
    """Run each command in *workspace* without a shell. The first that exits non-zero fails.

    A command still running when the step's timeout passes is killed, and the step times out.
    """
    commands = _commands(step, instance.path)
    if isinstance(commands, StepResult):
        return commands
    return await run_commands(commands, step.timeout_seconds, workspace)


async def run_commands(
    commands: Sequence[str], timeout_seconds: int | None, workspace: Path
) -> StepResult:
    """Run each command in *workspace* without a shell. The first that exits non-zero fails.

    A command still running when *timeout_seconds* pass is killed, and the step times out.
    """
    try:
        async with asyncio.timeout(timeout_seconds):
            for command in commands:
                failed = await _run_command(command, workspace)
                if failed is not None:
                    return failed
    except TimeoutError:
        return StepResult(
            ending=StepEnding.TIMED_OUT,
            summary=f"The commands ran past the step's timeout of {timeout_seconds}s.",
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
            return step_failed(f"The repository commands cannot be read: {exc}")
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
    """Call one of the instance's tools as a routine's code step is called."""
    tools, _ = ToolRegistry(instance.path, tools=instance.manifest.tools).refresh()
    try:
        tool = resolve_code_step(SharedCodeStep(step.call), tools)
    except ValueError:
        return step_failed(f'Tool "{step.call}" is not one of this instance\'s tools.')
    # A code step runs on no thread, so its tool's context names one no turn opens.
    context = ToolContext(instance=instance, thread_id=uuid4())
    return await run_tool(
        tool, step, command, lambda arguments: tool.ainvoke_value(arguments, context)
    )


async def run_tool(
    tool: Tool,
    step: CodeStepRun,
    command: StepRunCommand,
    invoke: Callable[[dict[str, JsonValue]], Awaitable[object]],
) -> StepResult:
    """Invoke the code step's tool with the run's values that its parameters name, and its id.

    A mapping the tool returns is the result's values. A HookResult also names its outcome.
    """
    held = {**command.work_item, **command.results, "run_id": str(command.origin.run_id)}
    if step.summary is not None:
        # The factory's needs_human call: the step the run stopped at, and how it ended.
        held |= {"step": command.origin.step, "summary": step.summary}
    arguments: dict[str, JsonValue] = {
        name: value for name, value in held.items() if name in tool.runnable.args
    }
    try:
        returned = await invoke(arguments)
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
        return step_failed(f'Tool "{step.call}" failed: {exception_message(exc)}')
    return StepResult(ending=StepEnding.CLEAN, summary="" if returned is None else str(returned))


async def _recorded(
    name: HookName, result: StepResult, command: StepRunCommand, instance: Instance, workspace: Path
) -> StepResult:
    """The step's result with the values and outcome its hook records, reading *workspace*.

    A hook the instance does not have fails a step that ended clean and keeps any other ending.
    """
    hooks, warnings = load_hooks(instance.path)
    found = next((hook for hook in hooks if hook.name == name), None)
    if found is None:
        unloaded = "".join(f"\n{warning.sources[0]}: {warning.message}" for warning in warnings)
        return step_failed(f'Hook "{name}" is not one of this instance\'s hooks.{unloaded}', result)
    return await recorded(found, "Hook", result, command, workspace, instance)


async def recorded(
    found: Hook,
    called: str,
    result: StepResult,
    command: StepRunCommand,
    workspace: Path,
    instance: Instance | None = None,
) -> StepResult:
    """The step's result with what *found*, its hook or its check as *called* says, records
    reading *workspace*.

    A hook that records nothing leaves the result as the step ended. The values it records join
    the step's, and an outcome it names replaces the step's. One that fails fails a step that
    ended clean and keeps any other ending.
    """
    end = StepEnd(
        instance=instance,
        workspace=workspace,
        ending=result.ending,
        work_item=command.work_item,
        results=command.results,
    )
    try:
        record = await found.record(end)
    except Exception as exc:
        # Hooks are user code. Whatever goes wrong fails the step, and says why.
        return step_failed(f'{called} "{found.name}" failed: {exception_message(exc)}', result)
    if record is None:
        return result
    return result.model_copy(
        update={
            "outcome": result.outcome if record.outcome is None else record.outcome,
            "values": result.values | dict(record.values),
        }
    )


def step_failed(reason: str, ended: StepResult | None = None) -> StepResult:
    """A failed result that says why, after the summary of the step as it *ended*.

    A step that ended otherwise than clean keeps its ending, so a timeout is never tried again,
    and a limited one keeps when its plan window resets. What the step spent stays.
    """
    summary = reason if ended is None or not ended.summary else f"{ended.summary}\n{reason}"
    usage = ended.usage if ended is not None else None
    if ended is None or ended.ending is StepEnding.CLEAN:
        return StepResult(ending=StepEnding.FAILED, summary=summary, usage=usage)
    return StepResult(ending=ended.ending, summary=summary, resets_at=ended.resets_at, usage=usage)


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
