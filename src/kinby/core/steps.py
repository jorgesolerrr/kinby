"""Run one factory step in this instance's workspace."""

from __future__ import annotations

import asyncio
import shlex
from pathlib import Path

from kinby.contracts import CommandStepRun, StepEnding, StepResult

#: How much of a failed command's output its step's summary keeps, from the end.
_OUTPUT_TAIL = 2_000


async def run_command_step(step: CommandStepRun, workspace: Path) -> StepResult:
    """Run each command in *workspace* without a shell. The first that exits non-zero fails."""
    for command in step.run:
        try:
            process = await asyncio.create_subprocess_exec(
                *shlex.split(command),
                cwd=workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except (OSError, ValueError) as exc:
            return StepResult(
                ending=StepEnding.FAILED, summary=f'"{command}" could not start: {exc}'
            )
        output, _ = await process.communicate()
        if process.returncode != 0:
            tail = output.decode(errors="replace").strip()[-_OUTPUT_TAIL:]
            return StepResult(
                ending=StepEnding.FAILED,
                summary=f'"{command}" exited with code {process.returncode}.\n{tail}'.strip(),
            )
    return StepResult(ending=StepEnding.CLEAN, summary="Every command exited with code 0.")
