import asyncio
import hashlib
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

from kinby.contracts import ErrorCode, ErrorEnvelope, Scope
from kinby.core import LangGraphRunner
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


def _graph_files(instance_path: Path) -> dict[str, str]:
    graph_path = instance_path / "memory" / "graph"
    return {path.stem: path.read_text(encoding="utf-8") for path in graph_path.glob("*.md")}


def _events(instance_path: Path) -> bytes:
    events_path = instance_path / ".state" / "events.jsonl"
    return events_path.read_bytes() if events_path.is_file() else b""


def _refused(result: object, code: ErrorCode) -> ErrorEnvelope:
    assert isinstance(result, ErrorEnvelope)
    assert result.code is code
    return result


def test_add_writes_a_user_fact_dated_today_with_no_thread(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        events = _events(tmp_path)

        added = await call(
            dispatcher,
            "memory.add",
            description="  Likes coffee black  ",
            subjects=["coffee", "mornings"],
            body="No sugar.",
        )

        assert added.node.startswith("2026-09-28-")
        assert _graph_files(tmp_path) == {
            added.node: (
                "---\n"
                "date: 2026-09-28\n"
                'description: "Likes coffee black"\n'
                'subjects: ["coffee", "mornings"]\n'
                "source: user\n"
                "---\n"
                "No sugar.\n"
            )
        }
        opened = await call(dispatcher, "memory.open", node=added.node)
        assert (opened.source, opened.thread) == ("user", None)
        assert _events(tmp_path) == events

    asyncio.run(scenario())


def test_add_takes_empty_subjects_and_body(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)

        added = await call(
            dispatcher, "memory.add", description="Lives in Madrid", subjects=[], body=""
        )

        opened = await call(dispatcher, "memory.open", node=added.node)
        assert (opened.description, opened.subjects, opened.body) == ("Lives in Madrid", [], "")

    asyncio.run(scenario())


def test_add_refuses_a_blank_description_and_writes_nothing(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)

        refused = await call(dispatcher, "memory.add", description="  ", subjects=[], body="")

        assert set(_refused(refused, ErrorCode.INVALID_ARGUMENT).fields) == {"description"}
        assert _graph_files(tmp_path) == {}

    asyncio.run(scenario())


def test_writes_need_the_admin_scope(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        node = _node(tmp_path, "2026-09-01-picked-markdown", description="Picked markdown")
        fact = {"description": "Picked markdown", "subjects": [], "body": ""}

        for method, payload in (
            ("memory.add", fact),
            ("memory.correct", {"node": node, **fact}),
            ("memory.forget", {"node": node}),
        ):
            refused = await dispatcher.dispatch(method, payload, {Scope.INSTANCE_READ})
            _refused(refused, ErrorCode.PERMISSION_DENIED)

    asyncio.run(scenario())


def test_correct_writes_a_new_user_fact_and_tombstones_the_old_one(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        old = _node(tmp_path, "2026-09-01-likes-tea", description="Likes tea", subjects="drinks")
        events = _events(tmp_path)

        corrected = await call(
            dispatcher,
            "memory.correct",
            node=old,
            description="Likes coffee",
            subjects=["drinks"],
            body="Black, no sugar.",
        )

        files = _graph_files(tmp_path)
        assert corrected.node.startswith("2026-09-28-")
        assert files[corrected.node] == (
            "---\n"
            "date: 2026-09-28\n"
            'description: "Likes coffee"\n'
            'subjects: ["drinks"]\n'
            "source: user\n"
            "---\n"
            "Black, no sugar.\n"
        )
        assert "tombstone: true\n" in files[old]
        assert await _listed(dispatcher) == [corrected.node]
        assert _events(tmp_path) == events

    asyncio.run(scenario())


def test_correct_refuses_an_episode_a_forgotten_and_an_unknown_node_writing_nothing(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        episode = _episode(tmp_path, "2026-09-03-fixed-deploy", description="Fixed the deploy")
        forgotten = _node(tmp_path, "2026-09-02-forgotten", description="Forgotten")
        _forget(tmp_path, forgotten)
        before = _graph_files(tmp_path)
        fact = {"description": "Corrected", "subjects": [], "body": ""}

        on_episode = await call(dispatcher, "memory.correct", node=episode, **fact)
        on_forgotten = await call(dispatcher, "memory.correct", node=forgotten, **fact)
        on_unknown = await call(dispatcher, "memory.correct", node="2026-09-01-never", **fact)

        _refused(on_episode, ErrorCode.INVALID_ARGUMENT)
        _refused(on_forgotten, ErrorCode.NOT_FOUND)
        _refused(on_unknown, ErrorCode.NOT_FOUND)
        assert _graph_files(tmp_path) == before

    asyncio.run(scenario())


def test_correct_refuses_a_blank_description_and_keeps_the_old_fact(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        old = _node(tmp_path, "2026-09-01-likes-tea", description="Likes tea")
        before = _graph_files(tmp_path)

        refused = await call(
            dispatcher, "memory.correct", node=old, description="", subjects=[], body=""
        )

        assert set(_refused(refused, ErrorCode.INVALID_ARGUMENT).fields) == {"description"}
        assert _graph_files(tmp_path) == before

    asyncio.run(scenario())


def test_forget_tombstones_a_fact_or_an_episode(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        fact = _node(tmp_path, "2026-09-01-likes-tea", description="Likes tea")
        episode = _episode(tmp_path, "2026-09-03-fixed-deploy", description="Fixed the deploy")
        events = _events(tmp_path)

        forgot_fact = await call(dispatcher, "memory.forget", node=fact)
        forgot_episode = await call(dispatcher, "memory.forget", node=episode)

        assert forgot_fact.model_dump() == {}
        assert forgot_episode.model_dump() == {}
        files = _graph_files(tmp_path)
        assert "tombstone: true\n" in files[fact]
        assert "tombstone: true\n" in files[episode]
        assert await _listed(dispatcher) == []
        assert _events(tmp_path) == events

    asyncio.run(scenario())


def test_forget_refuses_a_forgotten_an_unknown_and_a_malformed_node(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        fact = _node(tmp_path, "2026-09-01-likes-tea", description="Likes tea")
        await call(dispatcher, "memory.forget", node=fact)
        before = _graph_files(tmp_path)

        again = await call(dispatcher, "memory.forget", node=fact)
        unknown = await call(dispatcher, "memory.forget", node="2026-09-01-never")
        malformed = await call(dispatcher, "memory.forget", node="../kinby")

        _refused(again, ErrorCode.NOT_FOUND)
        _refused(unknown, ErrorCode.NOT_FOUND)
        _refused(malformed, ErrorCode.INVALID_ARGUMENT)
        assert _graph_files(tmp_path) == before

    asyncio.run(scenario())


def _profile(instance_path: Path) -> Path:
    return instance_path / "memory" / "profile.md"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_profile_get_returns_the_text_its_hash_and_about_how_many_tokens(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        _profile(tmp_path).parent.mkdir()
        _profile(tmp_path).write_text("Call me Jo.\n", encoding="utf-8")

        read = await call(dispatcher, "profile.get")

        assert read.model_dump() == {
            "text": "Call me Jo.\n",
            "hash": _sha256("Call me Jo.\n"),
            "tokens": 3,
        }

    asyncio.run(scenario())


def test_a_missing_profile_reads_as_empty_text_with_the_empty_hash(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)

        read = await call(dispatcher, "profile.get")

        assert read.model_dump() == {"text": "", "hash": _sha256(""), "tokens": 0}

    asyncio.run(scenario())


def test_profile_set_writes_the_profile_read_and_counts_its_tokens(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        _profile(tmp_path).parent.mkdir()
        _profile(tmp_path).write_text("Call me Jo.\n", encoding="utf-8")
        read = await call(dispatcher, "profile.get")

        written = await call(
            dispatcher, "profile.set", text="Call me Jo. Mornings only.", hash=read.hash
        )

        assert _profile(tmp_path).read_text(encoding="utf-8") == "Call me Jo. Mornings only."
        assert written.model_dump() == {
            "text": "Call me Jo. Mornings only.",
            "hash": _sha256("Call me Jo. Mornings only."),
            "tokens": 7,
        }
        assert await call(dispatcher, "profile.get") == written
        assert [path.name for path in _profile(tmp_path).parent.iterdir()] == ["profile.md"]

    asyncio.run(scenario())


def test_profile_set_over_a_missing_file_takes_the_empty_hash(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)

        await call(dispatcher, "profile.set", text="Call me Jo.", hash=_sha256(""))

        assert _profile(tmp_path).read_text(encoding="utf-8") == "Call me Jo."

    asyncio.run(scenario())


def test_profile_set_with_a_stale_hash_leaves_the_file_alone(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        _profile(tmp_path).parent.mkdir()
        _profile(tmp_path).write_text("Call me Jo.\n", encoding="utf-8")
        read = await call(dispatcher, "profile.get")
        _profile(tmp_path).write_text("Call me Jorge.\n", encoding="utf-8")

        refused = await call(dispatcher, "profile.set", text="Mine.", hash=read.hash)

        assert not _refused(refused, ErrorCode.STALE).retryable
        assert _profile(tmp_path).read_text(encoding="utf-8") == "Call me Jorge.\n"
        assert (await call(dispatcher, "config.history", limit=10)).changes == []

    asyncio.run(scenario())


def test_each_profile_write_records_one_config_change_by_the_app(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)

        await call(dispatcher, "profile.set", text="Call me Jo.\n", hash=_sha256(""))
        await call(
            dispatcher, "profile.set", text="Call me Jorge.\n", hash=_sha256("Call me Jo.\n")
        )

        history = await call(dispatcher, "config.history", file="memory/profile.md", limit=10)
        assert [(change.file, change.actor) for change in history.changes] == [
            ("memory/profile.md", "app"),
            ("memory/profile.md", "app"),
        ]
        assert history.changes[0].diff == (
            "--- a/memory/profile.md\n"
            "+++ b/memory/profile.md\n"
            "@@ -1 +1 @@\n"
            "-Call me Jo.\n"
            "+Call me Jorge.\n"
        )

    asyncio.run(scenario())


def test_profile_set_needs_the_admin_scope(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)

        read = await dispatcher.dispatch("profile.get", {}, {Scope.INSTANCE_READ})
        refused = await dispatcher.dispatch(
            "profile.set", {"text": "Mine.", "hash": _sha256("")}, {Scope.INSTANCE_READ}
        )

        assert not isinstance(read, ErrorEnvelope)
        _refused(refused, ErrorCode.PERMISSION_DENIED)
        assert not _profile(tmp_path).exists()

    asyncio.run(scenario())


def test_a_saved_profile_applies_at_the_next_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 28, tzinfo=UTC)))
        runner = LangGraphRunner(instance)
        before = runner.prepare_for_turn().system_prompt

        await call(dispatcher, "profile.set", text="Call me Jo.", hash=_sha256(""))

        assert "Call me Jo." not in before
        assert "Call me Jo." in runner.prepare_for_turn().system_prompt

    asyncio.run(scenario())
