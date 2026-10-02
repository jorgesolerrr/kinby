import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk
from pydantic import JsonValue

from kinby.cli import main
from kinby.contracts import (
    Delivery,
    ErrorCode,
    ErrorEnvelope,
    Event,
    Payload,
    PermissionMode,
    RecreateReason,
    RoutineName,
    RoutineTrigger,
    SystemPrompt,
)
from kinby.core import LangGraphRunner, boot_instance
from kinby.core.dispatcher import Dispatcher, TurnConfig
from kinby.core.errors import RoutineNotFound
from kinby.core.receiver import Receiver
from kinby.core.turns import PreparedTurnRequest, TurnContext, TurnOutcome
from kinby.hub import ControlEndpoint, HttpInstanceControl
from kinby.instance import Serve, load_instance
from kinby.instance.permissions import SHIPPED_BASH_DENY
from kinby.instance.recap import DEFAULT_RECAP_LENS
from kinby.plugins import ToolContext
from kinby.plugins.instance_tools import instance_tools
from tests.fake_package import install_fake_package
from tests.helpers import fixed_permission_ceiling, fixed_turn_preparation, turn_config_stub
from tests.test_contract_server import TOKEN, served_dispatcher
from tests.test_instance_tools import ScriptedModel
from tests.test_receiver import request
from tests.test_routines import instance_at, routine_file
from tests.test_scheduler import FailingRunner, FakeClock, ScriptedRunner, call, runtime

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


def test_a_routine_change_records_the_directory_hash_and_a_delete_records_none(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)

        written = await call(dispatcher, "routine.write", name="news", content=NEWS, hash=None)
        await call(dispatcher, "routine.delete", name="news", hash=written.hash)

        deleted, created = (
            await call(dispatcher, "config.history", file="routines/news", limit=10)
        ).changes
        assert (created.hash, deleted.hash) == (written.hash, None)

    asyncio.run(scenario())


def test_a_log_line_written_before_the_hash_still_reads(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        instance.manifest.state_dir.mkdir(parents=True, exist_ok=True)
        (instance.manifest.state_dir / "config-changes.jsonl").write_text(
            '{"at":"2026-09-28T10:00:00Z","file":"SYSTEM.md","actor":"app",'
            '"thread_id":null,"turn_id":null,"diff":""}\n',
            encoding="utf-8",
        )

        [change] = (await call(_dispatcher(tmp_path), "config.history", limit=10)).changes

        assert (change.file, change.actor, change.hash) == ("SYSTEM.md", "app", None)

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


def test_routine_rename_moves_every_file_to_the_new_name(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        (tmp_path / "routines" / "news" / "run.py").write_text(CODE_STEP, encoding="utf-8")
        (tmp_path / "routines" / "news" / "notes").mkdir()
        (tmp_path / "routines" / "news" / "notes" / "sources.txt").write_text(
            "Wire services.", encoding="utf-8"
        )
        dispatcher = _dispatcher(tmp_path)
        read = await call(dispatcher, "routine.read", name="news")

        renamed = await call(
            dispatcher, "routine.rename", name="news", new_name="headlines", hash=read.hash
        )

        assert (renamed.name, renamed.content, renamed.hash) == ("headlines", NEWS, read.hash)
        assert renamed == await call(dispatcher, "routine.read", name="headlines")
        assert not (tmp_path / "routines" / "news").exists()
        moved = tmp_path / "routines" / "headlines"
        assert {
            path.relative_to(moved).as_posix(): path.read_text(encoding="utf-8")
            for path in moved.rglob("*")
            if path.is_file()
        } == {"ROUTINE.md": NEWS, "run.py": CODE_STEP, "notes/sources.txt": "Wire services."}
        assert [each.name for each in (await call(dispatcher, "routine.list")).routines] == [
            "headlines"
        ]

    asyncio.run(scenario())


def test_routine_rename_ends_the_old_names_history_and_starts_the_new_ones(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        (tmp_path / "routines" / "news" / "run.py").write_text(CODE_STEP, encoding="utf-8")
        dispatcher = _dispatcher(tmp_path)
        read = await call(dispatcher, "routine.read", name="news")

        renamed = await call(
            dispatcher, "routine.rename", name="news", new_name="headlines", hash=read.hash
        )

        [removed] = (
            await call(dispatcher, "config.history", file="routines/news", limit=10)
        ).changes
        [added] = (
            await call(dispatcher, "config.history", file="routines/headlines", limit=10)
        ).changes
        assert removed.at == added.at
        assert (removed.actor, removed.hash) == ("app", None)
        assert (added.actor, added.hash) == ("app", renamed.hash)
        removed_lines = removed.diff.splitlines()
        added_lines = added.diff.splitlines()
        assert {"--- a/routines/news/ROUTINE.md", "--- a/routines/news/run.py"} <= set(
            removed_lines
        )
        assert {"+++ b/routines/headlines/ROUTINE.md", "+++ b/routines/headlines/run.py"} <= set(
            added_lines
        )
        assert "-Read the news." in removed_lines
        assert "+Read the news." in added_lines
        assert [line for line in removed_lines if line[:1] == "+" and line[:3] != "+++"] == []
        assert [line for line in added_lines if line[:1] == "-" and line[:3] != "---"] == []

    asyncio.run(scenario())


def test_routine_rename_refuses_a_stale_hash_and_a_taken_or_invalid_name(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        weather = routine_file(instance, "description: Weather", "Check the sky.", name="weather")
        dispatcher = _dispatcher(tmp_path)
        read = await call(dispatcher, "routine.read", name="news")

        taken = await call(
            dispatcher, "routine.rename", name="news", new_name="weather", hash=read.hash
        )
        invalid = await call(
            dispatcher, "routine.rename", name="news", new_name="../headlines", hash=read.hash
        )
        (tmp_path / "routines" / "news" / "run.py").write_text(CODE_STEP, encoding="utf-8")
        stale = await call(
            dispatcher, "routine.rename", name="news", new_name="headlines", hash=read.hash
        )
        missing = await call(
            dispatcher, "routine.rename", name="sports", new_name="games", hash=EMPTY_HASH
        )

        assert isinstance(missing, ErrorEnvelope)
        assert missing.code is ErrorCode.NOT_FOUND
        assert isinstance(taken, ErrorEnvelope)
        assert (taken.code, taken.fields) == (
            ErrorCode.INVALID_ARGUMENT,
            {"new_name": 'Routine "weather" already exists.'},
        )
        assert isinstance(invalid, ErrorEnvelope)
        assert invalid.code is ErrorCode.INVALID_ARGUMENT
        assert list(invalid.fields or {}) == ["new_name"]
        assert isinstance(stale, ErrorEnvelope)
        assert stale.code is ErrorCode.STALE
        assert sorted(path.name for path in (tmp_path / "routines").iterdir()) == [
            "news",
            "weather",
        ]
        assert (tmp_path / "routines" / "news" / "ROUTINE.md").read_text(encoding="utf-8") == NEWS
        assert weather.read_text(encoding="utf-8") == (
            "---\ndescription: Weather\n---\nCheck the sky.\n"
        )
        assert (await call(dispatcher, "config.history", limit=10)).changes == []

    asyncio.run(scenario())


def test_routine_rename_refuses_a_routine_with_pending_deliveries(
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

        refused = await call(
            dispatcher, "routine.rename", name="news", new_name="headlines", hash=read.hash
        )

        assert isinstance(refused, ErrorEnvelope)
        assert (refused.code, refused.retryable) == (ErrorCode.ROUTINE_PENDING, False)
        assert refused.message == 'Routine "news" has 1 pending delivery and cannot be renamed.'
        assert (tmp_path / "routines" / "news").is_dir()
        assert not (tmp_path / "routines" / "headlines").exists()
        assert (await call(dispatcher, "config.history", limit=10)).changes == []

    asyncio.run(scenario())


def test_a_signal_to_the_old_name_is_refused_as_an_unknown_routine_after_a_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("SIGNAL_SECRET", "token")
        instance = instance_at(tmp_path)
        routine_file(instance, "description: Issues\nsignal:\n  secret: SIGNAL_SECRET")
        dispatcher = _dispatcher(tmp_path)
        read = await call(dispatcher, "routine.read", name="news")
        await call(dispatcher, "routine.rename", name="news", new_name="issues", hash=read.hash)
        receiver = Receiver(Serve("127.0.0.1", 0), dispatcher.scheduler, instance)
        address = await receiver.start()
        bearer = {"Authorization": "Bearer token"}
        try:
            old = await request(address, "POST", "/signals/news", headers=bearer)
            unknown = await request(address, "POST", "/signals/missing", headers=bearer)
            new_status, _ = await request(address, "POST", "/signals/issues", headers=bearer)
        finally:
            await receiver.stop()

        assert old == unknown
        assert (old[0], new_status) == (404, 202)
        with pytest.raises(RoutineNotFound):
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

    asyncio.run(scenario())


def test_the_scheduler_fires_a_renamed_routine_once_under_each_name(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News\nschedule: * * * * *\ncatch_up: true")
        clock = FakeClock(datetime(2026, 9, 28, 8, 0, 30, tzinfo=UTC))
        dispatcher = runtime(instance, clock)
        clock.now = datetime(2026, 9, 28, 8, 1, 10, tzinfo=UTC)
        await dispatcher.scheduler.tick()
        await dispatcher.scheduler.drain()
        read = await call(dispatcher, "routine.read", name="news")

        await call(dispatcher, "routine.rename", name="news", new_name="headlines", hash=read.hash)
        await dispatcher.scheduler.tick()
        clock.now = datetime(2026, 9, 28, 8, 2, 10, tzinfo=UTC)
        await dispatcher.scheduler.tick()
        await dispatcher.scheduler.drain()

        threads = (await call(dispatcher, "thread.list")).threads
        assert sorted(thread.title for thread in threads) == [
            "headlines · 2026-09-28 08:02",
            "news · 2026-09-28 08:01",
        ]

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


MANIFEST = """# Ada's instance
id = "test"
persona_name = "Ada"  # the name she answers to

[models]
# The main model runs every turn.
main = "openai:gpt-5"

[tools]
defaults = false

[routines]
timezone = "Europe/Madrid"

[serve]
listen = "0.0.0.0:8080"
"""

MANIFEST_VALUES: dict[str, JsonValue] = {
    "persona_name": "Ada",
    "models": {"main": "openai:gpt-5", "recap": None, "embed": None},
    "budgets": {"steps": None, "tokens": None, "seconds": None, "usd_per_day": None},
    "routines": {"timezone": "Europe/Madrid"},
    "tools": {"defaults": False, "bash_timeout_seconds": 120},
    "memory": {"recap": "every-turn"},
    "feedback": {"ask": "every-turn"},
}


def _manifest_dispatcher(path: Path):
    dispatcher = _dispatcher(path)
    (path / "kinby.toml").write_text(MANIFEST, encoding="utf-8")
    return dispatcher


def test_manifest_get_returns_the_offered_fields_and_the_hash(tmp_path: Path) -> None:
    async def scenario() -> None:
        read = await call(_manifest_dispatcher(tmp_path), "manifest.get")

        assert read.values.model_dump(mode="json") == MANIFEST_VALUES
        assert read.hash == _sha256(MANIFEST)

    asyncio.run(scenario())


def test_manifest_set_writes_the_offered_fields_and_keeps_the_comments(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _manifest_dispatcher(tmp_path)
        read = await call(dispatcher, "manifest.get")
        values = read.values.model_dump(mode="json")
        values["persona_name"] = None
        values["models"]["main"] = "anthropic:claude-sonnet-5"
        values["budgets"]["steps"] = 20
        values["budgets"]["usd_per_day"] = 2.5
        values["routines"]["timezone"] = "America/Bogota"
        values["tools"]["defaults"] = True
        values["feedback"]["ask"] = "off"

        written = await call(dispatcher, "manifest.set", values=values, hash=read.hash)

        saved = (tmp_path / "kinby.toml").read_text(encoding="utf-8")
        assert saved == (
            "# Ada's instance\n"
            'id = "test"\n'
            "\n"
            "[models]\n"
            "# The main model runs every turn.\n"
            'main = "anthropic:claude-sonnet-5"\n'
            "\n"
            "[tools]\n"
            "defaults = true\n"
            "\n"
            "[routines]\n"
            'timezone = "America/Bogota"\n'
            "\n"
            "[serve]\n"
            'listen = "0.0.0.0:8080"\n'
            "\n"
            "[budgets]\n"
            "steps = 20\n"
            "usd_per_day = 2.5\n"
            "\n"
            "[feedback]\n"
            'ask = "off"\n'
        )
        assert written.values.model_dump(mode="json") == values
        assert written.hash == _sha256(saved)
        again = await call(dispatcher, "manifest.get")
        assert again.values.model_dump(mode="json") == values

    asyncio.run(scenario())


def test_manifest_set_with_the_values_read_leaves_the_file_as_it_was(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _manifest_dispatcher(tmp_path)
        read = await call(dispatcher, "manifest.get")

        await call(
            dispatcher,
            "manifest.set",
            values=read.values.model_dump(mode="json"),
            hash=read.hash,
        )

        assert (tmp_path / "kinby.toml").read_text(encoding="utf-8") == MANIFEST

    asyncio.run(scenario())


def test_manifest_set_with_a_stale_hash_leaves_the_file_alone(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _manifest_dispatcher(tmp_path)
        read = await call(dispatcher, "manifest.get")
        theirs = MANIFEST.replace('persona_name = "Ada"', 'persona_name = "Grace"')
        (tmp_path / "kinby.toml").write_text(theirs, encoding="utf-8")
        values = read.values.model_dump(mode="json")
        values["persona_name"] = "Ida"

        refused = await call(dispatcher, "manifest.set", values=values, hash=read.hash)

        assert isinstance(refused, ErrorEnvelope)
        assert (refused.code, refused.retryable) == (ErrorCode.STALE, False)
        assert (tmp_path / "kinby.toml").read_text(encoding="utf-8") == theirs

    asyncio.run(scenario())


def test_a_manifest_write_records_a_config_change(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _manifest_dispatcher(tmp_path)
        read = await call(dispatcher, "manifest.get")
        values = read.values.model_dump(mode="json")
        values["tools"]["bash_timeout_seconds"] = 300

        await call(dispatcher, "manifest.set", values=values, hash=read.hash)

        history = await call(dispatcher, "config.history", file="kinby.toml", limit=10)
        [change] = history.changes
        assert (change.actor, change.thread_id, change.turn_id) == ("app", None, None)
        assert "+bash_timeout_seconds = 300" in change.diff.splitlines()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("owned", "value"),
    [("id", "other"), ("state_dir", "/elsewhere"), ("serve", {"listen": "0.0.0.0:9000"})],
)
def test_manifest_set_refuses_a_field_the_hub_owns(
    tmp_path: Path, owned: str, value: JsonValue
) -> None:
    async def scenario() -> None:
        dispatcher = _manifest_dispatcher(tmp_path)
        read = await call(dispatcher, "manifest.get")
        values = read.values.model_dump(mode="json")
        values[owned] = value

        refused = await call(dispatcher, "manifest.set", values=values, hash=read.hash)

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert (tmp_path / "kinby.toml").read_text(encoding="utf-8") == MANIFEST

    asyncio.run(scenario())


def test_manifest_set_names_each_invalid_value(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _manifest_dispatcher(tmp_path)
        read = await call(dispatcher, "manifest.get")
        values = read.values.model_dump(mode="json")
        values["models"]["recap"] = "no-provider"
        values["budgets"]["steps"] = 0
        values["routines"]["timezone"] = "Mars/Olympus"

        refused = await call(dispatcher, "manifest.set", values=values, hash=read.hash)

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert refused.fields == {
            "models.recap": "must use provider:model form",
            "budgets.steps": "Input should be greater than 0",
            "routines.timezone": "Unknown IANA time zone: Mars/Olympus",
        }
        assert (tmp_path / "kinby.toml").read_text(encoding="utf-8") == MANIFEST
        assert (await call(dispatcher, "config.history", limit=10)).changes == []

    asyncio.run(scenario())


def test_manifest_set_writes_a_new_price_and_offers_its_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("MISTRAL_API_KEY", "")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    async def scenario() -> None:
        dispatcher = _manifest_dispatcher(tmp_path)
        read = await call(dispatcher, "manifest.get")
        values = read.values.model_dump(mode="json")
        values["models"]["main"] = "mistral:large-3"

        written = await call(
            dispatcher,
            "manifest.set",
            values=values,
            prices={"mistral:large-3": {"input": 2, "output": 6}},
            hash=read.hash,
        )

        assert (
            (tmp_path / "kinby.toml")
            .read_text(encoding="utf-8")
            .endswith('\n[prices."mistral:large-3"]\ninput = 2.0\noutput = 6.0\n')
        )
        assert [
            (choice.model, choice.priced_from, choice.key_set) for choice in written.model_choices
        ] == [
            ("anthropic:claude-fable-5-1", "shipped", False),
            ("anthropic:claude-haiku-4-5", "shipped", False),
            ("anthropic:claude-opus-5", "shipped", False),
            ("anthropic:claude-opus-5-5", "shipped", False),
            ("anthropic:claude-sonnet-4-6", "shipped", False),
            ("anthropic:claude-sonnet-5", "shipped", False),
            ("anthropic:claude-sonnet-5-5", "shipped", False),
            ("mistral:large-3", "manifest", False),
            ("openai:gpt-5", "shipped", True),
            ("openai:gpt-5-mini", "shipped", True),
            ("openai:gpt-5-nano", "shipped", True),
            ("openai:gpt-5-pro", "shipped", True),
            ("openai:gpt-5.1", "shipped", True),
            ("openai:gpt-5.2", "shipped", True),
            ("openai:gpt-5.2-pro", "shipped", True),
            ("openai:gpt-5.4", "shipped", True),
            ("openai:gpt-5.4-mini", "shipped", True),
            ("openai:gpt-5.4-nano", "shipped", True),
            ("openai:gpt-5.4-pro", "shipped", True),
            ("openai:gpt-5.5", "shipped", True),
            ("openai:gpt-5.5-pro", "shipped", True),
            ("openai:gpt-5.6-luna", "shipped", True),
            ("openai:gpt-5.6-sol", "shipped", True),
            ("openai:gpt-5.6-terra", "shipped", True),
            ("openai:gpt-6-astra", "shipped", True),
            ("openai:gpt-6-luna", "shipped", True),
            ("openai:gpt-6-sol", "shipped", True),
        ]

    asyncio.run(scenario())


def test_a_manifest_price_for_a_shipped_model_is_the_one_offered(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _manifest_dispatcher(tmp_path)
        with (tmp_path / "kinby.toml").open("a", encoding="utf-8") as manifest:
            manifest.write('\n[prices."openai:gpt-5"]\ninput = 1.0\noutput = 8.0\n')

        read = await call(dispatcher, "manifest.get")

        [choice] = [choice for choice in read.model_choices if choice.model == "openai:gpt-5"]
        assert choice.priced_from == "manifest"

    asyncio.run(scenario())


def test_a_new_routines_timezone_applies_at_the_next_tick(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News\nschedule: 0 9 * * *")
        clock = FakeClock(datetime(2026, 9, 6, 6, tzinfo=UTC))
        dispatcher = runtime(instance, clock)
        await dispatcher.scheduler.tick()
        read = await call(dispatcher, "manifest.get")
        values = read.values.model_dump(mode="json")
        values["routines"]["timezone"] = "Europe/Madrid"

        await call(dispatcher, "manifest.set", values=values, hash=read.hash)
        await dispatcher.scheduler.tick()

        listed = await call(dispatcher, "routine.list")
        assert listed.routines[0].next_run == datetime(2026, 9, 6, 7, tzinfo=UTC)
        clock.now = datetime(2026, 9, 6, 7, tzinfo=UTC)
        await dispatcher.scheduler.tick()
        assert len((await call(dispatcher, "thread.list")).threads) == 1

    asyncio.run(scenario())


def test_turning_the_default_tools_on_applies_at_the_next_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        model = ScriptedModel([AIMessageChunk(content="One."), AIMessageChunk(content="Two.")])
        runner = LangGraphRunner(instance, model_factory=lambda _: model)

        async def turn() -> set[str]:
            preparation = runner.prepare_for_turn()
            thread_id, turn_id = uuid4(), uuid4()

            async def emit(payload: Payload) -> Event:
                return Event(
                    sequence=1,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    payload=payload,
                    timestamp=datetime.now(UTC),
                )

            await runner.run(
                PreparedTurnRequest(
                    thread_id=thread_id,
                    turn_id=turn_id,
                    message="Hello.",
                    model=preparation.model,
                    permission_mode=PermissionMode.FULL_ACCESS,
                    system_prompt=SystemPrompt("System prompt"),
                ),
                TurnContext(preparation.budgets, emit),
            )
            return {tool.name for tool in model.bound_tools[-1]}

        without = await turn()
        manifest = tmp_path / "kinby.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace("defaults = false", "defaults = true"),
            encoding="utf-8",
        )
        with_defaults = await turn()

        assert "bash" not in without
        assert {"bash", "read", "write", "edit"} <= with_defaults

    asyncio.run(scenario())


PERMISSIONS = (
    'mode = "ask"\n'
    'ceiling = "auto"\n'
    "\n"
    "[tools]\n"
    'bash = "deny"\n'
    "\n"
    "[bash]\n"
    f"deny = ['''{SHIPPED_BASH_DENY[1]}''', '^deploy production$']\n"
    "ask = ['^npm publish']\n"
)


def test_permissions_get_marks_the_shipped_deny_patterns(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        (tmp_path / "permissions.toml").write_text(PERMISSIONS, encoding="utf-8")

        read = await call(dispatcher, "permissions.get")

        assert read.model_dump(mode="json") == {
            "mode": "ask",
            "ceiling": "auto",
            "tools": {"bash": "deny"},
            "bash": {
                "deny": [
                    *({"pattern": pattern, "shipped": True} for pattern in SHIPPED_BASH_DENY),
                    {"pattern": "^deploy production$", "shipped": False},
                ],
                "ask": ["^npm publish"],
            },
            "hash": _sha256(PERMISSIONS),
        }

    asyncio.run(scenario())


def test_permissions_get_without_a_file_reads_the_shipped_policy(tmp_path: Path) -> None:
    async def scenario() -> None:
        read = await call(_dispatcher(tmp_path), "permissions.get")

        assert read.model_dump(mode="json") == {
            "mode": "ask",
            "ceiling": "full-access",
            "tools": {},
            "bash": {
                "deny": [{"pattern": pattern, "shipped": True} for pattern in SHIPPED_BASH_DENY],
                "ask": [],
            },
            "hash": EMPTY_HASH,
        }

    asyncio.run(scenario())


def _permissions(hash: str, **values: JsonValue) -> dict[str, JsonValue]:
    return {
        "mode": "auto",
        "ceiling": "auto",
        "tools": {"bash": "ask", "web_fetch": "allow"},
        "bash": {"deny": ["^deploy production$"], "ask": [r"\bnpm publish\b"]},
        "hash": hash,
    } | values


def test_permissions_set_writes_only_the_instances_own_patterns(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        (tmp_path / "permissions.toml").write_text(PERMISSIONS, encoding="utf-8")

        written = await call(dispatcher, "permissions.set", **_permissions(_sha256(PERMISSIONS)))
        read = await call(dispatcher, "permissions.get")

        file = (tmp_path / "permissions.toml").read_text(encoding="utf-8")
        assert file == (
            'mode = "auto"\n'
            'ceiling = "auto"\n'
            "\n"
            "[tools]\n"
            'bash = "ask"\n'
            'web_fetch = "allow"\n'
            "\n"
            "[bash]\n"
            'deny = ["^deploy production$"]\n'
            'ask = ["\\\\bnpm publish\\\\b"]\n'
        )
        assert written == read
        assert read.model_dump(mode="json") == {
            "mode": "auto",
            "ceiling": "auto",
            "tools": {"bash": "ask", "web_fetch": "allow"},
            "bash": {
                "deny": [
                    *({"pattern": pattern, "shipped": True} for pattern in SHIPPED_BASH_DENY),
                    {"pattern": "^deploy production$", "shipped": False},
                ],
                "ask": [r"\bnpm publish\b"],
            },
            "hash": _sha256(file),
        }
        history = await call(dispatcher, "config.history", file="permissions.toml", limit=10)
        [change] = history.changes
        assert change.actor == "app"
        assert '+mode = "auto"' in change.diff.splitlines()

    asyncio.run(scenario())


HAND_WRITTEN_PERMISSIONS = (
    "# Hand-written by the owner.\n"
    'mode = "auto"\n'
    'ceiling = "full-access"\n'
    "\n"
    "[tools]\n"
    'bash = "deny"\n'
    "\n"
    "[bash]\n"
    "# my own only\n"
    "deny = ['\\bwget\\b']\n"
    "ask = []\n"
)


def test_permissions_set_keeps_the_comments_and_edits_the_tool_rules(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        path = tmp_path / "permissions.toml"
        path.write_text(HAND_WRITTEN_PERMISSIONS, encoding="utf-8")

        await call(
            dispatcher,
            "permissions.set",
            mode="auto",
            ceiling="full-access",
            tools={"edit": "ask"},
            bash={"deny": [r"\bwget\b"], "ask": []},
            hash=_sha256(HAND_WRITTEN_PERMISSIONS),
        )

        assert path.read_text(encoding="utf-8") == (
            "# Hand-written by the owner.\n"
            'mode = "auto"\n'
            'ceiling = "full-access"\n'
            "\n"
            "[tools]\n"
            'edit = "ask"\n'
            "\n"
            "[bash]\n"
            "# my own only\n"
            "deny = ['\\bwget\\b']\n"
            "ask = []\n"
        )

    asyncio.run(scenario())


def test_permissions_set_changes_the_mode_and_leaves_the_comments(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        path = tmp_path / "permissions.toml"
        path.write_text(HAND_WRITTEN_PERMISSIONS, encoding="utf-8")

        await call(
            dispatcher,
            "permissions.set",
            mode="read-only",
            ceiling="full-access",
            tools={"bash": "deny"},
            bash={"deny": [r"\bwget\b"], "ask": []},
            hash=_sha256(HAND_WRITTEN_PERMISSIONS),
        )

        assert path.read_text(encoding="utf-8") == HAND_WRITTEN_PERMISSIONS.replace(
            'mode = "auto"', 'mode = "read-only"'
        )

    asyncio.run(scenario())


def test_permissions_set_without_a_file_writes_one(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)

        written = await call(dispatcher, "permissions.set", **_permissions(EMPTY_HASH))
        read = await call(dispatcher, "permissions.get")

        assert (tmp_path / "permissions.toml").read_text(encoding="utf-8") == (
            'mode = "auto"\n'
            'ceiling = "auto"\n'
            "\n"
            "[tools]\n"
            'bash = "ask"\n'
            'web_fetch = "allow"\n'
            "\n"
            "[bash]\n"
            'deny = ["^deploy production$"]\n'
            'ask = ["\\\\bnpm publish\\\\b"]\n'
        )
        assert written == read

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("values", "fields"),
    [
        ({"mode": "full-access"}, {"mode": "full-access is above the ceiling, auto."}),
        (
            {"bash": {"deny": ["^ok$", "(unclosed"], "ask": ["[z-a]"]}},
            {
                "bash.deny.1": "invalid regex: missing ), unterminated subpattern at position 0",
                "bash.ask.0": "invalid regex: bad character range z-a at position 1",
            },
        ),
    ],
    ids=["mode-above-ceiling", "bad-regex"],
)
def test_permissions_set_refuses_invalid_values_and_leaves_the_file(
    tmp_path: Path, values: dict[str, JsonValue], fields: dict[str, str]
) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        (tmp_path / "permissions.toml").write_text(PERMISSIONS, encoding="utf-8")

        refused = await call(
            dispatcher, "permissions.set", **_permissions(_sha256(PERMISSIONS), **values)
        )

        assert isinstance(refused, ErrorEnvelope)
        assert (refused.code, refused.fields) == (ErrorCode.INVALID_ARGUMENT, fields)
        assert (tmp_path / "permissions.toml").read_text(encoding="utf-8") == PERMISSIONS

    asyncio.run(scenario())


def test_permissions_set_with_a_stale_hash_leaves_the_file(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        read = await call(dispatcher, "permissions.get")
        (tmp_path / "permissions.toml").write_text(PERMISSIONS, encoding="utf-8")

        refused = await call(dispatcher, "permissions.set", **_permissions(read.hash))

        assert isinstance(refused, ErrorEnvelope)
        assert (refused.code, refused.retryable) == (ErrorCode.STALE, False)
        assert (tmp_path / "permissions.toml").read_text(encoding="utf-8") == PERMISSIONS
        history = await call(dispatcher, "config.history", limit=10)
        assert history.changes == []

    asyncio.run(scenario())


@pytest.fixture
def writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An instance of the fake package ``writer``, whose config is a tone and a token."""
    package = install_fake_package(tmp_path / "site")
    monkeypatch.syspath_prepend(str(package.site))
    monkeypatch.setattr(
        "kinby.core.runtime.turn_config",
        turn_config_stub(
            lambda: TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, ScriptedRunner())
        ),
    )
    path = tmp_path / "instance"
    assert main(["init", str(path), "--package", "writer", "--model", "openai:gpt-5"]) == 0
    return path


@asynccontextmanager
async def _booted(path: Path) -> AsyncIterator[Dispatcher]:
    runtime = await boot_instance(load_instance(path))
    try:
        yield runtime.dispatcher
    finally:
        await runtime.stop_after_running_routine()


WRITER_CONFIG = "tone: plain\ntoken: EDITOR_TOKEN\n"


def test_package_config_get_returns_the_packages_schema_and_the_file(writer: Path) -> None:
    async def scenario() -> None:
        async with _booted(writer) as dispatcher:
            read = await call(dispatcher, "package.config.get")

        assert read.model_dump(mode="json") == {
            "schema": {
                "additionalProperties": False,
                "properties": {
                    "tone": {"enum": ["plain", "formal"], "title": "Tone", "type": "string"},
                    "token": {"title": "Token", "type": "string"},
                },
                "required": ["tone", "token"],
                "title": "WriterConfig",
                "type": "object",
            },
            "values": {"tone": "plain", "token": "EDITOR_TOKEN"},
            "hash": _sha256(WRITER_CONFIG),
        }

    asyncio.run(scenario())


def test_package_config_set_writes_the_validated_values(writer: Path) -> None:
    async def scenario() -> None:
        async with _booted(writer) as dispatcher:
            written = await call(
                dispatcher,
                "package.config.set",
                values={"token": "EDITOR_TOKEN", "tone": "formal"},
                hash=_sha256(WRITER_CONFIG),
            )
            read = await call(dispatcher, "package.config.get")
            history = await call(dispatcher, "config.history", file="package.yaml", limit=10)

        file = (writer / "package.yaml").read_text(encoding="utf-8")
        assert file == "tone: formal\ntoken: EDITOR_TOKEN\n"
        assert written == read
        assert (read.values, read.hash) == (
            {"tone": "formal", "token": "EDITOR_TOKEN"},
            _sha256(file),
        )
        [change] = history.changes
        assert change.actor == "app"
        assert change.diff.endswith("-tone: plain\n+tone: formal\n token: EDITOR_TOKEN\n")

    asyncio.run(scenario())


def test_package_config_set_puts_the_validators_errors_in_fields(writer: Path) -> None:
    async def scenario() -> None:
        async with _booted(writer) as dispatcher:
            refused = await call(
                dispatcher,
                "package.config.set",
                values={"tone": "shouty", "token": "GITHUB_TOKEN", "tonne": "formal"},
                hash=_sha256(WRITER_CONFIG),
            )

        assert isinstance(refused, ErrorEnvelope)
        assert (refused.code, refused.fields) == (
            ErrorCode.INVALID_ARGUMENT,
            {
                "tone": "Input should be 'plain' or 'formal'",
                "token": '"GITHUB_TOKEN" is not a secret field this package declares.',
                "tonne": "Extra inputs are not permitted",
            },
        )
        assert (writer / "package.yaml").read_text(encoding="utf-8") == WRITER_CONFIG

    asyncio.run(scenario())


def test_package_config_set_with_a_stale_hash_leaves_the_file(writer: Path) -> None:
    async def scenario() -> None:
        async with _booted(writer) as dispatcher:
            (writer / "package.yaml").write_text("tone: formal\ntoken: EDITOR_TOKEN\n")
            refused = await call(
                dispatcher,
                "package.config.set",
                values={"tone": "plain", "token": "EDITOR_TOKEN"},
                hash=_sha256(WRITER_CONFIG),
            )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.STALE
        assert (writer / "package.yaml").read_text() == "tone: formal\ntoken: EDITOR_TOKEN\n"

    asyncio.run(scenario())


def test_a_vanilla_instance_has_no_package_config(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)

        read = await call(dispatcher, "package.config.get")
        written = await call(dispatcher, "package.config.set", values={}, hash=EMPTY_HASH)
        probed = await call(dispatcher, "instance.probe")

        assert isinstance(read, ErrorEnvelope)
        assert isinstance(written, ErrorEnvelope)
        assert (read.code, written.code) == (ErrorCode.NOT_FOUND, ErrorCode.NOT_FOUND)
        assert not (tmp_path / "package.yaml").exists()
        assert probed.restart_reasons == []

    asyncio.run(scenario())


def test_the_probe_asks_for_a_restart_once_the_package_config_changed(writer: Path) -> None:
    async def scenario() -> None:
        async with _booted(writer) as dispatcher:
            before = await call(dispatcher, "instance.probe")
            await call(
                dispatcher,
                "package.config.set",
                values={"tone": "formal", "token": "EDITOR_TOKEN"},
                hash=_sha256(WRITER_CONFIG),
            )
            after = await call(dispatcher, "instance.probe")
            (writer / "package.yaml").write_text(WRITER_CONFIG, encoding="utf-8")
            restored = await call(dispatcher, "instance.probe")

        assert before.restart_reasons == []
        assert after.restart_reasons == ["package_config"]
        assert restored.restart_reasons == []

    asyncio.run(scenario())


def test_the_hub_reads_the_restart_reasons_over_the_control_route(writer: Path) -> None:
    async def scenario() -> list[RecreateReason]:
        async with _booted(writer) as dispatcher, served_dispatcher(dispatcher) as address:
            (writer / "package.yaml").write_text("tone: formal\ntoken: EDITOR_TOKEN\n")
            return await HttpInstanceControl().restart_reasons(
                ControlEndpoint(address=f"http://{address.host}:{address.port}", token=TOKEN)
            )

    assert asyncio.run(scenario()) == [RecreateReason.PACKAGE_CONFIG]
