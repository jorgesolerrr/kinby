import logging
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
    NodeNotFound,
)

_THREAD_ID = UUID("11111111-1111-1111-1111-111111111111")
_TURN_ID = UUID("22222222-2222-2222-2222-222222222222")


def _write_node(
    instance_path: Path,
    *,
    node_id: str,
    node_date: str,
    description: str,
    subjects: str,
    body: str,
    tools: str | None = None,
    turn: UUID | None = None,
) -> None:
    graph_path = instance_path / "memory" / "graph"
    graph_path.mkdir(parents=True, exist_ok=True)
    tools_line = f"tools: [{tools}]\n" if tools is not None else ""
    turn_line = f"turn: {turn}\n" if turn is not None else ""
    (graph_path / f"{node_id}.md").write_text(
        (
            "---\n"
            f"date: {node_date}\n"
            f"thread: {_THREAD_ID}\n"
            f"{turn_line}"
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
        turn=_TURN_ID,
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
        turn=_TURN_ID,
        description="Fixed the deployment",
        subjects=("kinby", "deployment"),
        tools=("grep", "bash", "edit"),
        body="Found the stale image tag, rebuilt the image, then restarted the container.",
    )


def test_episode_frontmatter_requires_a_turn(tmp_path: Path) -> None:
    node = NodeId("2026-08-30-missing-turn")
    _write_node(
        tmp_path,
        node_id=node,
        node_date="2026-08-30",
        description="Missing the episode turn",
        subjects="kinby, memory",
        tools="bash",
        body="Ran the tests.",
    )

    with pytest.raises(MemoryNodeError, match="invalid frontmatter"):
        _graph_store(tmp_path).open(node)


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


def test_recall_returns_facts_over_newer_episodes(tmp_path: Path) -> None:
    fact = NodeId("2026-08-20-deploys-use-the-staging-tag")
    _write_node(
        tmp_path,
        node_id=fact,
        node_date="2026-08-20",
        description="Deploys use the staging tag",
        subjects="kinby, deployment",
        body="Tag the image staging before a deploy.",
    )
    _write_node(
        tmp_path,
        node_id="2026-08-31-checked-the-deployment",
        node_date="2026-08-31",
        description="Checked the deployment",
        subjects="kinby, deployment",
        tools="bash",
        turn=_TURN_ID,
        body="Ran the health check.",
    )

    memories = _graph_store(tmp_path).recall("deployment")

    assert [memory.node for memory in memories] == [fact]


def test_recall_returns_episodes_when_no_fact_matches(tmp_path: Path) -> None:
    _write_node(
        tmp_path,
        node_id="2026-08-20-planned-memory",
        node_date="2026-08-20",
        description="Planned kinby memory",
        subjects="kinby, memory",
        body="The graph uses markdown nodes.",
    )
    for day in (25, 31):
        _write_node(
            tmp_path,
            node_id=f"2026-08-{day}-checked-the-deployment",
            node_date=f"2026-08-{day}",
            description="Checked the deployment",
            subjects="kinby, deployment",
            tools="bash",
            turn=_TURN_ID,
            body="Ran the health check.",
        )

    memories = _graph_store(tmp_path).recall("deployment")

    assert [memory.node for memory in memories] == [
        NodeId("2026-08-31-checked-the-deployment"),
        NodeId("2026-08-25-checked-the-deployment"),
    ]


def test_recall_returns_facts_and_episodes_within_a_date_bound(tmp_path: Path) -> None:
    _write_node(
        tmp_path,
        node_id="2026-08-25-deploys-use-the-staging-tag",
        node_date="2026-08-25",
        description="Deploys use the staging tag",
        subjects="kinby, deployment",
        body="Tag the image staging before a deploy.",
    )
    _write_node(
        tmp_path,
        node_id="2026-08-26-checked-the-deployment",
        node_date="2026-08-26",
        description="Checked the deployment",
        subjects="kinby, deployment",
        tools="bash",
        turn=_TURN_ID,
        body="Ran the health check.",
    )
    _write_node(
        tmp_path,
        node_id="2026-08-31-rolled-back-the-deployment",
        node_date="2026-08-31",
        description="Rolled back the deployment",
        subjects="kinby, deployment",
        tools="bash",
        turn=_TURN_ID,
        body="Restored the previous image.",
    )

    memories = _graph_store(tmp_path).recall("deployment", after=date(2026, 8, 25))
    before_the_rollback = _graph_store(tmp_path).recall("deployment", before=date(2026, 8, 30))

    assert [memory.node for memory in memories] == [
        NodeId("2026-08-31-rolled-back-the-deployment"),
        NodeId("2026-08-26-checked-the-deployment"),
        NodeId("2026-08-25-deploys-use-the-staging-tag"),
    ]
    assert [memory.node for memory in before_the_rollback] == [
        NodeId("2026-08-26-checked-the-deployment"),
        NodeId("2026-08-25-deploys-use-the-staging-tag"),
    ]


def test_recall_caps_matching_episodes_at_twenty(tmp_path: Path) -> None:
    for day in range(1, 26):
        _write_node(
            tmp_path,
            node_id=f"2026-08-{day:02d}-checked-memory",
            node_date=f"2026-08-{day:02d}",
            description=f"Checked memory {day}",
            subjects="kinby, memory",
            tools="bash",
            turn=_TURN_ID,
            body=f"Checked memory {day}.",
        )

    memories = _graph_store(tmp_path).recall("memory")

    assert len(memories) == 20
    assert memories[0].node == NodeId("2026-08-25-checked-memory")
    assert memories[-1].node == NodeId("2026-08-06-checked-memory")


def test_recall_caps_facts_and_episodes_within_a_date_bound_at_twenty(tmp_path: Path) -> None:
    for day in range(1, 26):
        episode = day % 2 == 0
        _write_node(
            tmp_path,
            node_id=f"2026-08-{day:02d}-memory-{day}",
            node_date=f"2026-08-{day:02d}",
            description=f"Memory {day}",
            subjects="kinby, memory",
            tools="bash" if episode else None,
            turn=_TURN_ID if episode else None,
            body=f"Memory {day}.",
        )

    memories = _graph_store(tmp_path).recall("memory", after=date(2026, 8, 1))

    assert len(memories) == 20
    assert memories[0].node == NodeId("2026-08-25-memory-25")
    assert memories[1].node == NodeId("2026-08-24-memory-24")
    assert memories[-1].node == NodeId("2026-08-06-memory-6")


def test_recall_falls_back_to_episodes_past_a_forgotten_fact(tmp_path: Path) -> None:
    forgotten_fact = NodeId("2026-08-31-deploys-use-the-staging-tag")
    _write_node(
        tmp_path,
        node_id=forgotten_fact,
        node_date="2026-08-31",
        description="Deploys use the staging tag",
        subjects="kinby, deployment",
        body="Tag the image staging before a deploy.",
    )
    forgotten_episode = NodeId("2026-08-26-rolled-back-the-deployment")
    episode = NodeId("2026-08-25-checked-the-deployment")
    for node, node_date in ((forgotten_episode, "2026-08-26"), (episode, "2026-08-25")):
        _write_node(
            tmp_path,
            node_id=node,
            node_date=node_date,
            description="Worked on the deployment",
            subjects="kinby, deployment",
            tools="bash",
            turn=_TURN_ID,
            body="Ran the health check.",
        )
    memory = _graph_store(tmp_path)

    memory.forget(forgotten_fact)
    memory.forget(forgotten_episode)

    assert [hit.node for hit in memory.recall("deployment")] == [episode]
    assert [hit.node for hit in memory.recall("deployment", after=date(2026, 8, 1))] == [episode]


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
        'description: "Picked markdown for memory"\n'
        'subjects: ["memory backend", "kinby"]\n'
        "---\n"
        "The memory backend uses markdown until evals justify a database.\n"
    )
    assert events_path.read_bytes() == b"canonical transcript\n"


def test_remember_escapes_frontmatter_values(tmp_path: Path) -> None:
    node = NodeId("2026-09-01-frontmatter-shaped-fact")
    fact = Fact(
        node=node,
        date=date(2026, 9, 1),
        thread=_THREAD_ID,
        description="A delimiter follows\n---\ntombstone: true",
        subjects=("memory, graph", "subject]\n---\ntombstone: true"),
        body="The frontmatter-shaped text is data.",
    )
    memory = _graph_store(tmp_path)

    memory.remember(fact)

    node_path = tmp_path / "memory" / "graph" / f"{node}.md"
    document = node_path.read_text(encoding="utf-8")
    assert document.splitlines().count("---") == 2
    assert memory.open(node) == fact
    assert memory.recall("memory graph")[0].node == node


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
    with pytest.raises(NodeNotFound, match="was forgotten"):
        memory.open(node)
    assert events_path.read_bytes() == b"canonical transcript\n"


def test_a_fact_without_a_thread_added_by_the_user_is_recalled_and_opened(tmp_path: Path) -> None:
    node = NodeId("2026-09-02-likes-coffee")
    graph_path = tmp_path / "memory" / "graph"
    graph_path.mkdir(parents=True)
    (graph_path / f"{node}.md").write_text(
        (
            "---\n"
            "date: 2026-09-02\n"
            "description: Likes coffee\n"
            "subjects: [coffee]\n"
            "source: user\n"
            "---\n"
            "Black, no sugar.\n"
        ),
        encoding="utf-8",
    )
    memory = _graph_store(tmp_path)

    assert [hit.node for hit in memory.recall("coffee")] == [node]
    assert memory.open(node) == Fact(
        node=node,
        date=date(2026, 9, 2),
        description="Likes coffee",
        subjects=("coffee",),
        body="Black, no sugar.",
        thread=None,
        added_by_user=True,
    )


def test_remember_writes_a_user_fact_with_its_source_and_no_thread(tmp_path: Path) -> None:
    node = NodeId("2026-09-02-likes-coffee")
    fact = Fact(
        node=node,
        date=date(2026, 9, 2),
        description="Likes coffee",
        subjects=("coffee",),
        body="Black, no sugar.",
        thread=None,
        added_by_user=True,
    )
    memory = _graph_store(tmp_path)

    memory.remember(fact)

    assert memory.open(node) == fact
    assert (tmp_path / "memory" / "graph" / f"{node}.md").read_text(encoding="utf-8") == (
        "---\n"
        "date: 2026-09-02\n"
        'description: "Likes coffee"\n'
        'subjects: ["coffee"]\n'
        "source: user\n"
        "---\n"
        "Black, no sugar.\n"
    )


def test_episode_frontmatter_requires_a_thread(tmp_path: Path) -> None:
    node = NodeId("2026-08-30-missing-thread")
    graph_path = tmp_path / "memory" / "graph"
    graph_path.mkdir(parents=True)
    (graph_path / f"{node}.md").write_text(
        (
            "---\n"
            "date: 2026-08-30\n"
            f"turn: {_TURN_ID}\n"
            "description: Missing the episode thread\n"
            "subjects: [kinby]\n"
            "tools: [bash]\n"
            "---\n"
            "Ran the tests.\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(MemoryNodeError, match="invalid frontmatter"):
        _graph_store(tmp_path).open(node)


def test_open_refuses_an_id_that_was_never_written(tmp_path: Path) -> None:
    with pytest.raises(NodeNotFound, match="was not found"):
        _graph_store(tmp_path).open(NodeId("2026-09-01-never-written"))


def test_a_broken_node_is_skipped_with_a_warning_and_still_refuses_to_open(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_node(
        tmp_path,
        node_id="2026-09-01-picked-markdown",
        node_date="2026-09-01",
        description="Picked markdown for memory",
        subjects="memory backend, kinby",
        body="The memory backend uses markdown until evals justify a database.",
    )
    broken = NodeId("2026-09-02-truncated")
    broken_path = tmp_path / "memory" / "graph" / f"{broken}.md"
    broken_path.write_text("---\ndate: 2026-09-02\ndescrip", encoding="utf-8")
    memory = GraphStore(tmp_path)

    with caplog.at_level(logging.WARNING, logger="kinby.memory.graph"):
        nodes = memory.nodes()
    [nodes_warning] = caplog.records
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="kinby.memory.graph"):
        hits = memory.recall("memory")
    [recall_warning] = caplog.records

    assert [node.node for node in nodes] == [NodeId("2026-09-01-picked-markdown")]
    assert [hit.node for hit in hits] == [NodeId("2026-09-01-picked-markdown")]
    assert str(broken_path) in nodes_warning.getMessage()
    assert str(broken_path) in recall_warning.getMessage()
    with pytest.raises(MemoryNodeError, match="invalid frontmatter"):
        memory.open(broken)
    with pytest.raises(MemoryNodeError, match="invalid frontmatter"):
        memory.forget(broken)


def test_remember_and_forget_leave_no_staging_file(tmp_path: Path) -> None:
    node = NodeId("2026-09-01-picked-markdown")
    memory = _graph_store(tmp_path)
    graph_path = tmp_path / "memory" / "graph"

    memory.remember(
        Fact(
            node=node,
            date=date(2026, 9, 1),
            thread=_THREAD_ID,
            description="Picked markdown for memory",
            subjects=("memory backend",),
            body="The memory backend uses markdown.",
        )
    )
    after_remember = sorted(path.name for path in graph_path.iterdir())
    memory.forget(node)

    assert after_remember == [f"{node}.md"]
    assert sorted(path.name for path in graph_path.iterdir()) == [f"{node}.md"]


def test_a_node_with_invalid_utf8_is_skipped_and_refuses_to_open(tmp_path: Path) -> None:
    _write_node(
        tmp_path,
        node_id="2026-09-01-picked-markdown",
        node_date="2026-09-01",
        description="Picked markdown for memory",
        subjects="memory backend, kinby",
        body="The memory backend uses markdown until evals justify a database.",
    )
    broken = NodeId("2026-09-02-cut-mid-character")
    (tmp_path / "memory" / "graph" / f"{broken}.md").write_bytes(
        b"---\ndate: 2026-09-02\ndescription: Caf" + "é".encode()[:1]
    )
    memory = GraphStore(tmp_path)

    assert [node.node for node in memory.nodes()] == [NodeId("2026-09-01-picked-markdown")]
    with pytest.raises(MemoryNodeError, match="invalid frontmatter"):
        memory.open(broken)


def test_a_failed_remember_keeps_the_node_and_leaves_no_staging_file(tmp_path: Path) -> None:
    node = NodeId("2026-09-01-picked-markdown")
    fact = Fact(
        node=node,
        date=date(2026, 9, 1),
        thread=_THREAD_ID,
        description="Picked markdown for memory",
        subjects=("memory backend",),
        body="The memory backend uses markdown.",
    )
    memory = _graph_store(tmp_path)
    memory.remember(fact)
    node_path = tmp_path / "memory" / "graph" / f"{node}.md"
    before = node_path.read_bytes()

    with pytest.raises(UnicodeEncodeError):
        memory.remember(
            Fact(
                node=node,
                date=date(2026, 9, 1),
                thread=_THREAD_ID,
                description="Picked markdown for memory",
                subjects=("memory backend",),
                body="A lone surrogate \ud800 cannot be written.",
            )
        )

    assert [path.name for path in node_path.parent.iterdir()] == [f"{node}.md"]
    assert node_path.read_bytes() == before
