"""Expose the memory facade as core model tools."""

from __future__ import annotations

import json
from datetime import date

from kinby.memory.facade import Episode, Memory, MemoryNode, NodeId
from kinby.plugins.tools import Tool, tool


def memory_tools(memory: Memory) -> tuple[Tool, Tool]:
    """Build the read-only core tools for one memory facade."""

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

    return memory_search, memory_open


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
