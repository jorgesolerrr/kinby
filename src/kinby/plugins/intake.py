"""The intake tool: a factory's intake routine hands the hub each work item it finds."""

from __future__ import annotations

import os
from collections.abc import Mapping

import aiohttp

from kinby.contracts import (
    FACTORY_RUN_INTAKE,
    CallFrame,
    ErrorFrame,
    FactoryRun,
    FactoryRunIntakeCommand,
    FrameId,
    ResultFrame,
    RoutineName,
    StepValue,
    ValueName,
    parse_server_frame,
)
from kinby.plugins.tools import Tool, ToolContext, tool

#: Where the hub takes this instance's work items. A hub sets it only in a factory's instances.
INTAKE_URL_VARIABLE = "KINBY_INTAKE_URL"


def intake_tools() -> tuple[Tool, ...]:
    """The intake tool, in an instance its hub gave an intake route."""
    return (hand_to_factory,) if os.environ.get(INTAKE_URL_VARIABLE) else ()


@tool(write=True)
async def hand_to_factory(work_item: dict[str, StepValue], context: ToolContext) -> str:
    """Hand a work item to the factory this routine is the intake of, which starts a run.

    A work item that matches a run still open returns that run. The values are the ones the
    factory's work item declares, such as {"issue": 42}.
    """
    if context.routine is None:
        raise ValueError("Only a factory's intake routine hands it work items.")
    run = await hand_over(context.routine, work_item)
    return f'Factory run {run.run_id} of "{run.factory}" is {run.status.value}.'


async def hand_over(routine: RoutineName, work_item: Mapping[ValueName, StepValue]) -> FactoryRun:
    """Hand a work item to the factory *routine* is the intake of, on this instance's intake route.

    A work item that matches a run still open returns that run.
    """
    # kinby.core imports the plugins, so the server's names load on first use.
    from kinby.core.contract_server import CONTROL_TOKEN_VARIABLE

    call = CallFrame(
        id=FrameId("1"),
        method=FACTORY_RUN_INTAKE.name,
        params=FactoryRunIntakeCommand(routine=routine, work_item=dict(work_item)).model_dump(
            mode="json"
        ),
    )
    headers = {"Authorization": f"Bearer {os.environ.get(CONTROL_TOKEN_VARIABLE, '')}"}
    try:
        async with (
            aiohttp.ClientSession(headers=headers) as session,
            session.ws_connect(os.environ[INTAKE_URL_VARIABLE]) as socket,
        ):
            await socket.send_str(call.model_dump_json())
            answer = await socket.receive_str()
    except aiohttp.ClientError as exc:
        raise ConnectionError(f"The hub refused the intake: {exc}") from exc
    match parse_server_frame(answer):
        case ResultFrame(result=result):
            return FactoryRun.model_validate(result)
        case ErrorFrame(error=error):
            raise ValueError(f"The hub refused the intake: {error.message}")
        case other:
            raise ConnectionError(f"The hub answered the intake with {other!r}.")
