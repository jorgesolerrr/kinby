"""The line tools: an instance hands work to the lines it owns, lists them with their unfinished
runs, reads their workbenches, and records those workbenches' repository commands. It reads the
factories its lines are on, and changes a step of one as a write the user approves (ADR 0096)."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import replace
from uuid import UUID

from pydantic import JsonValue, ValidationError

from kinby.contracts import (
    FACTORY_OWNED_CHANGE,
    FACTORY_OWNED_CHANGE_PREVIEW,
    FACTORY_OWNED_GET,
    LINE_OWNED_HAND_IN,
    LINE_OWNED_LIST,
    WORKBENCH_OWNED_COMMAND_RUN,
    WORKBENCH_OWNED_COMMANDS_SET,
    WORKBENCH_OWNED_DIRECTORY_LIST,
    WORKBENCH_OWNED_FILE_READ,
    FactoryChangeCommand,
    FactoryChangeOperation,
    FactoryChangePreview,
    FactoryChangePreviewCommand,
    FactoryGetCommand,
    FactoryResult,
    FactoryRun,
    FactoryStepAddCommand,
    FactoryStepChange,
    FactoryStepOffSetCommand,
    FactoryStepSaveCommand,
    LineHandInCommand,
    LineOwnedListCommand,
    Scope,
    StepValue,
    WorkbenchCommandRunCommand,
    WorkbenchCommandRunResult,
    WorkbenchCommandsSetCommand,
    WorkbenchDirectoryListCommand,
    WorkbenchDirectoryListResult,
    WorkbenchFileReadCommand,
    WorkbenchFileReadResult,
)
from kinby.core.errors import PermissionDenied
from kinby.instance import Instance
from kinby.plugins.hub import HUB_SCOPES_VARIABLE, HUB_URL_VARIABLE, HubRefused, call_hub
from kinby.plugins.tools import PreviewRefused, Tool, ToolContext, tool


def line_tools() -> tuple[Tool, ...]:
    """The line tools, in an instance its hub grants line:owner on its hub route."""
    granted = os.environ.get(HUB_SCOPES_VARIABLE, "").split(",")
    if not os.environ.get(HUB_URL_VARIABLE) or Scope.LINE_OWNER not in granted:
        return ()
    return (
        list_lines,
        hand_to_line,
        read_workbench_file,
        list_workbench_directory,
        run_workbench_command,
        record_workbench_commands,
        read_factory,
        change_factory,
    )


@tool(write=False)
async def list_lines() -> str:
    """List the lines you own, as JSON: each line's factory, its workbench, its model and daily
    budget, and its runs not yet done or cancelled, with each run's status and step.
    """
    listed = await call_hub(
        LINE_OWNED_LIST.name, LineOwnedListCommand().model_dump(mode="json"), "the list"
    )
    return json.dumps(listed, ensure_ascii=False)


@tool(write=True, hub=True)
async def hand_to_line(line_id: UUID, work_item: dict[str, StepValue]) -> str:
    """Hand a work item to one of the lines you own, which starts a run of its factory on its
    workbench.

    A work item that matches an unfinished run of the line returns that run. The values are the
    ones the line's factory's work item declares, such as {"issue": 42}. list_lines names the
    lines.
    """
    command = LineHandInCommand(line_id=line_id, work_item=work_item)
    answer = await call_hub(LINE_OWNED_HAND_IN.name, command.model_dump(mode="json"), "the hand-in")
    run = FactoryRun.model_validate(answer)
    return f'Factory run {run.run_id} of "{run.factory}" is {run.status.value}.'


@tool(write=False)
async def read_workbench_file(workbench_id: UUID, path: str) -> str:
    """Read a UTF-8 text file of the workspace of a workbench one of your lines is on, by its
    path there. list_lines names each line's workbench_id."""
    command = WorkbenchFileReadCommand(workbench_id=workbench_id, path=path)
    answer = await call_hub(
        WORKBENCH_OWNED_FILE_READ.name, command.model_dump(mode="json"), f'the read of "{path}"'
    )
    return WorkbenchFileReadResult.model_validate(answer).text


@tool(write=False)
async def list_workbench_directory(workbench_id: UUID, path: str = ".") -> str:
    """List a directory of the workspace of a workbench one of your lines is on, one entry a
    line, each directory ending in a slash."""
    command = WorkbenchDirectoryListCommand(workbench_id=workbench_id, path=path)
    answer = await call_hub(
        WORKBENCH_OWNED_DIRECTORY_LIST.name,
        command.model_dump(mode="json"),
        f'the listing of "{path}"',
    )
    return "\n".join(WorkbenchDirectoryListResult.model_validate(answer).entries)


@tool(write=False)
async def run_workbench_command(workbench_id: UUID, command: str) -> str:
    """Run a bash command in the workspace of a workbench one of your lines is on, and return its
    output.

    The workbench runs only a command whose programs read, such as ls, cat, grep, find or wc, on
    paths inside its workspace, and refuses any other. git is not one of them.
    """
    run = WorkbenchCommandRunCommand(workbench_id=workbench_id, command=command)
    answer = await call_hub(
        WORKBENCH_OWNED_COMMAND_RUN.name, run.model_dump(mode="json"), "the command"
    )
    return WorkbenchCommandRunResult.model_validate(answer).output


@tool(write=True)
async def record_workbench_commands(workbench_id: UUID, name: str, commands: list[str]) -> str:
    """Set one of the lists of repository commands of a workbench one of your lines is on, such
    as setup or checks, replacing the whole list.

    A command step runs each command in the run's folder without a shell. An empty list is a
    valid one.
    """
    command = WorkbenchCommandsSetCommand(workbench_id=workbench_id, name=name, commands=commands)
    await call_hub(
        WORKBENCH_OWNED_COMMANDS_SET.name,
        command.model_dump(mode="json"),
        "the repository commands",
    )
    return f'Recorded the repository commands "{name}", {len(commands)} commands.'


@tool(write=False)
async def read_factory(name: str) -> str:
    """Read a factory one of your lines is on, as JSON: each file of its folder by its path, with
    factory.yaml among them, and the hash change_factory takes."""
    answer = await call_hub(
        FACTORY_OWNED_GET.name, FactoryGetCommand(name=name).model_dump(mode="json"), "the read"
    )
    return json.dumps(answer, ensure_ascii=False)


@tool(write=True, hub=True)
async def change_factory(
    operation: str, arguments: dict[str, JsonValue], context: ToolContext
) -> str:
    """Change one step of a factory one of your lines is on, once the user approves it. The change
    reaches the runs of every line on the factory, so its approval shows the diff and those lines.

    operation is one of:
    - step_save: arguments are name, the factory's; step, the step's whole entry in factory.yaml,
      whose id names the step it replaces; and prompt and hook, the new text of the step's prompt
      file and of the file that declares its check, each left out to keep the file as it is.
    - step_add: arguments are name; after, the id of the step the new one follows; step, its
      entry; and prompt, the text of its new prompt file, if it names one.
    - step_off_set: arguments are name; step, the step's id; and off, true to turn it off so runs
      pass over it, or false to turn it back on.
    Each also takes hash, as read_factory returned it. A factory changed since that read refuses
    the change, and so does the factory check.
    """
    if context.approval_id is None:
        raise PermissionError("The hub changes a factory only with the user's approval.")
    command = FactoryChangeCommand(
        approval_id=context.approval_id,
        operation=FactoryChangeOperation(operation),
        arguments=arguments,
    )
    answer = await call_hub(
        FACTORY_OWNED_CHANGE.name, command.model_dump(mode="json"), "the change"
    )
    changed = FactoryResult.model_validate(answer)
    return f'Factory "{changed.name}" is now version {changed.version}.'


async def _change_preview(arguments: Mapping[str, JsonValue], instance: Instance) -> str:
    """The diff the change makes and every line on the factory, as the hub shows them."""
    command = {"operation": arguments.get("operation"), "arguments": arguments.get("arguments")}
    try:
        answer = await call_hub(FACTORY_OWNED_CHANGE_PREVIEW.name, command, "the preview")
    except HubRefused as exc:
        raise PreviewRefused(str(exc)) from exc
    except ConnectionError as exc:
        # The approval must show the diff and every line, which an unanswered preview lacks.
        raise PreviewRefused(
            f"The hub could not answer the preview, so the change did not run. Try again. {exc}"
        ) from exc
    previewed = FactoryChangePreview.model_validate(answer)
    lines = "\n".join(f"- {line}" for line in previewed.lines)
    return (
        f"This change reaches the runs of every line on the factory:\n{lines}\n\n{previewed.diff}"
    )


def _refusal(call: Mapping[str, JsonValue]) -> str | None:
    try:
        requested_change(call)
    except PermissionDenied as exc:
        return str(exc)
    return None


change_factory = replace(change_factory, preview=_change_preview, check=_refusal)

#: The step write each operation of change_factory asks for.
_CHANGES: Mapping[FactoryChangeOperation, type[FactoryStepChange]] = {
    FactoryChangeOperation.STEP_SAVE: FactoryStepSaveCommand,
    FactoryChangeOperation.STEP_ADD: FactoryStepAddCommand,
    FactoryChangeOperation.STEP_OFF_SET: FactoryStepOffSetCommand,
}


def requested_change(call: Mapping[str, JsonValue]) -> FactoryStepChange:
    """The step write one change_factory call asks for, from its operation and its arguments. A
    refusal names each wrong field."""
    try:
        command = FactoryChangePreviewCommand.model_validate(call)
        return _CHANGES[command.operation].model_validate(command.arguments)
    except ValidationError as exc:
        named = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise PermissionDenied(f"change_factory cannot run with these arguments: {named}") from exc
