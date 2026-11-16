"""What a workbench's steps share: running commands, calling a tool, what a check records, and
the bound on a step's result."""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path

from pydantic import JsonValue

from kinby.contracts import (
    CodeStepRun,
    Event,
    MessageDelta,
    StepEnding,
    StepResult,
    StepRunCommand,
    ToolCall,
)
from kinby.plugins.errors import exception_message
from kinby.plugins.hooks import Hook, HookResult, StepEnd
from kinby.plugins.tools import Tool

#: How much of a failed command's output its step's summary keeps, from the end.
_OUTPUT_TAIL = 2_000
#: The longest summary a step result keeps.
_SUMMARY_CHARACTERS = 16_000
#: The largest step result a workbench sends, serialized. Far under the hub's frame limit.
_RESULT_BYTES = 1024 * 1024


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


async def recorded(
    found: Hook, result: StepResult, command: StepRunCommand, folder: Path
) -> StepResult:
    """The step's result with what its check *found* records reading the run's *folder*.

    A check that records nothing leaves the result as the step ended. The values it records join
    the step's, and an outcome it names replaces the step's. One that fails fails a step that
    ended clean and keeps any other ending.
    """
    end = StepEnd(
        workspace=folder,
        ending=result.ending,
        work_item=command.work_item,
        results=command.results,
    )
    try:
        record = await found.record(end)
    except Exception as exc:
        # Checks are user code. Whatever goes wrong fails the step, and says why.
        return step_failed(f'Check "{found.name}" failed: {exception_message(exc)}', result)
    if record is None:
        return result
    return result.model_copy(
        update={
            "outcome": result.outcome if record.outcome is None else record.outcome,
            "values": result.values | dict(record.values),
        }
    )


def bounded(result: StepResult) -> StepResult:
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


def step_failed(reason: str, ended: StepResult | None = None) -> StepResult:
    """A failed result that says why, after the summary of the step as it *ended*.

    A step that ended otherwise than clean keeps its ending, so a timeout is never tried again,
    and a limited one keeps when its plan window resets. What the step spent, and the views it
    wrote, stay.
    """
    summary = reason if ended is None or not ended.summary else f"{ended.summary}\n{reason}"
    usage = ended.usage if ended is not None else None
    views = ended.views if ended is not None else []
    if ended is None or ended.ending is StepEnding.CLEAN:
        return StepResult(ending=StepEnding.FAILED, summary=summary, usage=usage, views=views)
    return StepResult(
        ending=ended.ending, summary=summary, resets_at=ended.resets_at, usage=usage, views=views
    )


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
