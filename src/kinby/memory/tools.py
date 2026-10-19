"""Expose the memory facade as core model tools."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date

from kinby.contracts import NodeId
from kinby.core.clock import utc_today
from kinby.memory.facade import (
    Episode,
    Fact,
    Memory,
    MemoryHit,
    RecappedRun,
    RecappedTurn,
    new_node_id,
)
from kinby.plugins.tools import Tool, ToolContext, tool


def memory_tools(
    memory: Memory,
    *,
    today: Callable[[], date] = utc_today,
) -> tuple[Tool, ...]:
    """Build the core tools for one memory facade."""

    @tool(write=False)
    async def memory_search(
        query: str,
        after: date | None = None,
        before: date | None = None,
        *,
        context: ToolContext,
    ) -> str:
        """Search the facts you learned by their descriptions and subjects, and by those of the
        episodes each fact was learned from.

        A search with an inclusive after or before date also returns the episodes in that range,
        and a search no fact matches returns the matching episodes instead.
        """
        hits = memory.recall(query, after=after, before=before)
        if hits:
            await context.report_recall(tuple(hit.node for hit in hits))
        return hits_json(hits)

    @tool(write=False)
    async def memory_open(node: NodeId, context: ToolContext) -> str:
        """Open one memory node by the id returned from memory_search."""
        opened = open_memory(memory, node)
        await context.report_recall((node,))
        return opened

    @tool(write=True)
    def remember(
        description: str,
        subjects: tuple[str, ...],
        body: str,
        context: ToolContext,
    ) -> str:
        """Remember one fact learned in this thread."""
        learned_on = today()
        node = new_node_id(learned_on, description)
        return memory.remember(
            Fact(
                node=node,
                date=learned_on,
                thread=context.thread_id,
                description=description,
                subjects=subjects,
                body=body,
            )
        )

    @tool(write=True)
    def forget(node: NodeId) -> str:
        """Forget one memory node by the id returned from memory_search."""
        memory.forget(node, today())
        return f'Forgot memory node "{node}".'

    return memory_search, memory_open, remember, forget


def hits_json(hits: tuple[MemoryHit, ...]) -> str:
    """The hits of a memory search as JSON, for the model and for a coding session's shell."""
    return json.dumps(
        [
            {
                "node": hit.node,
                "date": hit.date.isoformat(),
                "description": hit.description,
            }
            for hit in hits
        ],
        ensure_ascii=False,
    )


def open_memory(memory: Memory, node: NodeId) -> str:
    """One memory node's body as JSON, for the model and for a coding session's shell."""
    found = memory.open(node)
    opened: dict[str, object] = {
        "node": found.node,
        "body": found.body,
    }
    match found:
        case Episode(recaps=RecappedTurn(turn=turn), tools=tools):
            opened |= {"turn": str(turn), "tools": list(tools)}
        case Episode(recaps=RecappedRun(run=run), tools=tools):
            opened |= {"run": str(run), "tools": list(tools)}
    return json.dumps(opened, ensure_ascii=False)
