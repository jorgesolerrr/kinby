from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from kinby.memory import (
    Episode,
    Fact,
    GraphStore,
    Memory,
    MemoryHit,
    MemoryNodeError,
    NodeId,
)

_THREAD_ID = UUID("11111111-1111-1111-1111-111111111111")


def _write_node(
    instance_path: Path,
    *,
    node_id: str,
    node_date: str,
    description: str,
    subjects: str,
    body: str,
    tools: str | None = None,
) -> None:
    graph_path = instance_path / "memory" / "graph"
    graph_path.mkdir(parents=True, exist_ok=True)
    tools_line = f"tools: [{tools}]\n" if tools is not None else ""
    (graph_path / f"{node_id}.md").write_text(
        (
            "---\n"
            f"date: {node_date}\n"
            f"thread: {_THREAD_ID}\n"
            f"description: {description}\n"
            f"subjects: [{subjects}]\n"
            f"{tools_line}"
            "---\n"
            f"{body}\n"
        ),
        encoding="utf-8",
    )


def _graph_store(instance_path: Path) -> Memory:
    return GraphStore(instance_path)


def test_recall_finds_nodes_on_one_day(tmp_path: Path) -> None:
    _write_node(
        tmp_path,
        node_id="2026-08-29-fixed-permission-gate",
        node_date="2026-08-29",
        description="Fixed the kinby permission gate",
        subjects="kinby, permission gate",
        body="The gate now checks every tool call.",
    )
    _write_node(
        tmp_path,
        node_id="2026-08-30-planned-memory",
        node_date="2026-08-30",
        description="Planned kinby memory",
        subjects="kinby, memory",
        body="The graph uses markdown nodes.",
    )

    memories = _graph_store(tmp_path).recall(
        "kinby",
        after=date(2026, 8, 30),
        before=date(2026, 8, 30),
    )

    assert memories == (
        MemoryHit(
            node=NodeId("2026-08-30-planned-memory"),
            date=date(2026, 8, 30),
            description="Planned kinby memory",
        ),
    )


def test_recall_returns_the_last_touched_subject_first(tmp_path: Path) -> None:
    _write_node(
        tmp_path,
        node_id="2026-08-20-started-permission-work",
        node_date="2026-08-20",
        description="Started permission work",
        subjects="kinby, permission gate",
        body="The first gate draft used fixed modes.",
    )
    _write_node(
        tmp_path,
        node_id="2026-08-31-finished-permission-work",
        node_date="2026-08-31",
        description="Finished permission work",
        subjects="kinby, permission gate",
        body="The gate now applies rules per tool.",
    )

    memories = _graph_store(tmp_path).recall("permission KINBY")

    assert [memory.node for memory in memories] == [
        NodeId("2026-08-31-finished-permission-work"),
        NodeId("2026-08-20-started-permission-work"),
    ]


def test_episode_is_searchable_and_opens_with_its_trace(tmp_path: Path) -> None:
    node = NodeId("2026-08-30-fixed-deployment")
    _write_node(
        tmp_path,
        node_id=node,
        node_date="2026-08-30",
        description="Fixed the deployment",
        subjects="kinby, deployment",
        tools="grep, bash, edit",
        body="Found the stale image tag, rebuilt the image, then restarted the container.",
    )
    memory = _graph_store(tmp_path)

    hits = memory.recall("deployment")
    opened = memory.open(node)

    assert [hit.node for hit in hits] == [node]
    assert opened == Episode(
        node=node,
        date=date(2026, 8, 30),
        thread=_THREAD_ID,
        description="Fixed the deployment",
        subjects=("kinby", "deployment"),
        tools=("grep", "bash", "edit"),
        body="Found the stale image tag, rebuilt the image, then restarted the container.",
    )


def test_latest_fact_about_a_subject_wins(tmp_path: Path) -> None:
    _write_node(
        tmp_path,
        node_id="2026-08-10-picked-neo4j",
        node_date="2026-08-10",
        description="Picked Neo4j for memory",
        subjects="memory backend",
        body="The memory backend will use Neo4j.",
    )
    latest = NodeId("2026-08-31-picked-markdown")
    _write_node(
        tmp_path,
        node_id=latest,
        node_date="2026-08-31",
        description="Picked markdown for memory",
        subjects="memory backend",
        body="The memory backend will use markdown until evals justify a database.",
    )
    memory = _graph_store(tmp_path)

    current = memory.open(memory.recall("memory backend")[0].node)

    assert isinstance(current, Fact)
    assert current.node == latest
    assert current.body == ("The memory backend will use markdown until evals justify a database.")


def test_recall_returns_no_guess_for_a_non_matching_query(tmp_path: Path) -> None:
    _write_node(
        tmp_path,
        node_id="2026-08-30-planned-memory",
        node_date="2026-08-30",
        description="Planned memory",
        subjects="kinby, memory",
        body="The graph uses markdown nodes.",
    )

    assert _graph_store(tmp_path).recall("gardening") == ()


def test_recall_caps_matching_nodes_at_twenty(tmp_path: Path) -> None:
    for day in range(1, 26):
        _write_node(
            tmp_path,
            node_id=f"2026-08-{day:02d}-memory-note",
            node_date=f"2026-08-{day:02d}",
            description=f"Memory note {day}",
            subjects="kinby, memory",
            body=f"Memory note {day} body.",
        )

    memories = _graph_store(tmp_path).recall("memory")

    assert len(memories) == 20
    assert memories[0].node == NodeId("2026-08-25-memory-note")
    assert memories[-1].node == NodeId("2026-08-06-memory-note")


def test_remember_writes_a_fact_that_later_recall_finds(tmp_path: Path) -> None:
    node = NodeId("2026-09-01-picked-markdown")
    fact = Fact(
        node=node,
        date=date(2026, 9, 1),
        thread=_THREAD_ID,
        description="Picked markdown for memory",
        subjects=("memory backend", "kinby"),
        body="The memory backend uses markdown until evals justify a database.",
    )
    events_path = tmp_path / ".state" / "events.jsonl"
    events_path.parent.mkdir()
    events_path.write_bytes(b"canonical transcript\n")
    memory = _graph_store(tmp_path)

    remembered = memory.remember(fact)

    assert remembered == node
    assert memory.recall("memory backend") == (
        MemoryHit(
            node=node,
            date=date(2026, 9, 1),
            description="Picked markdown for memory",
        ),
    )
    assert memory.open(node) == fact
    assert (tmp_path / "memory" / "graph" / f"{node}.md").read_text(encoding="utf-8") == (
        "---\n"
        "date: 2026-09-01\n"
        f"thread: {_THREAD_ID}\n"
        "description: Picked markdown for memory\n"
        "subjects: [memory backend, kinby]\n"
        "---\n"
        "The memory backend uses markdown until evals justify a database.\n"
    )
    assert events_path.read_bytes() == b"canonical transcript\n"


def test_forget_tombstones_a_fact_and_excludes_it_from_recall_and_open(
    tmp_path: Path,
) -> None:
    node = NodeId("2026-09-01-picked-markdown")
    _write_node(
        tmp_path,
        node_id=node,
        node_date="2026-09-01",
        description="Picked markdown for memory",
        subjects="memory backend, kinby",
        body="The memory backend uses markdown until evals justify a database.",
    )
    events_path = tmp_path / ".state" / "events.jsonl"
    events_path.parent.mkdir()
    events_path.write_bytes(b"canonical transcript\n")
    node_path = tmp_path / "memory" / "graph" / f"{node}.md"
    memory = _graph_store(tmp_path)

    memory.forget(node)

    assert node_path.is_file()
    assert "tombstone: true\n" in node_path.read_text(encoding="utf-8")
    assert memory.recall("memory backend") == ()
    with pytest.raises(MemoryNodeError, match="was forgotten"):
        memory.open(node)
    assert events_path.read_bytes() == b"canonical transcript\n"
