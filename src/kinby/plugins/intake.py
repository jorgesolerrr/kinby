"""The intake tools: a factory's intake routine hands the hub each work item it finds, and a line's
intake routine has the hub run the line's intake on its workbench."""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import JsonValue

from kinby.contracts import (
    FACTORY_RUN_INTAKE,
    LINE_INTAKE,
    FactoryRun,
    FactoryRunIntakeCommand,
    LineIntakeCommand,
    RoutineName,
    Scope,
    StepValue,
    ValueName,
)
from kinby.plugins.hub import HUB_SCOPES_VARIABLE, HUB_URL_VARIABLE, call_hub
from kinby.plugins.tools import Tool, ToolContext, tool


def intake_tools() -> tuple[Tool, ...]:
    """The intake tools of the scopes the instance's hub grants it on its hub route:
    factory:intake's and line:intake's."""
    granted = os.environ.get(HUB_SCOPES_VARIABLE, "").split(",")
    if not os.environ.get(HUB_URL_VARIABLE):
        return ()
    return (
        *((hand_to_factory,) if Scope.FACTORY_INTAKE in granted else ()),
        *((run_line_intake,) if Scope.LINE_INTAKE in granted else ()),
    )


@tool(write=True)
async def hand_to_factory(work_item: dict[str, StepValue], context: ToolContext) -> str:
    """Hand a work item to the factory this routine is the intake of, which starts a run.

    A work item that matches a run still open returns that run, and retries one that needs a
    human. The values are the ones the factory's work item declares, such as {"issue": 42}.
    """
    if context.routine is None:
        raise ValueError("Only a factory's intake routine hands it work items.")
    run = await hand_over(context.routine, work_item)
    return f'Factory run {run.run_id} of "{run.factory}" is {run.status.value}.'


async def hand_over(routine: RoutineName, work_item: Mapping[ValueName, StepValue]) -> FactoryRun:
    """Hand a work item to the factory *routine* is the intake of, on this instance's hub route.

    A work item that matches a run still open returns that run, and retries one that needs a
    human.
    """
    command = FactoryRunIntakeCommand(routine=routine, work_item=dict(work_item))
    run = await call_hub(FACTORY_RUN_INTAKE.name, command.model_dump(mode="json"), "the intake")
    return FactoryRun.model_validate(run)


@tool(write=True)
async def run_line_intake(context: ToolContext, signal: dict[str, JsonValue] | None = None) -> None:
    """Run the intake of the line this routine belongs to, on the line's workbench, which hands
    the line each work item it finds. Only a line's intake routine runs it, as its code step.

    *signal* is the delivery that fired the routine, if one did.
    """
    if context.routine is None:
        raise ValueError("Only a line's intake routine runs its intake.")
    command = LineIntakeCommand(routine=context.routine, signal=signal or {})
    await call_hub(LINE_INTAKE.name, command.model_dump(mode="json"), "the line's intake")
