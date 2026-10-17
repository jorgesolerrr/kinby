"""Expose the memory facade as core model tools."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date

from kinby.contracts import NodeId
from kinby.core.clock import utc_today
from kinby.memory.facade import Episode, Fact, Memory, new_node_id
from kinby.plugins.tools import Tool, ToolContext, tool


def memory_tools(
    memory: Memory,
    *,
    today: Callable[[], date] = utc_today,
) -> tuple[Tool, ...]:
    """Build the core tools for one memory facade."""

    @tool(write=False)
    def memory_search(
        query: str,
        after: date | None = None,
        before: date | None = None,
    ) -> str:
        """Search the facts you learned by their descriptions and subjects.

        A search with an inclusive after or before date also returns the episodes in that range,
        and a search no fact matches returns the matching episodes instead.
        """
        return search_memory(memory, query, after=after, before=before)

    @tool(write=False)
    def memory_open(node: NodeId) -> str:
        """Open one memory node by the id returned from memory_search."""
        return open_memory(memory, node)

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
        memory.forget(node)
        return f'Forgot memory node "{node}".'

    return memory_search, memory_open, remember, forget


def search_memory(
    memory: Memory,
    query: str,
    *,
    after: date | None = None,
    before: date | None = None,
) -> str:
    """The hits of a memory search as JSON, for the model and for a coding session's shell."""
    return json.dumps(
        [
            {
                "node": hit.node,
                "date": hit.date.isoformat(),
                "description": hit.description,
            }
            for hit in memory.recall(query, after=after, before=before)
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
    if isinstance(found, Episode):
        opened["turn"] = str(found.turn)
        opened["tools"] = list(found.tools)
    return json.dumps(opened, ensure_ascii=False)
