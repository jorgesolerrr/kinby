"""Bring memory to a turn or a client step without the agent choosing to search."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from langchain_core.embeddings import Embeddings

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
            return _pre_searched(
                [fact for hit in hits if isinstance(fact := graph.open(hit.node), Fact)]
            )
        case MemoryDelivery.NONE:
            return None


async def embedded_pre_search(
    graph: GraphStore, task: str, embeddings: Embeddings
) -> DeliveredMemory | None:
    """A pre-search that finds the facts whose *embeddings* lie closest to *task*'s.

    Only facts are embedded, and their vectors are kept for this call alone.
    """
    facts = [node for node in graph.nodes() if isinstance(node, Fact)]
    if not facts:
        return None
    vectors = await embeddings.aembed_documents(
        [f"{fact.description}\n\n{fact.body}" for fact in facts]
    )
    query = await embeddings.aembed_query(task)
    ranked = sorted(
        zip(facts, vectors, strict=True), key=lambda pair: _cosine(query, pair[1]), reverse=True
    )
    return _pre_searched([fact for fact, _ in ranked[:_PRE_SEARCH_FACTS]])


def _pre_searched(facts: Sequence[Fact]) -> DeliveredMemory | None:
    if not facts:
        return None
    found = "\n\n".join(
        f"## {fact.description} ({fact.node})\n{fact.body.strip()}" for fact in facts
    )
    return DeliveredMemory(f"{_PRE_SEARCH_HEADER}\n\n{found}", tuple(fact.node for fact in facts))


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    norms = math.hypot(*left) * math.hypot(*right)
    return math.sumprod(left, right) / norms if norms else 0


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
