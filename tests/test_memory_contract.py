import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

from kinby.contracts import ErrorCode, ErrorEnvelope
from tests.test_routines import instance_at
from tests.test_scheduler import FakeClock, call, runtime

_THREAD_ID = UUID("11111111-1111-1111-1111-111111111111")
_TURN_ID = UUID("22222222-2222-2222-2222-222222222222")


def _dispatcher(path: Path):
    return runtime(instance_at(path), FakeClock(datetime(2026, 9, 28, tzinfo=UTC)))


def _node(
    instance_path: Path,
    node: str,
    *,
    description: str,
    subjects: str = "kinby",
    body: str = "The body.",
    thread: UUID | None = _THREAD_ID,
    extra: str = "",
) -> str:
    graph_path = instance_path / "memory" / "graph"
    graph_path.mkdir(parents=True, exist_ok=True)
    thread_line = f"thread: {thread}\n" if thread is not None else ""
    (graph_path / f"{node}.md").write_text(
        (
            "---\n"
            f"date: {node[:10]}\n"
            f"{thread_line}"
            f"description: {description}\n"
            f"subjects: [{subjects}]\n"
            f"{extra}"
            "---\n"
            f"{body}\n"
        ),
        encoding="utf-8",
    )
    return node


def _episode(instance_path: Path, node: str, *, description: str, subjects: str = "kinby") -> str:
    return _node(
        instance_path,
        node,
        description=description,
        subjects=subjects,
        body="Found the stale tag, then rebuilt.",
        extra=f"turn: {_TURN_ID}\ntools: [grep, bash]\n",
    )


def _user_fact(instance_path: Path, node: str, *, description: str) -> str:
    return _node(
        instance_path,
        node,
        description=description,
        subjects="coffee",
        body="Black, no sugar.",
        thread=None,
        extra="source: user\n",
    )


async def _listed(dispatcher, **command) -> list[str]:
    listed = await call(dispatcher, "memory.list", **command)
    return [item.node for item in listed.items]


def test_list_returns_every_node_newest_first(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        older = _node(tmp_path, "2026-08-01-picked-markdown", description="Picked markdown")
        newer = _episode(tmp_path, "2026-09-01-fixed-deploy", description="Fixed the deploy")
        same_day = _user_fact(tmp_path, "2026-09-01-likes-coffee", description="Likes coffee")

        listed = await call(dispatcher, "memory.list")

        assert [item.node for item in listed.items] == [same_day, newer, older]
        assert listed.cursor is None
        episode = listed.items[1]
        assert (episode.kind, episode.date, episode.description, episode.subjects) == (
            "episode",
            date(2026, 9, 1),
            "Fixed the deploy",
            ["kinby"],
        )

    asyncio.run(scenario())


def _forget(instance_path: Path, node: str) -> None:
    path = instance_path / "memory" / "graph" / f"{node}.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("---\n", "---\ntombstone: true\n", 1),
        encoding="utf-8",
    )


def test_list_query_matches_descriptions_and_subjects_as_recall_does(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        by_subject = _node(
            tmp_path, "2026-09-02-picked-a-gate", description="Picked a gate", subjects="Kinby"
        )
        by_description = _node(
            tmp_path, "2026-09-01-kinby-memory", description="Kinby memory", subjects="notes"
        )
        _node(tmp_path, "2026-09-03-gardening", description="Gardening", subjects="home")

        assert await _listed(dispatcher, query="KINBY") == [by_subject, by_description]
        assert await _listed(dispatcher, query="kinby gate") == [by_subject]
        assert len(await _listed(dispatcher, query="")) == 3

    asyncio.run(scenario())


def test_list_filters_by_kind(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        fact = _node(tmp_path, "2026-09-01-picked-markdown", description="Picked markdown")
        user_fact = _user_fact(tmp_path, "2026-09-02-likes-coffee", description="Likes coffee")
        episode = _episode(tmp_path, "2026-09-03-fixed-deploy", description="Fixed the deploy")

        assert await _listed(dispatcher, kind="fact") == [user_fact, fact]
        assert await _listed(dispatcher, kind="episode") == [episode]

    asyncio.run(scenario())


def test_list_filters_by_one_subject_exactly_and_ignoring_case(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        gate = _node(
            tmp_path,
            "2026-09-01-fixed-the-gate",
            description="Fixed the gate",
            subjects="kinby, Permission Gate",
        )
        _node(
            tmp_path,
            "2026-09-02-planned-permissions",
            description="Planned permissions",
            subjects="permission",
        )

        assert await _listed(dispatcher, subject="permission gate") == [gate]

    asyncio.run(scenario())


def test_list_date_bounds_are_inclusive(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        _node(tmp_path, "2026-09-01-first", description="First")
        second = _node(tmp_path, "2026-09-02-second", description="Second")
        third = _node(tmp_path, "2026-09-03-third", description="Third")
        _node(tmp_path, "2026-09-04-fourth", description="Fourth")

        assert await _listed(dispatcher, after="2026-09-02", before="2026-09-03") == [
            third,
            second,
        ]
        assert await _listed(dispatcher, after="2026-09-04") == ["2026-09-04-fourth"]
        assert await _listed(dispatcher, before="2026-09-01") == ["2026-09-01-first"]

    asyncio.run(scenario())


def test_list_filters_combine(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        match = _node(
            tmp_path, "2026-09-02-deploy-fact", description="Deploy fact", subjects="deploy"
        )
        _episode(tmp_path, "2026-09-02-deploy-episode", description="Deploy", subjects="deploy")
        _node(tmp_path, "2026-09-01-older-deploy", description="Older deploy", subjects="deploy")
        _node(tmp_path, "2026-09-03-deploy-other", description="Deploy", subjects="other")

        listed = await _listed(
            dispatcher,
            query="deploy",
            kind="fact",
            subject="Deploy",
            after="2026-09-02",
            before="2026-09-02",
        )

        assert listed == [match]

    asyncio.run(scenario())


def test_list_pages_below_the_cursor_and_ends_with_a_null_cursor(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        nodes = [
            _node(tmp_path, f"2026-09-0{day}-note", description=f"Note {day}")
            for day in range(1, 6)
        ]

        first = await call(dispatcher, "memory.list", limit=2)
        written_between = _node(tmp_path, "2026-09-28-new-note", description="New note")
        second = await call(dispatcher, "memory.list", limit=2, cursor=first.cursor)
        last = await call(dispatcher, "memory.list", limit=2, cursor=second.cursor)

        assert [item.node for item in first.items] == [nodes[4], nodes[3]]
        assert first.cursor == nodes[3]
        assert [item.node for item in second.items] == [nodes[2], nodes[1]]
        assert second.cursor == nodes[1]
        assert [item.node for item in last.items] == [nodes[0]]
        assert last.cursor is None
        assert written_between not in [item.node for item in (*second.items, *last.items)]

    asyncio.run(scenario())


def test_a_full_last_page_has_a_null_cursor(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        _node(tmp_path, "2026-09-01-first", description="First")
        _node(tmp_path, "2026-09-02-second", description="Second")

        listed = await call(dispatcher, "memory.list", limit=2)

        assert len(listed.items) == 2
        assert listed.cursor is None

    asyncio.run(scenario())


def test_list_defaults_to_fifty_and_goes_past_recalls_cap(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        for index in range(60):
            _node(tmp_path, f"2026-09-01-note-{index:02d}", description="Note")

        listed = await call(dispatcher, "memory.list")

        assert len(listed.items) == 50
        assert listed.cursor == "2026-09-01-note-10"

    asyncio.run(scenario())


def test_list_refuses_a_limit_outside_one_to_two_hundred(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)

        for limit in (0, 201):
            refused = await call(dispatcher, "memory.list", limit=limit)
            assert isinstance(refused, ErrorEnvelope)
            assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert (await call(dispatcher, "memory.list", limit=200)).items == []

    asyncio.run(scenario())


def test_list_leaves_out_tombstoned_nodes(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        kept = _node(tmp_path, "2026-09-01-kept", description="Kept")
        forgotten = _node(tmp_path, "2026-09-02-forgotten", description="Forgotten")
        _forget(tmp_path, forgotten)

        assert await _listed(dispatcher) == [kept]

    asyncio.run(scenario())


def test_each_node_says_where_it_came_from(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        agent = _node(tmp_path, "2026-09-01-picked-markdown", description="Picked markdown")
        user = _user_fact(tmp_path, "2026-09-02-likes-coffee", description="Likes coffee")
        recap = _episode(tmp_path, "2026-09-03-fixed-deploy", description="Fixed the deploy")

        listed = await call(dispatcher, "memory.list")

        assert {item.node: item.source for item in listed.items} == {
            agent: "agent",
            user: "user",
            recap: "recap",
        }

    asyncio.run(scenario())


def test_open_returns_an_agent_fact(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        node = _node(
            tmp_path,
            "2026-09-01-picked-markdown",
            description="Picked markdown",
            subjects="memory, kinby",
            body="Markdown until evals justify a database.",
        )

        opened = await call(dispatcher, "memory.open", node=node)

        assert opened.model_dump(mode="json") == {
            "node": node,
            "kind": "fact",
            "date": "2026-09-01",
            "description": "Picked markdown",
            "subjects": ["memory", "kinby"],
            "source": "agent",
            "body": "Markdown until evals justify a database.",
            "thread": str(_THREAD_ID),
            "turn": None,
            "tools": None,
        }

    asyncio.run(scenario())


def test_open_returns_a_user_fact_without_a_thread(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        node = _user_fact(tmp_path, "2026-09-02-likes-coffee", description="Likes coffee")

        opened = await call(dispatcher, "memory.open", node=node)

        assert (opened.kind, opened.source, opened.body) == ("fact", "user", "Black, no sugar.")
        assert (opened.thread, opened.turn, opened.tools) == (None, None, None)

    asyncio.run(scenario())


def test_open_returns_an_episode_with_its_turn_and_tool_path(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        node = _episode(tmp_path, "2026-09-03-fixed-deploy", description="Fixed the deploy")

        opened = await call(dispatcher, "memory.open", node=node)

        assert (opened.kind, opened.source, opened.description) == (
            "episode",
            "recap",
            "Fixed the deploy",
        )
        assert (opened.thread, opened.turn, opened.tools) == (
            _THREAD_ID,
            _TURN_ID,
            ["grep", "bash"],
        )
        assert opened.body == "Found the stale tag, then rebuilt."

    asyncio.run(scenario())


def test_open_refuses_an_unknown_a_forgotten_and_a_malformed_node(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        forgotten = _node(tmp_path, "2026-09-02-forgotten", description="Forgotten")
        _forget(tmp_path, forgotten)

        unknown = await call(dispatcher, "memory.open", node="2026-09-01-never-written")
        tombstoned = await call(dispatcher, "memory.open", node=forgotten)
        malformed = await call(dispatcher, "memory.open", node="../kinby")

        assert isinstance(unknown, ErrorEnvelope)
        assert unknown.code is ErrorCode.NOT_FOUND
        assert isinstance(tombstoned, ErrorEnvelope)
        assert tombstoned.code is ErrorCode.NOT_FOUND
        assert isinstance(malformed, ErrorEnvelope)
        assert malformed.code is ErrorCode.INVALID_ARGUMENT

    asyncio.run(scenario())


def test_list_refuses_a_cursor_that_is_not_a_node_id(tmp_path: Path) -> None:
    async def scenario() -> None:
        refused = await call(_dispatcher(tmp_path), "memory.list", cursor="not-a-node")

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT

    asyncio.run(scenario())
