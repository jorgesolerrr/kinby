import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Self
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.tools import StructuredTool

from kinby.contracts import (
    ApprovalRequested,
    Delivery,
    Event,
    GateDecider,
    GateOutcome,
    Payload,
    PermissionMode,
    RoutineName,
    RoutineOrigin,
    RoutineTrigger,
    SystemPrompt,
    ToolCall,
    ToolGated,
    ToolResult,
)
from kinby.core import LangGraphRunner
from kinby.core.turns import (
    ApprovalDecision,
    ParkedTurn,
    PreparedTurnRequest,
    TurnContext,
    TurnOutcome,
)
from kinby.instance import load_instance
from kinby.plugins import ToolContext
from kinby.plugins.instance_tools import instance_tools
from tests.test_routines import instance_at
from tests.test_scheduler import FakeClock, call, runtime


class ScriptedModel:
    def __init__(self, responses: Sequence[AIMessageChunk]) -> None:
        self._responses = iter(responses)
        self.bound_tools: list[tuple[StructuredTool, ...]] = []

    def bind_tools(self, tools: Sequence[StructuredTool]) -> Self:
        self.bound_tools.append(tuple(tools))
        return self

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        yield next(self._responses)


def test_routine_write_installs_an_armed_routine(tmp_path: Path) -> None:
    async def scenario() -> None:
        manifest = tmp_path / "kinby.toml"
        instance_at(tmp_path)
        with manifest.open("a", encoding="utf-8") as file:
            file.write('[routines]\ntimezone = "Europe/Madrid"\n')
        instance = load_instance(tmp_path)
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 8, 8, tzinfo=UTC)))
        assert dispatcher.scheduler is not None
        write = next(tool for tool in instance_tools(instance) if tool.name == "routine_write")

        result = await write.ainvoke(
            {
                "name": "morning-news",
                "content": (
                    "---\ndescription: Morning news\nschedule: 0 9 * * *\n---\nRead the news.\n"
                ),
            },
            ToolContext(instance=instance, thread_id=uuid4()),
        )

        target = tmp_path / "routines" / "morning-news" / "ROUTINE.md"
        assert target.read_text(encoding="utf-8").endswith("Read the news.\n")
        assert "Wrote routines/morning-news/ROUTINE.md." in result
        assert "Next firing:" in result
        assert "+02:00" in result

        await dispatcher.scheduler.tick()
        listed = await call(dispatcher, "routine.list")
        assert listed.routines[0].name == "morning-news"
        assert listed.routines[0].next_run is not None

    asyncio.run(scenario())


def test_routine_write_rejects_a_name_with_a_slash_without_changes(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        original = tmp_path / "routines" / "daily" / "ROUTINE.md"
        original.parent.mkdir(parents=True)
        original.write_text("original", encoding="utf-8")
        write = next(tool for tool in instance_tools(instance) if tool.name == "routine_write")

        with pytest.raises(ValueError, match="letters, digits, hyphens, and underscores"):
            await write.ainvoke(
                {
                    "name": "daily/news",
                    "content": "---\ndescription: News\n---\nRead the news.\n",
                },
                ToolContext(instance=instance, thread_id=uuid4()),
            )

        assert original.read_text(encoding="utf-8") == "original"
        assert not (tmp_path / "routines" / "daily" / "news").exists()

    asyncio.run(scenario())


def test_routine_write_rejects_two_code_tools_without_changes(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        target = tmp_path / "routines" / "news"
        target.mkdir(parents=True)
        routine_path = target / "ROUTINE.md"
        code_path = target / "run.py"
        routine_path.write_text("original routine", encoding="utf-8")
        code_path.write_text("original code", encoding="utf-8")
        write = next(tool for tool in instance_tools(instance) if tool.name == "routine_write")
        two_tools = '''from kinby.plugins import tool
@tool(write=False)
def first() -> str:
    """Return the first result."""
    return "first"
@tool(write=False)
def second() -> str:
    """Return the second result."""
    return "second"
'''

        with pytest.raises(ValueError, match=r"run\.py must define exactly one tool"):
            await write.ainvoke(
                {
                    "name": "news",
                    "content": "---\ndescription: New news\n---\nRead new news.\n",
                    "code": two_tools,
                },
                ToolContext(instance=instance, thread_id=uuid4()),
            )

        assert routine_path.read_text(encoding="utf-8") == "original routine"
        assert code_path.read_text(encoding="utf-8") == "original code"

    asyncio.run(scenario())


def test_routine_rewrite_without_code_keeps_run_py(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        target = tmp_path / "routines" / "news"
        target.mkdir(parents=True)
        (target / "ROUTINE.md").write_text(
            "---\ndescription: Old news\n---\nRead old news.\n",
            encoding="utf-8",
        )
        code = '''from kinby.plugins import tool
@tool(write=False)
def fetch() -> str:
    """Fetch the news."""
    return "news"
'''
        (target / "run.py").write_text(code, encoding="utf-8")
        assets = target / "assets"
        assets.mkdir()
        (assets / "prompt.txt").write_text("supporting prompt", encoding="utf-8")
        write = next(tool for tool in instance_tools(instance) if tool.name == "routine_write")

        await write.ainvoke(
            {
                "name": "news",
                "content": "---\ndescription: New news\n---\nRead new news.\n",
            },
            ToolContext(instance=instance, thread_id=uuid4()),
        )

        assert (target / "ROUTINE.md").read_text(encoding="utf-8").endswith("Read new news.\n")
        assert (target / "run.py").read_text(encoding="utf-8") == code
        assert (target / "assets" / "prompt.txt").read_text(encoding="utf-8") == "supporting prompt"

    asyncio.run(scenario())


def test_routine_list_reports_state_pending_deliveries_and_broken_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("SIGNAL_SECRET", "secret")
        instance = instance_at(tmp_path)
        digest = tmp_path / "routines" / "digest" / "ROUTINE.md"
        digest.parent.mkdir(parents=True)
        digest.write_text(
            "---\ndescription: Digest\nenabled: false\n---\nMake a digest.\n",
            encoding="utf-8",
        )
        news = tmp_path / "routines" / "news" / "ROUTINE.md"
        news.parent.mkdir(parents=True)
        news.write_text(
            "---\n"
            "description: Issues\n"
            "schedule: 0 9 * * *\n"
            "signal:\n"
            "  secret: SIGNAL_SECRET\n"
            "---\n"
            "Read issues.\n",
            encoding="utf-8",
        )
        broken = tmp_path / "routines" / "broken" / "ROUTINE.md"
        broken.parent.mkdir(parents=True)
        broken.write_text("---\nenabled: true\n---\nBroken.\n", encoding="utf-8")
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 8, 8, tzinfo=UTC)))
        assert dispatcher.scheduler is not None
        for body in ("opened", "labeled"):
            await dispatcher.scheduler.receive(
                RoutineName("news"),
                Delivery(
                    headers={},
                    content_type="text/plain",
                    body=body,
                    received_at=datetime(2026, 9, 8, 8, tzinfo=UTC),
                ),
                RoutineTrigger.SIGNAL,
            )
        list_tool = next(tool for tool in instance_tools(instance) if tool.name == "routine_list")

        result = json.loads(
            await list_tool.ainvoke(
                {},
                ToolContext(instance=instance, thread_id=uuid4()),
            )
        )

        assert [routine["name"] for routine in result] == ["broken", "digest", "news"]
        assert "description" in result[0]["warning"]
        assert result[1] == {
            "name": "digest",
            "description": "Digest",
            "schedule": None,
            "enabled": False,
            "mode": "ask",
            "signal": False,
            "pending": 0,
        }
        assert result[2] == {
            "name": "news",
            "description": "Issues",
            "schedule": "0 9 * * *",
            "enabled": True,
            "mode": "ask",
            "signal": True,
            "pending": 2,
        }

    asyncio.run(scenario())


def test_routine_read_returns_markdown_and_code(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        target = tmp_path / "routines" / "news"
        target.mkdir(parents=True)
        content = "---\ndescription: News\n---\nRead the news.\n"
        code = "def fetch() -> str:\n    return 'news'\n"
        (target / "ROUTINE.md").write_text(content, encoding="utf-8")
        (target / "run.py").write_text(code, encoding="utf-8")
        read = next(tool for tool in instance_tools(instance) if tool.name == "routine_read")

        result = json.loads(
            await read.ainvoke(
                {"name": "news"},
                ToolContext(instance=instance, thread_id=uuid4()),
            )
        )

        assert result == {"ROUTINE.md": content, "run.py": code}

    asyncio.run(scenario())


def test_approved_routine_write_runs_through_the_gate(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        content = "---\ndescription: Morning news\n---\nRead the news.\n"
        arguments = {"name": "morning-news", "content": content}
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "routine_write",
                            "args": arguments,
                            "id": "routine-write-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="The routine is installed."),
            ]
        )
        runner = LangGraphRunner(instance, model_factory=lambda _: model)
        preparation = runner.prepare_for_turn()
        thread_id = uuid4()
        turn_id = uuid4()
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

        turn = PreparedTurnRequest(
            thread_id=thread_id,
            turn_id=turn_id,
            message="Install a morning news routine.",
            model=preparation.model,
            permission_mode=PermissionMode.ASK,
            system_prompt=SystemPrompt("System prompt"),
        )
        context = TurnContext(preparation.budgets, emit)

        parked = await runner.run(turn, context)

        assert isinstance(parked, ParkedTurn)
        assert {tool.name for tool in model.bound_tools[0]} >= {
            "routine_list",
            "routine_read",
            "routine_write",
        }
        approval = next(payload for payload in payloads if isinstance(payload, ApprovalRequested))
        assert approval.name == "routine_write"
        assert approval.arguments == arguments
        assert not (tmp_path / "routines" / "morning-news").exists()

        completed = await runner.resume(turn, ApprovalDecision.APPROVE, context)

        assert isinstance(completed, TurnOutcome)
        assert (tmp_path / "routines" / "morning-news" / "ROUTINE.md").read_text(
            encoding="utf-8"
        ) == content
        tool_events = [
            payload
            for payload in payloads
            if isinstance(payload, ToolCall | ToolGated | ToolResult)
        ]
        assert tool_events[:2] == [
            ToolCall(
                call_id="routine-write-1",
                name="routine_write",
                arguments=arguments,
                write=True,
            ),
            ToolGated(
                call_id="routine-write-1",
                name="routine_write",
                action=GateOutcome.ALLOW,
                rule="mode.ask.write",
                decided_by=GateDecider.USER,
            ),
        ]
        result = tool_events[2]
        assert isinstance(result, ToolResult)
        assert not result.error
        assert result.output == "Wrote routines/morning-news/ROUTINE.md."

    asyncio.run(scenario())


def test_denied_routine_write_leaves_the_instance_untouched(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        content = "---\ndescription: Morning news\n---\nRead the news.\n"
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "routine_write",
                            "args": {"name": "morning-news", "content": content},
                            "id": "routine-write-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="I left the routine unchanged."),
            ]
        )
        runner = LangGraphRunner(instance, model_factory=lambda _: model)
        preparation = runner.prepare_for_turn()
        thread_id = uuid4()
        turn_id = uuid4()
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

        turn = PreparedTurnRequest(
            thread_id=thread_id,
            turn_id=turn_id,
            message="Install a morning news routine.",
            model=preparation.model,
            permission_mode=PermissionMode.ASK,
            system_prompt=SystemPrompt("System prompt"),
        )
        context = TurnContext(preparation.budgets, emit)

        assert isinstance(await runner.run(turn, context), ParkedTurn)
        assert isinstance(
            await runner.resume(turn, ApprovalDecision.DENY, context),
            TurnOutcome,
        )

        assert not (tmp_path / "routines" / "morning-news").exists()
        gated = next(payload for payload in payloads if isinstance(payload, ToolGated))
        assert gated.action is GateOutcome.DENY
        assert gated.decided_by is GateDecider.USER
        result = next(payload for payload in payloads if isinstance(payload, ToolResult))
        assert result.error

    asyncio.run(scenario())


def test_full_access_writes_a_routine_without_approval(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        content = "---\ndescription: Morning news\n---\nRead the news.\n"
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "routine_write",
                            "args": {"name": "morning-news", "content": content},
                            "id": "routine-write-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="The routine is installed."),
            ]
        )
        runner = LangGraphRunner(instance, model_factory=lambda _: model)
        preparation = runner.prepare_for_turn()
        thread_id = uuid4()
        turn_id = uuid4()
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
                message="Install a morning news routine.",
                model=preparation.model,
                permission_mode=PermissionMode.FULL_ACCESS,
                system_prompt=SystemPrompt("System prompt"),
            ),
            TurnContext(preparation.budgets, emit),
        )

        assert isinstance(completed, TurnOutcome)
        assert not any(isinstance(payload, ApprovalRequested) for payload in payloads)
        assert (tmp_path / "routines" / "morning-news" / "ROUTINE.md").read_text(
            encoding="utf-8"
        ) == content
        gated = next(payload for payload in payloads if isinstance(payload, ToolGated))
        assert gated.action is GateOutcome.ALLOW
        assert gated.decided_by is GateDecider.POLICY

    asyncio.run(scenario())


@pytest.mark.parametrize("trigger", [RoutineTrigger.MANUAL, RoutineTrigger.SIGNAL])
def test_routine_and_signal_turns_have_every_routine_instance_tool(
    tmp_path: Path,
    trigger: RoutineTrigger,
) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        target = tmp_path / "routines" / "news" / "ROUTINE.md"
        target.parent.mkdir(parents=True)
        target.write_text(
            "---\ndescription: News\n---\nRead the news.\n",
            encoding="utf-8",
        )
        model = ScriptedModel([AIMessageChunk(content="Done")])
        runner = LangGraphRunner(instance, model_factory=lambda _: model)
        preparation = runner.prepare_for_turn()
        thread_id = uuid4()
        turn_id = uuid4()

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
                message="",
                model=preparation.model,
                permission_mode=PermissionMode.ASK,
                system_prompt=SystemPrompt("System prompt"),
                origin=RoutineOrigin(
                    name=RoutineName("news"),
                    trigger=trigger,
                ),
            ),
            TurnContext(preparation.budgets, emit),
        )

        assert {tool.name for tool in model.bound_tools[0]} >= {
            "routine_list",
            "routine_read",
            "routine_write",
        }

    asyncio.run(scenario())


def test_invalid_routine_write_becomes_a_tool_error(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        target = tmp_path / "routines" / "news" / "ROUTINE.md"
        target.parent.mkdir(parents=True)
        target.write_text(
            "---\ndescription: Original\n---\nOriginal.\n",
            encoding="utf-8",
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "routine_write",
                            "args": {
                                "name": "news",
                                "content": "---\nenabled: true\n---\nBroken.\n",
                            },
                            "id": "routine-write-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="The routine was not changed."),
            ]
        )
        runner = LangGraphRunner(instance, model_factory=lambda _: model)
        preparation = runner.prepare_for_turn()
        thread_id = uuid4()
        turn_id = uuid4()
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
                message="Change the news routine.",
                model=preparation.model,
                permission_mode=PermissionMode.FULL_ACCESS,
                system_prompt=SystemPrompt("System prompt"),
            ),
            TurnContext(preparation.budgets, emit),
        )

        assert isinstance(completed, TurnOutcome)
        result = next(payload for payload in payloads if isinstance(payload, ToolResult))
        assert result.error
        assert "description" in result.output
        assert target.read_text(encoding="utf-8").endswith("Original.\n")

    asyncio.run(scenario())


def test_routine_write_reports_signal_path_and_disabled_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("SIGNAL_SECRET", "secret")
        instance_at(tmp_path)
        with (tmp_path / "kinby.toml").open("a", encoding="utf-8") as manifest:
            manifest.write('[serve]\nlisten = "127.0.0.1:8484"\n')
        instance = load_instance(tmp_path)
        write = next(tool for tool in instance_tools(instance) if tool.name == "routine_write")

        result = await write.ainvoke(
            {
                "name": "issues",
                "content": (
                    "---\n"
                    "description: Issues\n"
                    "enabled: false\n"
                    "signal:\n"
                    "  secret: SIGNAL_SECRET\n"
                    "---\n"
                    "Read issues.\n"
                ),
            },
            ToolContext(instance=instance, thread_id=uuid4()),
        )

        assert result == (
            "Wrote routines/issues/ROUTINE.md. Signal path: /signals/issues. Status: disabled."
        )

    asyncio.run(scenario())


def test_routine_rewrite_with_code_replaces_run_py(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        target = tmp_path / "routines" / "news"
        target.mkdir(parents=True)
        (target / "ROUTINE.md").write_text(
            "---\ndescription: Old news\n---\nRead old news.\n",
            encoding="utf-8",
        )
        (target / "run.py").write_text("old code", encoding="utf-8")
        replacement = '''from kinby.plugins import tool
@tool(write=False)
def fetch() -> str:
    """Fetch the replacement news."""
    return "replacement"
'''
        write = next(tool for tool in instance_tools(instance) if tool.name == "routine_write")

        await write.ainvoke(
            {
                "name": "news",
                "content": "---\ndescription: New news\n---\nRead new news.\n",
                "code": replacement,
            },
            ToolContext(instance=instance, thread_id=uuid4()),
        )

        assert (target / "run.py").read_text(encoding="utf-8") == replacement

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("content", "code", "message"),
    [
        (
            "---\ndescription: News\nschedule: not a cron\n---\nRead news.\n",
            None,
            "five cron fields",
        ),
        ("---\nenabled: true\n---\nRead news.\n", None, "description"),
        (
            "---\ndescription: News\nrun: shared\n---\nRead news.\n",
            '''from kinby.plugins import tool
@tool(write=False)
def fetch() -> str:
    """Fetch news."""
    return "news"
''',
            "either run or run.py",
        ),
    ],
)
def test_invalid_routine_write_keeps_the_existing_directory(
    tmp_path: Path,
    content: str,
    code: str | None,
    message: str,
) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        target = tmp_path / "routines" / "news"
        target.mkdir(parents=True)
        original = "---\ndescription: Original\n---\nOriginal.\n"
        (target / "ROUTINE.md").write_text(original, encoding="utf-8")
        write = next(tool for tool in instance_tools(instance) if tool.name == "routine_write")
        arguments = {"name": "news", "content": content}
        if code is not None:
            arguments["code"] = code

        with pytest.raises(ValueError, match=message):
            await write.ainvoke(
                arguments,
                ToolContext(instance=instance, thread_id=uuid4()),
            )

        assert (target / "ROUTINE.md").read_text(encoding="utf-8") == original
        assert not (target / "run.py").exists()

    asyncio.run(scenario())


def test_routine_read_rejects_an_unknown_name(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        read = next(tool for tool in instance_tools(instance) if tool.name == "routine_read")

        with pytest.raises(LookupError, match='Routine "missing" was not found'):
            await read.ainvoke(
                {"name": "missing"},
                ToolContext(instance=instance, thread_id=uuid4()),
            )

    asyncio.run(scenario())


def test_routine_instance_tool_metadata(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    tools = {tool.name: tool for tool in instance_tools(instance)}

    assert set(tools) == {"routine_list", "routine_read", "routine_write"}
    assert not tools["routine_list"].write
    assert not tools["routine_read"].write
    assert tools["routine_write"].write
    assert all(not tool.paths for tool in tools.values())
