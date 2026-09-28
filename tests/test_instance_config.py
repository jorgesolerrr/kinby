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
            ("mistral:large-3", "manifest", False),
            ("openai:gpt-5", "shipped", True),
            ("openai:gpt-5-mini", "shipped", True),
            ("openai:gpt-5-nano", "shipped", True),
            ("openai:gpt-5.1", "shipped", True),
            ("openai:gpt-5.2", "shipped", True),
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
