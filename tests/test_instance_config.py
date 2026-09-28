import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk
from pydantic import JsonValue

from kinby.contracts import (
    ErrorCode,
    ErrorEnvelope,
    Event,
    Payload,
    PermissionMode,
    SystemPrompt,
)
from kinby.core import LangGraphRunner
from kinby.core.turns import PreparedTurnRequest, TurnContext, TurnOutcome
from kinby.instance.permissions import SHIPPED_BASH_DENY
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
