"""Expose the memory facade as core model tools."""

from __future__ import annotations

import json
import re
from datetime import date
from uuid import uuid4

from kinby.memory.facade import Episode, Fact, Memory, MemoryNode, NodeId
from kinby.plugins.tools import Tool, ToolContext, tool


def memory_tools(memory: Memory) -> tuple[Tool, ...]:
    """Build the core tools for one memory facade."""

    @tool(write=False)
    def memory_search(
        query: str,
        after: date | None = None,
        before: date | None = None,
    ) -> str:
        """Search memory descriptions and subjects within optional inclusive dates."""
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

    @tool(write=False)
    def memory_open(node: NodeId) -> str:
        """Open one memory node by the id returned from memory_search."""
        return json.dumps(_opened(memory.open(node)), ensure_ascii=False)

    @tool(write=True)
    def remember(
        description: str,
        subjects: tuple[str, ...],
        body: str,
        context: ToolContext,
    ) -> str:
        """Remember one fact learned in this thread."""
        learned_on = date.today()
        node = _fact_node(learned_on, description)
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


def _fact_node(learned_on: date, description: str) -> NodeId:
    slug = re.sub(r"[^a-z0-9]+", "-", description.casefold()).strip("-")
    readable = (slug or "fact")[:64].rstrip("-")
    return NodeId(f"{learned_on.isoformat()}-{readable}-{uuid4().hex[:8]}")


def _opened(memory: MemoryNode) -> dict[str, object]:
    opened: dict[str, object] = {
        "node": memory.node,
        "date": memory.date.isoformat(),
        "thread": str(memory.thread),
        "description": memory.description,
        "subjects": list(memory.subjects),
        "body": memory.body,
    }
    if isinstance(memory, Episode):
        opened["tools"] = list(memory.tools)
    return opened
