import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk
from pydantic import JsonValue

from kinby.contracts import (
    Delivery,
    ErrorCode,
    ErrorEnvelope,
    Event,
    Payload,
    PermissionMode,
    RoutineName,
    RoutineTrigger,
    SystemPrompt,
)
from kinby.core import LangGraphRunner
from kinby.core.turns import PreparedTurnRequest, TurnContext, TurnOutcome
from kinby.instance.recap import DEFAULT_RECAP_LENS
from kinby.plugins import ToolContext
from kinby.plugins.instance_tools import instance_tools
from tests.test_instance_tools import ScriptedModel
from tests.test_routines import instance_at, routine_file
from tests.test_scheduler import FailingRunner, FakeClock, call, runtime

EMPTY_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _dispatcher(path: Path):
    return runtime(instance_at(path), FakeClock(datetime(2026, 9, 28, tzinfo=UTC)))


def test_prompt_get_returns_the_file_and_its_hash(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        (tmp_path / "SYSTEM.md").write_text("Be brief.\n", encoding="utf-8")
        (tmp_path / "RECAP.md").write_text("Name one lesson.\n", encoding="utf-8")

        behavior = await call(dispatcher, "prompt.get", name="behavior")
        recap = await call(dispatcher, "prompt.get", name="recap")

        assert (behavior.content, behavior.hash, behavior.default) == (
            "Be brief.\n",
            _sha256("Be brief.\n"),
            False,
        )
        assert (recap.content, recap.hash, recap.default) == (
            "Name one lesson.\n",
            _sha256("Name one lesson.\n"),
            False,
        )

    asyncio.run(scenario())


def test_prompt_get_shows_the_shipped_text_when_the_file_is_missing(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)

        behavior = await call(dispatcher, "prompt.get", name="behavior")
        recap = await call(dispatcher, "prompt.get", name="recap")

        assert (behavior.content, behavior.hash, behavior.default) == ("", EMPTY_HASH, True)
        assert (recap.content, recap.hash, recap.default) == (DEFAULT_RECAP_LENS, EMPTY_HASH, True)

    asyncio.run(scenario())


def test_prompt_get_refuses_an_unknown_prompt(tmp_path: Path) -> None:
    async def scenario() -> None:
        result = await call(_dispatcher(tmp_path), "prompt.get", name="profile")

        assert isinstance(result, ErrorEnvelope)
        assert result.code is ErrorCode.INVALID_ARGUMENT

    asyncio.run(scenario())


def test_prompt_set_writes_the_file_read_with_its_hash(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        (tmp_path / "SYSTEM.md").write_text("Be brief.\n", encoding="utf-8")
        read = await call(dispatcher, "prompt.get", name="behavior")

        written = await call(
            dispatcher, "prompt.set", name="behavior", content="Be kind.\n", hash=read.hash
        )

        assert (tmp_path / "SYSTEM.md").read_text(encoding="utf-8") == "Be kind.\n"
        assert (written.content, written.hash, written.default) == (
            "Be kind.\n",
            _sha256("Be kind.\n"),
            False,
        )
        assert [path.name for path in tmp_path.iterdir() if "SYSTEM" in path.name] == ["SYSTEM.md"]

    asyncio.run(scenario())


def test_prompt_set_over_a_missing_file_takes_the_empty_hash(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)

        await call(
            dispatcher, "prompt.set", name="recap", content="Name a lesson.", hash=EMPTY_HASH
        )

        assert (tmp_path / "RECAP.md").read_text(encoding="utf-8") == "Name a lesson."
        read = await call(dispatcher, "prompt.get", name="recap")
        assert (read.content, read.default) == ("Name a lesson.", False)

    asyncio.run(scenario())


def test_prompt_set_with_a_stale_hash_leaves_the_file_alone(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        (tmp_path / "SYSTEM.md").write_text("Be brief.\n", encoding="utf-8")
        read = await call(dispatcher, "prompt.get", name="behavior")
        (tmp_path / "SYSTEM.md").write_text("The agent's version.\n", encoding="utf-8")

        refused = await call(
            dispatcher, "prompt.set", name="behavior", content="Be kind.\n", hash=read.hash
        )
        missing = await call(
            dispatcher, "prompt.set", name="recap", content="Mine.", hash=_sha256("Theirs.")
        )

        assert isinstance(refused, ErrorEnvelope)
        assert (refused.code, refused.retryable) == (ErrorCode.STALE, False)
        assert (tmp_path / "SYSTEM.md").read_text(encoding="utf-8") == "The agent's version.\n"
        assert isinstance(missing, ErrorEnvelope)
        assert missing.code is ErrorCode.STALE
        assert not (tmp_path / "RECAP.md").exists()

    asyncio.run(scenario())


def test_an_app_write_records_a_config_change(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        (tmp_path / "SYSTEM.md").write_text("Be brief.\n", encoding="utf-8")

        await call(
            dispatcher,
            "prompt.set",
            name="behavior",
            content="Be kind.\n",
            hash=_sha256("Be brief.\n"),
        )
        await call(dispatcher, "prompt.set", name="behavior", content="Stale.\n", hash=EMPTY_HASH)

        history = await call(dispatcher, "config.history", limit=10)
        [change] = history.changes
        assert (change.file, change.actor, change.thread_id, change.turn_id) == (
            "SYSTEM.md",
            "app",
            None,
            None,
        )
        assert change.diff == (
            "--- a/SYSTEM.md\n+++ b/SYSTEM.md\n@@ -1 +1 @@\n-Be brief.\n+Be kind.\n"
        )

    asyncio.run(scenario())


def test_config_history_lists_the_newest_first_by_file(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        await call(dispatcher, "prompt.set", name="behavior", content="One.\n", hash=EMPTY_HASH)
        await call(dispatcher, "prompt.set", name="recap", content="Lens.\n", hash=EMPTY_HASH)
        await call(
            dispatcher, "prompt.set", name="behavior", content="Two.\n", hash=_sha256("One.\n")
        )

        every = await call(dispatcher, "config.history", limit=10)
        latest = await call(dispatcher, "config.history", file="SYSTEM.md", limit=1)

        assert [change.file for change in every.changes] == ["SYSTEM.md", "RECAP.md", "SYSTEM.md"]
        assert every.changes[0].at >= every.changes[-1].at
        [change] = latest.changes
        assert change.diff.endswith("-One.\n+Two.\n")

    asyncio.run(scenario())


def test_an_agent_write_in_a_turn_records_its_thread_and_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        content = "---\ndescription: News\n---\nRead the news.\n"
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "routine_write",
                            "args": {"name": "news", "content": content},
                            "id": "routine-write-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="Installed."),
            ]
        )
        runner = LangGraphRunner(instance, model_factory=lambda _: model)
        preparation = runner.prepare_for_turn()
        thread_id, turn_id = uuid4(), uuid4()
        payloads: list[Payload] = []

        async def emit(payload: Payload) -> Event:
            payloads.append(payload)
            return Event(
                sequence=len(payloads),
                thread_id=thread_id,
                turn_id=turn_id,
                payload=payload,
                timestamp=datetime.now(UTC),
            )

        completed = await runner.run(
            PreparedTurnRequest(
                thread_id=thread_id,
                turn_id=turn_id,
                message="Install a news routine.",
                model=preparation.model,
                permission_mode=PermissionMode.FULL_ACCESS,
                system_prompt=SystemPrompt("System prompt"),
            ),
            TurnContext(preparation.budgets, emit),
        )

        assert isinstance(completed, TurnOutcome)
        history = await call(_dispatcher(tmp_path), "config.history", limit=10)
        [change] = history.changes
        assert (change.file, change.actor, change.thread_id, change.turn_id) == (
            "routines/news",
            "agent",
            thread_id,
            turn_id,
        )
        assert change.diff.startswith(
            "--- a/routines/news/ROUTINE.md\n+++ b/routines/news/ROUTINE.md\n"
        )
        assert change.diff.endswith("+Read the news.\n")

    asyncio.run(scenario())


SKILL = "---\nname: planning\ndescription: Plan work.\n---\nWrite a plan.\n"


@pytest.mark.parametrize(
    ("tool", "arguments", "file", "diff_line"),
    [
        (
            "routine_write",
            {"name": "news", "content": "---\ndescription: News\n---\nRead it all.\n"},
            "routines/news",
            "+Read it all.",
        ),
        (
            "routine_set_enabled",
            {"name": "news", "enabled": False},
            "routines/news",
            "+enabled: false",
        ),
        ("routine_delete", {"name": "news"}, "routines/news", "-Read the news."),
        (
            "skill_write",
            {"name": "planning", "content": SKILL},
            "skills/planning",
            "+Write a plan.",
        ),
        ("skill_delete", {"name": "drafting"}, "skills/drafting", "-Write a draft."),
    ],
)
def test_each_instance_tool_records_an_agent_change(
    tmp_path: Path, tool: str, arguments: dict[str, JsonValue], file: str, diff_line: str
) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        drafting = tmp_path / "skills" / "drafting" / "SKILL.md"
        drafting.parent.mkdir(parents=True)
        drafting.write_text(
            "---\nname: drafting\ndescription: Draft.\n---\nWrite a draft.\n", encoding="utf-8"
        )
        thread_id, turn_id = uuid4(), uuid4()
        selected = next(each for each in instance_tools(instance) if each.name == tool)

        await selected.ainvoke(
            arguments, ToolContext(instance=instance, thread_id=thread_id, turn_id=turn_id)
        )

        history = await call(_dispatcher(tmp_path), "config.history", file=file, limit=10)
        [change] = history.changes
        assert (change.actor, change.thread_id, change.turn_id) == ("agent", thread_id, turn_id)
        assert diff_line in change.diff.splitlines()

    asyncio.run(scenario())


def test_the_failure_policy_records_the_routine_it_disables(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News\nenabled: true")
        dispatcher = runtime(
            instance, FakeClock(datetime(2026, 9, 28, tzinfo=UTC)), FailingRunner()
        )
        for _ in range(10):
            await call(dispatcher, "routine.run", name="news")
            await dispatcher.scheduler.drain()

        history = await call(dispatcher, "config.history", file="routines/news", limit=10)

        [change] = history.changes
        assert change.actor == "failure_policy"
        assert "-enabled: true" in change.diff.splitlines()
        assert "+enabled: false" in change.diff.splitlines()

    asyncio.run(scenario())


NEWS = "---\ndescription: News\n---\nRead the news.\n"
CODE_STEP = """from kinby.plugins import tool


@tool(write=False)
def fetch() -> str:
    \"\"\"Fetch the news.\"\"\"
    return "news"
"""


def test_routine_read_returns_routine_md_and_the_directory_hash(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        dispatcher = _dispatcher(tmp_path)

        read = await call(dispatcher, "routine.read", name="news")
        again = await call(dispatcher, "routine.read", name="news")
        (tmp_path / "routines" / "news" / "notes.txt").write_text("A note.", encoding="utf-8")
        with_notes = await call(dispatcher, "routine.read", name="news")
        missing = await call(dispatcher, "routine.read", name="weather")

        assert (read.name, read.content) == ("news", NEWS)
        assert again.hash == read.hash
        assert (with_notes.content, with_notes.hash == read.hash) == (NEWS, False)
        assert isinstance(missing, ErrorEnvelope)
        assert missing.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_routine_write_creates_a_routine_with_a_null_hash(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)

        written = await call(dispatcher, "routine.write", name="news", content=NEWS, hash=None)

        assert (tmp_path / "routines" / "news" / "ROUTINE.md").read_text(encoding="utf-8") == NEWS
        assert written == await call(dispatcher, "routine.read", name="news")
        [summary] = (await call(dispatcher, "routine.list")).routines
        assert (summary.name, summary.enabled) == ("news", True)
        [change] = (
            await call(dispatcher, "config.history", file="routines/news", limit=10)
        ).changes
        assert (change.actor, change.thread_id, change.turn_id) == ("app", None, None)
        assert "+Read the news." in change.diff.splitlines()

    asyncio.run(scenario())


def test_routine_write_replaces_the_routine_read(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        (tmp_path / "routines" / "news" / "run.py").write_text(CODE_STEP, encoding="utf-8")
        dispatcher = _dispatcher(tmp_path)
        read = await call(dispatcher, "routine.read", name="news")
        content = "---\ndescription: News\n---\nRead the headlines.\n"

        written = await call(
            dispatcher, "routine.write", name="news", content=content, hash=read.hash
        )

        assert (written.content, written.hash == read.hash) == (content, False)
        assert (tmp_path / "routines" / "news" / "run.py").read_text(encoding="utf-8") == CODE_STEP

    asyncio.run(scenario())


def test_routine_write_over_a_change_since_the_read_is_stale(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        dispatcher = _dispatcher(tmp_path)
        read = await call(dispatcher, "routine.read", name="news")
        (tmp_path / "routines" / "news" / "run.py").write_text(CODE_STEP, encoding="utf-8")
        mine = "---\ndescription: Mine\n---\nMine.\n"

        stale = await call(dispatcher, "routine.write", name="news", content=mine, hash=read.hash)
        exists = await call(dispatcher, "routine.write", name="news", content=mine, hash=None)

        for refused in (stale, exists):
            assert isinstance(refused, ErrorEnvelope)
            assert (refused.code, refused.retryable) == (ErrorCode.STALE, False)
        assert (tmp_path / "routines" / "news" / "ROUTINE.md").read_text(encoding="utf-8") == NEWS
        assert (await call(dispatcher, "config.history", limit=10)).changes == []

    asyncio.run(scenario())


def test_routine_write_refuses_a_routine_the_loader_refuses(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)

        broken = await call(
            dispatcher,
            "routine.write",
            name="news",
            content="---\n---\nNo description.\n",
            hash=None,
        )
        bad_name = await call(dispatcher, "routine.write", name="../news", content=NEWS, hash=None)

        for refused in (broken, bad_name):
            assert isinstance(refused, ErrorEnvelope)
            assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert "description" in broken.message
        assert not (tmp_path / "routines" / "news").exists()

    asyncio.run(scenario())


def test_routine_set_enabled_toggles_the_enabled_line_without_a_hash(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        path = routine_file(instance, "description: News\nschedule: 0 9 * * *")
        dispatcher = _dispatcher(tmp_path)

        off = await call(dispatcher, "routine.set_enabled", name="news", enabled=False)
        [summary] = (await call(dispatcher, "routine.list")).routines
        on = await call(dispatcher, "routine.set_enabled", name="news", enabled=True)
        missing = await call(dispatcher, "routine.set_enabled", name="weather", enabled=True)

        assert off.content == (
            "---\ndescription: News\nschedule: 0 9 * * *\nenabled: false\n---\nRead the news.\n"
        )
        assert (summary.enabled, summary.next_run) == (False, None)
        assert on == await call(dispatcher, "routine.read", name="news")
        assert path.read_text(encoding="utf-8") == (
            "---\ndescription: News\nschedule: 0 9 * * *\nenabled: true\n---\nRead the news.\n"
        )
        assert isinstance(missing, ErrorEnvelope)
        assert missing.code is ErrorCode.NOT_FOUND
        changes = (await call(dispatcher, "config.history", file="routines/news", limit=10)).changes
        assert [change.actor for change in changes] == ["app", "app"]
        assert "+enabled: true" in changes[0].diff.splitlines()
        assert "+enabled: false" in changes[1].diff.splitlines()

    asyncio.run(scenario())


def test_routine_delete_removes_the_routine_read(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        dispatcher = _dispatcher(tmp_path)
        read = await call(dispatcher, "routine.read", name="news")

        await call(dispatcher, "routine.delete", name="news", hash=read.hash)
        again = await call(dispatcher, "routine.delete", name="news", hash=read.hash)
        missing = await call(dispatcher, "routine.delete", name="weather", hash=EMPTY_HASH)

        assert not (tmp_path / "routines" / "news").exists()
        assert (await call(dispatcher, "routine.list")).routines == []
        assert isinstance(again, ErrorEnvelope)
        assert again.code is ErrorCode.STALE
        assert isinstance(missing, ErrorEnvelope)
        assert missing.code is ErrorCode.NOT_FOUND
        [change] = (
            await call(dispatcher, "config.history", file="routines/news", limit=10)
        ).changes
        assert change.actor == "app"
        assert "-Read the news." in change.diff.splitlines()

    asyncio.run(scenario())


def test_routine_delete_over_a_change_since_the_read_is_stale(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        path = routine_file(instance, "description: News")
        dispatcher = _dispatcher(tmp_path)
        read = await call(dispatcher, "routine.read", name="news")
        path.write_text(NEWS.replace("news", "headlines"), encoding="utf-8")

        refused = await call(dispatcher, "routine.delete", name="news", hash=read.hash)

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.STALE
        assert path.is_file()

    asyncio.run(scenario())


def test_routine_delete_refuses_a_routine_with_pending_deliveries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("SIGNAL_SECRET", "secret")
        instance = instance_at(tmp_path)
        routine_file(instance, "description: Issues\nsignal:\n  secret: SIGNAL_SECRET")
        dispatcher = _dispatcher(tmp_path)
        await dispatcher.scheduler.receive(
            RoutineName("news"),
            Delivery(
                headers={},
                content_type="text/plain",
                body="opened",
                received_at=datetime(2026, 9, 28, tzinfo=UTC),
            ),
            RoutineTrigger.SIGNAL,
        )
        read = await call(dispatcher, "routine.read", name="news")

        refused = await call(dispatcher, "routine.delete", name="news", hash=read.hash)

        assert isinstance(refused, ErrorEnvelope)
        assert (refused.code, refused.retryable) == (ErrorCode.ROUTINE_PENDING, False)
        assert refused.message == 'Routine "news" has 1 pending delivery and cannot be deleted.'
        assert (tmp_path / "routines" / "news").is_dir()

    asyncio.run(scenario())


def test_an_app_write_after_an_agent_change_is_stale(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        dispatcher = _dispatcher(tmp_path)
        tools = {each.name: each for each in instance_tools(instance)}
        context = ToolContext(instance=instance, thread_id=uuid4(), turn_id=uuid4())
        await tools["routine_write"].ainvoke({"name": "news", "content": NEWS}, context)
        read = await call(dispatcher, "routine.read", name="news")

        await tools["routine_set_enabled"].ainvoke({"name": "news", "enabled": False}, context)
        refused = await call(dispatcher, "routine.write", name="news", content=NEWS, hash=read.hash)

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.STALE
        theirs = await call(dispatcher, "routine.read", name="news")
        assert "enabled: false" in theirs.content.splitlines()

    asyncio.run(scenario())
