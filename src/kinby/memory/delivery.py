"""Bring memory to a turn or a client step without the agent choosing to search."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from kinby.contracts import NodeId
from kinby.memory.facade import Fact
from kinby.memory.graph import GraphStore

#: About 1k tokens, at four characters a token.
_INDEX_CHARACTERS = 4_000
_INDEX_HEADER = (
    "# Memory index\nThe facts in your memory, newest first. Open one before you act on it."
)
_PRE_SEARCH_FACTS = 3
_PRE_SEARCH_HEADER = "# From your memory\nA search of your memory for this work found these facts."


class MemoryDelivery(StrEnum):
    """How memory reaches a turn or a client step before the agent searches it."""

    NONE = "none"
    #: Every live fact's id and description, newest first, as many as fit about 1k tokens.
    INDEX = "index"
    #: The bodies of the facts a memory search with the task's text finds first.
    PRE_SEARCH = "pre-search"


@dataclass(frozen=True)
class DeliveredMemory:
    """The memory put in front of the agent, and the facts it recalls by reading it."""

    text: str
    #: Empty for the index, which shows no body: the agent recalls a fact by opening it.
    recalled: tuple[NodeId, ...] = ()


def deliver_memory(
    delivery: MemoryDelivery, graph: GraphStore, task: str
) -> DeliveredMemory | None:
    """The memory *delivery* brings to *task*. None when it brings nothing."""
    match delivery:
        case MemoryDelivery.INDEX:
            index = memory_index(graph)
            return None if index is None else DeliveredMemory(index)
        case MemoryDelivery.PRE_SEARCH:
            hits = graph.recall(task)[:_PRE_SEARCH_FACTS]
            facts = [fact for hit in hits if isinstance(fact := graph.open(hit.node), Fact)]
            if not facts:
                return None
            found = "\n\n".join(
                f"## {fact.description} ({fact.node})\n{fact.body.strip()}" for fact in facts
            )
            return DeliveredMemory(
                f"{_PRE_SEARCH_HEADER}\n\n{found}", tuple(fact.node for fact in facts)
            )
        case MemoryDelivery.NONE:
            return None


def memory_index(graph: GraphStore) -> str | None:
    """The index of the newest live facts that fit its cap. None when the graph holds no fact."""
    lines = [_INDEX_HEADER]
    size = len(_INDEX_HEADER)
    for fact in (node for node in graph.nodes() if isinstance(node, Fact)):
        line = f"- {fact.node}: {fact.description}"
        size += 1 + len(line)
        if size > _INDEX_CHARACTERS:
            break
        lines.append(line)
    return "\n".join(lines) if len(lines) > 1 else None
