"""The steps a workbench runs for the hub, each in the folder of its run."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from functools import partial
from pathlib import Path
from uuid import UUID

from pydantic import JsonValue

from kinby.contracts import (
    ClientStepRun,
    CodeStepRun,
    CommandStepRun,
    DelegatedRunOutcome,
    FactoryRunTranscript,
    FactoryRunTranscriptCommand,
    HookName,
    RunFolder,
    RunFolderRemoveCommand,
    RunFolderRemoveResult,
    StepEnding,
    StepId,
    StepResult,
    StepRunCommand,
    StepUsage,
)
from kinby.core.coding_clients import run_coding_client
from kinby.core.held_results import HeldResults
from kinby.core.prompt import render_client_message
from kinby.core.run_worktrees import (
    RunWorktreeError,
    cut_run_worktree,
    remove_run_worktree,
    run_worktree,
)
from kinby.core.steps import recorded, run_commands, run_tool, step_failed
from kinby.factories import SHIPPED_FACTORIES
from kinby.instance.layout import SKILLS_DIR, STATE_DIR, TOOLS_DIR, WORKSPACE_DIR
from kinby.plugins.errors import exception_message
from kinby.plugins.hooks import load_hooks
from kinby.plugins.registry import load_tool_file
from kinby.plugins.skills import load_skill_roots
from kinby.plugins.tools import Tool

#: Where the hub mounts its own factory folders in a workbench's container.
FACTORIES_MOUNT = Path("/factories")
#: Where the directory of each run on a workbench whose workspace is no git clone lives.
_RUNS_DIR = "runs"
#: What a run worktree of a branch neither the clone nor origin holds is cut from.
_BASE = "origin/HEAD"
#: Where each agent step attempt's transcript lives, as ``<run>/<step>/<attempt>.jsonl``.
_TRANSCRIPTS_DIR = "transcripts"
#: The most of a transcript one read returns, far under the control socket's frame limit.
_PAGE_BYTES = 1024 * 1024

_logger = logging.getLogger(__name__)


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

    async def transcript(self, command: FactoryRunTranscriptCommand) -> FactoryRunTranscript:
        """One page of the attempt's transcript, from its *cursor*-th line, no larger than its
        size. A line that is no JSON object, such as one a killed client left half written, is
        skipped. One too large for any page reads as a ``cut`` event that gives its size."""
        path = self._transcript(command.run_id, command.step, command.attempt)
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
            lines = text.splitlines()
        except FileNotFoundError:
            lines = []
        page: list[dict[str, JsonValue] | None] = []
        size = 0
        for line in lines[command.cursor :]:
            length = len(line.encode())
            if length > _PAGE_BYTES:
                page.append({"type": "cut", "bytes": length})
                continue
            size += length
            if size > _PAGE_BYTES:
                break
            page.append(_event(line))
        end = command.cursor + len(page)
        return FactoryRunTranscript(
            events=[event for event in page if event is not None],
            cursor=end if end < len(lines) else None,
        )

    async def _run(self, command: StepRunCommand) -> StepResult:
        if command.folder is None:
            return step_failed("The step names no run folder.")
        try:
            folder = await asyncio.to_thread(
                self._folder, command.origin.run_id, command.folder, fresh=command.fresh_folder
            )
        except RunWorktreeError as exc:
            return step_failed(str(exc))
        match command.step:
            case CommandStepRun(run=run, timeout_seconds=timeout):
                # The hub puts each list of repository commands' commands in its place.
                commands = [entry for entry in run if isinstance(entry, str)]
                if len(commands) < len(run):
                    return step_failed("A workbench reads no list of repository commands itself.")
                result = await run_commands(commands, timeout, folder)
            case ClientStepRun() as step:
                result = await self._agent(step, command, folder)
            case CodeStepRun() as step:
                result = await self._call(step, command, folder)
            case _:
                return step_failed(f"A workbench runs no {command.step.kind} step.")
        if command.hook is None:
            return result
        return await self._checked(command.hook, result, command, folder)

    def _folder(self, run_id: UUID, folder: RunFolder, *, fresh: bool) -> Path:
        if folder.branch is None:
            directory = self._directory / _RUNS_DIR / str(run_id)
            if fresh:
                shutil.rmtree(directory, ignore_errors=True)
            directory.mkdir(parents=True, exist_ok=True)
            return directory
        if fresh:
            return cut_run_worktree(
                self._directory, self._workspace, folder.branch, _BASE, str(run_id)
            )
        return run_worktree(self._directory, self._workspace, folder.branch, str(run_id), _BASE)

    async def _agent(
        self, step: ClientStepRun, command: StepRunCommand, folder: Path
    ) -> StepResult:
        """Run the agent step's coding client in the run's folder, offering it the factory's skills
        and the workbench's picks, and keep what it streams as the attempt's transcript.

        What the client said last is only the summary, and its run is what the step spent. A run
        the plan refused ends limited.
        """
        origin = command.origin
        skills, warnings = await asyncio.to_thread(
            load_skill_roots,
            (self._factory(origin.factory) / SKILLS_DIR, self._directory / SKILLS_DIR),
        )
        for warning in warnings:
            _logger.warning("%s: %s", ", ".join(warning.sources), warning.message)
        transcript = self._transcript(origin.run_id, origin.step, command.attempt or 0)
        transcript.parent.mkdir(parents=True, exist_ok=True)
        ran = await run_coding_client(
            step.client,
            render_client_message(
                step.prompt,
                command.work_item,
                command.results,
                tuple(skills.values()),
                remembers=False,
            ),
            folder,
            instance=None,
            state_dir=self._directory / STATE_DIR,
            origin=origin,
            attempt=command.attempt,
            resume=step.resume,
            timeout_seconds=step.timeout_seconds,
            model=step.model,
            effort=step.effort,
            transcript=transcript,
        )
        reported = ran.reported
        usage = StepUsage(delegated_run=reported) if reported is not None else None
        if reported is not None and reported.outcome is DelegatedRunOutcome.LIMITED:
            return StepResult(
                ending=StepEnding.LIMITED,
                summary=ran.summary,
                session=ran.session,
                resets_at=reported.resets_at,
                usage=usage,
            )
        return StepResult(ending=ran.ending, summary=ran.summary, session=ran.session, usage=usage)

    def _transcript(self, run_id: UUID, step: StepId, attempt: int) -> Path:
        return self._directory / _TRANSCRIPTS_DIR / str(run_id) / step / f"{attempt}.jsonl"

    async def _call(self, step: CodeStepRun, command: StepRunCommand, folder: Path) -> StepResult:
        """Call the factory's tool, whose path parameters resolve in the run's folder."""
        factory = command.origin.factory
        try:
            tool = self._tool(factory, step.call)
        except Exception as exc:
            # Tool files are user code. Whatever goes wrong loading them fails the step.
            return step_failed(
                f'The tools of factory "{factory}" cannot load: {exception_message(exc)}'
            )
        if tool is None:
            return step_failed(f'Tool "{step.call}" is not one of factory "{factory}"\'s tools.')
        if tool.context_parameter is not None:
            return step_failed(f'Tool "{step.call}" takes a tool context, which a workbench lacks.')
        return await run_tool(
            tool,
            step,
            command,
            lambda arguments: tool.runnable.ainvoke(tool.resolve_paths(arguments, folder)),
        )

    async def _checked(
        self, name: HookName, result: StepResult, command: StepRunCommand, folder: Path
    ) -> StepResult:
        """The step's result with what its check, a hook of the factory's folder, records reading
        the run's folder. A check the folder does not hold fails a step that ended clean."""
        factory = command.origin.factory
        own = self._factory(factory).resolve()
        hooks, warnings = await asyncio.to_thread(load_hooks, own)
        # The hooks kinby ships are an instance's defaults, which no factory's check names.
        found = next(
            (hook for hook in hooks if hook.name == name and hook.source.is_relative_to(own)),
            None,
        )
        if found is None:
            unloaded = "".join(f"\n{warning.sources[0]}: {warning.message}" for warning in warnings)
            reason = f'Check "{name}" is not one of factory "{factory}"\'s hooks.{unloaded}'
            return step_failed(reason, result)
        return await recorded(found, "Check", result, command, folder)

    def _tool(self, factory: str, name: str) -> Tool | None:
        """The factory's tool by *name*, read from its folder now."""
        return next(
            (
                tool
                for path in sorted((self._factory(factory) / TOOLS_DIR).glob("*.py"))
                for tool in load_tool_file(path)
                if tool.name == name
            ),
            None,
        )

    def _factory(self, name: str) -> Path:
        """The factory's folder. The hub's hides the one kinby ships."""
        folder = self._factories / name
        return folder if folder.is_dir() else self._shipped / name


def _event(line: str) -> dict[str, JsonValue] | None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None
