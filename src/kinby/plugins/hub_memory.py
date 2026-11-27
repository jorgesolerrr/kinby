"""The memory of the owner of a workbench run's line, which the run's steps search and open,
read-only, through the workbench's hub route (ADR 0134)."""

from __future__ import annotations

import json
import os
from datetime import date

from kinby.contracts import (
    WORKBENCH_MEMORY_OPEN,
    WORKBENCH_MEMORY_SEARCH,
    ContractModel,
    Method,
    NodeId,
    Scope,
    WorkbenchMemoryOpenCommand,
    WorkbenchMemorySearchCommand,
)
from kinby.core.session_recalls import SessionAttempt
from kinby.memory.facade import MemoryHit
from kinby.memory.tools import hits_json
from kinby.plugins.hub import HUB_SCOPES_VARIABLE, HUB_URL_VARIABLE, HubRefused, call_hub
from kinby.plugins.tools import Tool, tool


class OwnerMemoryUnavailable(Exception):
    """The line's owner, or the hub, cannot be reached, so the step goes on without memory."""


def reads_owner_memory() -> bool:
    """Whether the hub grants workbench:memory on this workbench's hub route."""
    granted = os.environ.get(HUB_SCOPES_VARIABLE, "").split(",")
    return bool(os.environ.get(HUB_URL_VARIABLE)) and Scope.WORKBENCH_MEMORY in granted


async def search_owner_memory(
    attempt: SessionAttempt, query: str, after: date | None, before: date | None
) -> str:
    """The hits of the search, as JSON in the shape of the instance's own memory search."""
    command = WorkbenchMemorySearchCommand(
        run_id=attempt.run_id,
        step=attempt.step,
        attempt=attempt.attempt,
        query=query,
        after=after,
        before=before,
    )
    found = await _call(WORKBENCH_MEMORY_SEARCH, command)
    return hits_json(tuple(MemoryHit(hit.node, hit.date, hit.description) for hit in found.hits))


async def open_owner_memory(attempt: SessionAttempt, node: NodeId) -> str:
    """The node's body, as JSON in the shape of the instance's own memory open."""
    command = WorkbenchMemoryOpenCommand(
        run_id=attempt.run_id, step=attempt.step, attempt=attempt.attempt, node=node
    )
    found = await _call(WORKBENCH_MEMORY_OPEN, command)
    opened: dict[str, object] = {"node": found.node, "body": found.body}
    if found.turn is not None:
        opened |= {"turn": str(found.turn), "tools": found.tools}
    elif found.run is not None:
        opened |= {"run": str(found.run), "tools": found.tools}
    return json.dumps(opened, ensure_ascii=False)


def owner_memory_tools(attempt: SessionAttempt) -> tuple[Tool, ...]:
    """The read-only memory tools of a kinby turn at the step *attempt* of a workbench's run."""

    @tool(write=False)
    async def memory_search(
        query: str, after: date | None = None, before: date | None = None
    ) -> str:
        """Search facts for any query word, best match first; after or before adds the episodes."""
        try:
            return await search_owner_memory(attempt, query, after, before)
        except OwnerMemoryUnavailable as exc:
            return f"{exc} Go on without memory."

    @tool(write=False)
    async def memory_open(node: NodeId) -> str:
        """Open one memory node by the id returned from memory_search."""
        try:
            return await open_owner_memory(attempt, node)
        except OwnerMemoryUnavailable as exc:
            return f"{exc} Go on without memory."

    return memory_search, memory_open


async def _call[Command: ContractModel, Result: ContractModel](
    method: Method[Command, Result], command: Command
) -> Result:
    try:
        result = await call_hub(method.name, command.model_dump(mode="json"), "the memory lookup")
    except HubRefused as exc:
        # The hub answers a stopped or unreachable owner as retryable.
        if exc.error.retryable:
            raise OwnerMemoryUnavailable(exc.error.message) from exc
        raise
    except ConnectionError as exc:
        raise OwnerMemoryUnavailable(str(exc)) from exc
    return method.result.model_validate(result)
