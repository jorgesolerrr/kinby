"""The line tools: an instance hands work to the lines it owns, lists them with their unfinished
runs, reads their workbenches, and records those workbenches' repository commands."""

from __future__ import annotations

import json
import os
from uuid import UUID

from kinby.contracts import (
    LINE_OWNED_HAND_IN,
    LINE_OWNED_LIST,
    WORKBENCH_OWNED_COMMAND_RUN,
    WORKBENCH_OWNED_COMMANDS_SET,
    WORKBENCH_OWNED_DIRECTORY_LIST,
    WORKBENCH_OWNED_FILE_READ,
    FactoryRun,
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
from kinby.plugins.hub import HUB_SCOPES_VARIABLE, HUB_URL_VARIABLE, call_hub
from kinby.plugins.tools import Tool, tool


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


@tool(write=True)
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
