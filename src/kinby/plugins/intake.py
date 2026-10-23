"""The intake tool: a factory's intake routine hands the hub each work item it finds."""

from __future__ import annotations

import os
from collections.abc import Mapping

from kinby.contracts import (
    FACTORY_RUN_INTAKE,
    FactoryRun,
    FactoryRunIntakeCommand,
    RoutineName,
    Scope,
    StepValue,
    ValueName,
)
from kinby.plugins.hub import HUB_SCOPES_VARIABLE, HUB_URL_VARIABLE, call_hub
from kinby.plugins.tools import Tool, ToolContext, tool


def intake_tools() -> tuple[Tool, ...]:
    """The intake tool, in an instance its hub grants factory:intake on its hub route."""
    granted = os.environ.get(HUB_SCOPES_VARIABLE, "").split(",")
    if not os.environ.get(HUB_URL_VARIABLE) or Scope.FACTORY_INTAKE not in granted:
        return ()
    return (hand_to_factory,)


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
