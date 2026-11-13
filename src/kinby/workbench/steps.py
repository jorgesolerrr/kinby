"""The steps a workbench runs for the hub, each in the folder of its run."""

from __future__ import annotations

import asyncio
import shutil
from functools import partial
from pathlib import Path
from uuid import UUID

from kinby.contracts import (
    CodeStepRun,
    CommandStepRun,
    RunFolder,
    RunFolderRemoveCommand,
    RunFolderRemoveResult,
    StepEnding,
    StepResult,
    StepRunCommand,
)
from kinby.core.held_results import HeldResults
from kinby.core.run_worktrees import RunWorktreeError, remove_run_worktree, run_worktree
from kinby.core.steps import run_commands, run_tool
from kinby.factories import SHIPPED_FACTORIES
from kinby.instance.layout import TOOLS_DIR, WORKSPACE_DIR
from kinby.plugins.errors import exception_message
from kinby.plugins.registry import load_tool_file
from kinby.plugins.tools import Tool

#: Where the hub mounts its own factory folders in a workbench's container.
FACTORIES_MOUNT = Path("/factories")
#: Where the directory of each run on a workbench whose workspace is no git clone lives.
_RUNS_DIR = "runs"
#: What a run worktree of a branch neither the clone nor origin holds is cut from.
_BASE = "origin/HEAD"


class WorkbenchSteps:
    """Run the hub's steps on the workbench in *directory*, with the tools of the hub's factory
    folders in *factories*, over the ones kinby ships in *shipped*.

    Nothing copies a factory here, so a step runs its tool as the factory's folder holds it then.
    """

    def __init__(
        self, directory: Path, factories: Path = FACTORIES_MOUNT, shipped: Path = SHIPPED_FACTORIES
    ) -> None:
        self._directory = directory
        self._workspace = directory / WORKSPACE_DIR
        self._factories = factories
        self._shipped = shipped
        self._held = HeldResults(directory / "step-results")

    async def run(self, command: StepRunCommand) -> StepResult:
        """Run the step once per attempt in its run's folder, cut first when the workbench lacks
        it. A folder that cannot be had fails the step before it starts."""
        return await self._held.answer(command, partial(self._run, command))

    async def remove_folder(self, command: RunFolderRemoveCommand) -> RunFolderRemoveResult:
        """Remove the run's folder, and a run worktree's branch, whichever exist."""
        branch = command.folder.branch
        if branch is None:
            directory = self._directory / _RUNS_DIR / str(command.run_id)
            await asyncio.to_thread(shutil.rmtree, directory, ignore_errors=True)
        else:
            await asyncio.to_thread(
                remove_run_worktree, self._directory, self._workspace, branch, str(command.run_id)
            )
        return RunFolderRemoveResult()

    async def _run(self, command: StepRunCommand) -> StepResult:
        if command.folder is None:
            return _failed("The step names no run folder.")
        try:
            folder = await asyncio.to_thread(self._folder, command.origin.run_id, command.folder)
        except RunWorktreeError as exc:
            return _failed(str(exc))
        match command.step:
            case CommandStepRun(run=run, timeout_seconds=timeout):
                # The hub puts each list of repository commands' commands in its place.
                commands = [entry for entry in run if isinstance(entry, str)]
                if len(commands) < len(run):
                    return _failed("A workbench reads no list of repository commands itself.")
                return await run_commands(commands, timeout, folder)
            case CodeStepRun() as step:
                return await self._call(step, command, folder)
            case _:
                return _failed(f"A workbench runs no {command.step.kind} step.")

    def _folder(self, run_id: UUID, folder: RunFolder) -> Path:
        if folder.branch is None:
            directory = self._directory / _RUNS_DIR / str(run_id)
            directory.mkdir(parents=True, exist_ok=True)
            return directory
        return run_worktree(self._directory, self._workspace, folder.branch, str(run_id), _BASE)

    async def _call(self, step: CodeStepRun, command: StepRunCommand, folder: Path) -> StepResult:
        """Call the factory's tool, whose path parameters resolve in the run's folder."""
        factory = command.origin.factory
        try:
            tool = self._tool(factory, step.call)
        except Exception as exc:
            # Tool files are user code. Whatever goes wrong loading them fails the step.
            return _failed(
                f'The tools of factory "{factory}" cannot load: {exception_message(exc)}'
            )
        if tool is None:
            return _failed(f'Tool "{step.call}" is not one of factory "{factory}"\'s tools.')
        if tool.context_parameter is not None:
            return _failed(f'Tool "{step.call}" takes a tool context, which a workbench lacks.')
        return await run_tool(
            tool,
            step,
            command,
            lambda arguments: tool.runnable.ainvoke(tool.resolve_paths(arguments, folder)),
        )

    def _tool(self, factory: str, name: str) -> Tool | None:
        """The factory's tool by *name*, read from its folder now. The hub's folder hides the one
        kinby ships."""
        folder = self._factories / factory
        if not folder.is_dir():
            folder = self._shipped / factory
        return next(
            (
                tool
                for path in sorted((folder / TOOLS_DIR).glob("*.py"))
                for tool in load_tool_file(path)
                if tool.name == name
            ),
            None,
        )


def _failed(reason: str) -> StepResult:
    return StepResult(ending=StepEnding.FAILED, summary=reason)
