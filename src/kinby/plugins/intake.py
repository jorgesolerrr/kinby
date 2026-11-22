"""The intake tool: a line's intake routine has the hub run the line's intake on its workbench."""

from __future__ import annotations

import asyncio
import os

from pydantic import JsonValue

from kinby.contracts import LINE_INTAKE, LineIntakeCommand, Scope
from kinby.plugins.hub import HUB_SCOPES_VARIABLE, HUB_URL_VARIABLE, call_hub
from kinby.plugins.tools import Tool, ToolContext, tool

#: How long the intake waits on the hub. Longer than the hub waits on the workbench
#: (``kinby.hub.control.INTAKE_SECONDS``), so a hung workbench fails with the hub's answer.
HUB_INTAKE_SECONDS = 6 * 60


def intake_tools() -> tuple[Tool, ...]:
    """The intake tool, when the instance's hub grants it line:intake on its hub route."""
    granted = os.environ.get(HUB_SCOPES_VARIABLE, "").split(",")
    if not os.environ.get(HUB_URL_VARIABLE) or Scope.LINE_INTAKE not in granted:
        return ()
    return (run_line_intake,)


@tool(write=True)
async def run_line_intake(context: ToolContext, signal: dict[str, JsonValue] | None = None) -> None:
    """Run the intake of the line this routine belongs to, on the line's workbench, which hands
    the line each work item it finds. Only a line's intake routine runs it, as its code step.

    *signal* is the delivery that fired the routine, if one did.
    """
    if context.routine is None:
        raise ValueError("Only a line's intake routine runs its intake.")
    command = LineIntakeCommand(routine=context.routine, signal=signal or {})
    try:
        async with asyncio.timeout(HUB_INTAKE_SECONDS):
            await call_hub(LINE_INTAKE.name, command.model_dump(mode="json"), "the line's intake")
    except TimeoutError as exc:
        raise TimeoutError(
            f"The hub did not answer the line's intake within {HUB_INTAKE_SECONDS} seconds."
        ) from exc
