"""The line tools: an instance hands work to the lines it owns, and lists them with their
unfinished runs."""

from __future__ import annotations

import json
import os
from uuid import UUID

from kinby.contracts import (
    LINE_OWNED_HAND_IN,
    LINE_OWNED_LIST,
    FactoryRun,
    LineHandInCommand,
    LineOwnedListCommand,
    Scope,
    StepValue,
)
from kinby.plugins.hub import HUB_SCOPES_VARIABLE, HUB_URL_VARIABLE, call_hub
from kinby.plugins.tools import Tool, tool


def line_tools() -> tuple[Tool, ...]:
    """The line tools, in an instance its hub grants line:owner on its hub route."""
    granted = os.environ.get(HUB_SCOPES_VARIABLE, "").split(",")
    if not os.environ.get(HUB_URL_VARIABLE) or Scope.LINE_OWNER not in granted:
        return ()
    return (list_lines, hand_to_line)


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
