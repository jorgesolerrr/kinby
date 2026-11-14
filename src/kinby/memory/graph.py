"""Read knowledge graph nodes from an instance directory."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from datetime import date
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError
from pydantic.dataclasses import dataclass

from kinby.contracts import LiveFact, NodeId
from kinby.frontmatter import (
    FrontmatterError,
    parse_frontmatter,
    render_frontmatter_value,
)
from kinby.instance.layout import GRAPH_DIR, MEMORY_DIR
from kinby.memory.facade import (
    Episode,
    Fact,
    MemoryHit,
    MemoryNode,
    RecappedRun,
    RecappedTurn,
)

_logger = logging.getLogger(__name__)

#: How many nodes recall returns, best match first. A model reads them all.
_RECALL_LIMIT = 20


class MemoryNodeError(ValueError):
    """A graph node has missing or invalid frontmatter."""


class InvalidNodeId(ValueError):
    """A node id that cannot name a file in the graph, such as one with a path in it."""


class NodeNotFound(LookupError):
    """No live node has this id: none was written, or it was forgotten."""


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class _NodeFrontmatter:
    date: date
    description: Annotated[str, Field(min_length=1)]
    subjects: tuple[str, ...]


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class _FactFrontmatter(_NodeFrontmatter):
    #: Absent on a fact the user added.
    thread: UUID | None = None
    #: Written only on a fact the user added.
    source: Literal["user"] | None = None
    #: Written only on a fact dreaming learned from episodes.
    sources: tuple[NodeId, ...] = ()
    workbench: str | None = None
    tombstone: bool = False
    #: Absent on a tombstone written before a forget recorded its day.
    forgotten: date | None = None


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class _TurnEpisodeFrontmatter(_NodeFrontmatter):
    thread: UUID
    turn: UUID
    tools: tuple[str, ...]
    workbench: str | None = None
    tombstone: bool = False
    forgotten: date | None = None


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class _RunEpisodeFrontmatter(_NodeFrontmatter):
    run: UUID
    generation: int
    tools: tuple[str, ...]
    workbench: str | None = None
    tombstone: bool = False
    forgotten: date | None = None


type _Frontmatter = _FactFrontmatter | _TurnEpisodeFrontmatter | _RunEpisodeFrontmatter
_FRONTMATTER = TypeAdapter(_Frontmatter)


@dataclass(frozen=True)
class ForgottenFact:
    """A fact a forget tombstoned, with the day of the forget when the tombstone records it."""

    fact: Fact
    forgotten_on: date | None


def _searchable(memory: MemoryNode) -> str:
    return " ".join((memory.description, *memory.subjects)).casefold()


class GraphStore:
    """The markdown knowledge graph feed for one instance."""

    def __init__(self, instance_path: Path) -> None:
        self._path = instance_path / MEMORY_DIR / GRAPH_DIR

    def recall(
        self,
        query: str,
        *,
        after: date | None = None,
        before: date | None = None,
        workbench: str | None = None,
    ) -> tuple[MemoryHit, ...]:
        """Find graph nodes holding any term of *query* within inclusive date bounds.

        Nodes holding more distinct terms come first, then the newest, and a query with no terms
        matches every node. Without a date bound only facts match, and episodes only when no fact
        does (ADR 0074). Recall for a run on *workbench* finds only the nodes marked with it and
        the unmarked; without one, nodes with any mark.
        """
        terms = set(query.casefold().split())
        scored = self._matched_terms(terms, after=after, before=before)
        matches = [
            memory
            for memory, matched in sorted(scored, key=lambda pair: pair[1], reverse=True)
            if (matched or not terms)
            and (workbench is None or memory.workbench in (None, workbench))
        ]
        if after is None and before is None:
            matches = [memory for memory in matches if isinstance(memory, Fact)] or matches
        return tuple(
            MemoryHit(memory.node, memory.date, memory.description)
            for memory in matches[:_RECALL_LIMIT]
        )

    def nodes(
        self,
        query: str = "",
        *,
        after: date | None = None,
        before: date | None = None,
    ) -> list[MemoryNode]:
        """Every live node matching *query* within inclusive date bounds, newest first.

        Each term of *query* must be a case-insensitive substring of the description or a subject,
        or of a fact's source episodes'.
        """
        terms = set(query.casefold().split())
        return [
            memory
            for memory, matched in self._matched_terms(terms, after=after, before=before)
            if matched == len(terms)
        ]

    def _matched_terms(
        self,
        terms: set[str],
        *,
        after: date | None,
        before: date | None,
    ) -> list[tuple[MemoryNode, int]]:
        """Each live node within inclusive date bounds, newest first, with how many terms it holds.

        A fact also holds its source episodes' terms, since it takes their place in recall
        (ADR 0100).
        """
        live = [memory for memory, frontmatter in self._read_all() if not frontmatter.tombstone]
        text = {memory.node: _searchable(memory) for memory in live}
        scored: list[tuple[MemoryNode, int]] = []
        for memory in live:
            if after is not None and memory.date < after:
                continue
            if before is not None and memory.date > before:
                continue
            sources = memory.sources if isinstance(memory, Fact) else ()
            searchable = " ".join((text[memory.node], *(text.get(node, "") for node in sources)))
            scored.append((memory, sum(term in searchable for term in terms)))
        return sorted(scored, key=lambda pair: (pair[0].date, pair[0].node), reverse=True)

    def forgotten_facts(self) -> list[ForgottenFact]:
        """Every fact a forget left a tombstone on, which dreaming must not write back."""
        return [
            ForgottenFact(memory, frontmatter.forgotten)
            for memory, frontmatter in self._read_all()
            if frontmatter.tombstone and isinstance(memory, Fact)
        ]

    def fact_ids(self) -> frozenset[NodeId]:
        """Every fact's id, a forgotten one's too, since it was a fact when it was recalled."""
        return frozenset(memory.node for memory, _ in self._read_all() if isinstance(memory, Fact))

    def live_facts(self) -> list[LiveFact]:
        """Every fact no tombstone hides, newest first."""
        return [
            LiveFact(node=memory.node, date=memory.date, description=memory.description)
            for memory in self.nodes()
            if isinstance(memory, Fact)
        ]

    def _read_all(self) -> Iterator[tuple[MemoryNode, _Frontmatter]]:
        """Each readable node with its frontmatter, skipping invalid files."""
        if not self._path.is_dir():
            return
        for path in self._path.glob("*.md"):
            try:
                yield _parse_node(path)
            except MemoryNodeError:
                _logger.warning('Skipped graph node "%s": it has invalid frontmatter.', path)

    def open(self, node: NodeId) -> MemoryNode:
        path = self._node_path(node)
        if not path.is_file():
            raise NodeNotFound(f'Graph node "{node}" was not found.')
        memory = _read_node(path)
        if memory is None:
            raise NodeNotFound(f'Graph node "{node}" was forgotten.')
        return memory

    def remember(self, memory: MemoryNode) -> NodeId:
        self._path.mkdir(parents=True, exist_ok=True)
        _write_node(self._node_path(memory.node), _render_node(memory))
        return memory.node

    def forget(self, node: NodeId, forgotten_on: date) -> None:
        path = self._node_path(node)
        if _read_node(path) is None:
            return
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        closing = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
        lines[closing:closing] = ["tombstone: true\n", f"forgotten: {forgotten_on.isoformat()}\n"]
        _write_node(path, "".join(lines))

    def prune(self, node: NodeId) -> None:
        """Delete *node*'s file, leaving no tombstone (ADR 0074)."""
        self._node_path(node).unlink(missing_ok=True)

    def marked(self, workbench: str) -> dict[NodeId, str]:
        """The file of each node marked with *workbench*, by node. A forgotten fact's comes too,
        so a graph that takes it never learns the fact back."""
        return {
            memory.node: self._node_path(memory.node).read_text(encoding="utf-8")
            for memory, _ in self._read_all()
            if memory.workbench == workbench
        }

    def take(self, files: Mapping[NodeId, str]) -> list[NodeId]:
        """Write each node file this graph holds no node of yet, and answer which it wrote."""
        self._path.mkdir(parents=True, exist_ok=True)
        written = [node for node in files if not self._node_path(node).exists()]
        for node in written:
            _write_node(self._node_path(node), files[node])
        return written

    def _node_path(self, node: NodeId) -> Path:
        relative = Path(f"{node}.md")
        if relative.parent != Path():
            raise InvalidNodeId(f'Invalid graph node id "{node}".')
        return self._path / relative


def _write_node(path: Path, document: str) -> None:
    """Replace a node file, staging beside it so no reader sees half a file."""
    staging = path.with_name(f".{path.name}.{uuid4().hex}.staging")
    try:
        staging.write_text(document, encoding="utf-8", newline="\n")
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


def _render_node(memory: MemoryNode) -> str:
    description = render_frontmatter_value(memory.description)
    subjects = render_frontmatter_value(memory.subjects)
    tools = (
        f"tools: {render_frontmatter_value(memory.tools)}\n" if isinstance(memory, Episode) else ""
    )
    source = "source: user\n" if isinstance(memory, Fact) and memory.added_by_user else ""
    sources = (
        f"sources: {render_frontmatter_value(memory.sources)}\n"
        if isinstance(memory, Fact) and memory.sources
        else ""
    )
    workbench = (
        ""
        if memory.workbench is None
        else f"workbench: {render_frontmatter_value(memory.workbench)}\n"
    )
    body = memory.body.rstrip("\r\n")
    return (
        "---\n"
        f"date: {memory.date.isoformat()}\n"
        f"{_origin(memory)}"
        f"{workbench}"
        f"description: {description}\n"
        f"subjects: {subjects}\n"
        f"{tools}"
        f"{source}"
        f"{sources}"
        "---\n"
        f"{body}\n"
    )


def _origin(memory: MemoryNode) -> str:
    """The frontmatter lines that say where *memory* came from."""
    if isinstance(memory, Fact):
        return "" if memory.thread is None else f"thread: {memory.thread}\n"
    if isinstance(memory.recaps, RecappedTurn):
        return f"thread: {memory.recaps.thread}\nturn: {memory.recaps.turn}\n"
    return f"run: {memory.recaps.run}\ngeneration: {memory.recaps.generation}\n"


def _read_node(path: Path) -> MemoryNode | None:
    memory, frontmatter = _parse_node(path)
    return None if frontmatter.tombstone else memory


def _parse_node(path: Path) -> tuple[MemoryNode, _Frontmatter]:
    try:
        values, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        frontmatter = _FRONTMATTER.validate_python(values)
    except (UnicodeDecodeError, FrontmatterError, ValidationError) as exc:
        raise MemoryNodeError(f'Graph node "{path}" has invalid frontmatter.') from exc
    node = NodeId(path.stem)
    if isinstance(frontmatter, _FactFrontmatter):
        fact = Fact(
            node=node,
            date=frontmatter.date,
            description=frontmatter.description,
            subjects=frontmatter.subjects,
            body=body,
            thread=frontmatter.thread,
            added_by_user=frontmatter.source == "user",
            sources=frontmatter.sources,
            workbench=frontmatter.workbench,
        )
        return fact, frontmatter
    recaps = (
        RecappedTurn(frontmatter.thread, frontmatter.turn)
        if isinstance(frontmatter, _TurnEpisodeFrontmatter)
        else RecappedRun(frontmatter.run, frontmatter.generation)
    )
    episode = Episode(
        node=node,
        date=frontmatter.date,
        description=frontmatter.description,
        subjects=frontmatter.subjects,
        body=body,
        recaps=recaps,
        tools=frontmatter.tools,
        workbench=frontmatter.workbench,
    )
    return episode, frontmatter
