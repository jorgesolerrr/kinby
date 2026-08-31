import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.tools import StructuredTool

from kinby.contracts import (
    ApprovalRequested,
    Event,
    MessageDelta,
    Payload,
    ToolCall,
    ToolResult,
)
from kinby.core import LangGraphRunner
from kinby.core.turns import ParkedTurn, TurnOutcome, TurnRequest, TurnResult
from kinby.instance import Instance, load_instance
from kinby.instance.permissions import PermissionsError

_MODEL = "openai:gpt-5"


class ScriptedModel:
    def __init__(self, responses: Sequence[AIMessageChunk]) -> None:
        self._responses = iter(responses)

    def bind_tools(self, tools: Sequence[StructuredTool]) -> ScriptedModel:
        return self

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        yield next(self._responses)


def _instance(tmp_path: Path, *, defaults: bool = False) -> Instance:
    instance_path = tmp_path / "instance"
    instance_path.mkdir()
    (instance_path / "tools").mkdir()
    (instance_path / "workspace").mkdir()
    tools = "" if defaults else "\n[tools]\ndefaults = false\n"
    (instance_path / "kinby.toml").write_text(
        f'id = "test"\n\n[models]\nmain = "{_MODEL}"\n{tools}',
        encoding="utf-8",
    )
    return load_instance(instance_path)


async def _run(
    instance: Instance,
    model: ScriptedModel,
) -> tuple[TurnResult, list[Payload]]:
    runner = LangGraphRunner(
        instance,
        model_factory=lambda _: model,
    )
    return await _run_turn(runner)


async def _run_turn(runner: LangGraphRunner) -> tuple[TurnResult, list[Payload]]:
    runner.prepare_for_turn()
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

    result = await runner.run(
        TurnRequest(
            thread_id=thread_id,
            turn_id=turn_id,
            message="Remember this",
            model=_MODEL,
        ),
        emit,
    )
    return result, payloads


def test_ask_rule_parks_with_the_tool_call_details(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        (instance.path / "tools" / "write_note.py").write_text(
            """from kinby.plugins import tool

@tool(write=True)
def write_note(note: str) -> str:
    \"\"\"Write one note.\"\"\"
    return note
""",
            encoding="utf-8",
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_note",
                            "args": {"note": "remember me"},
                            "id": "write-1",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        )

        result, payloads = await _run(instance, model)

        assert isinstance(result, ParkedTurn)
        approval = next(payload for payload in payloads if isinstance(payload, ApprovalRequested))
        assert approval == ApprovalRequested(
            approval_id=approval.approval_id,
            name="write_note",
            arguments={"note": "remember me"},
            rule="mode.ask.write",
        )

    asyncio.run(scenario())


def test_allow_rule_runs_a_read_only_tool_without_approval(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        (instance.path / "tools" / "weather.py").write_text(
            """from kinby.plugins import tool

@tool(write=False)
def weather(city: str) -> str:
    \"\"\"Return the weather for one city.\"\"\"
    return f\"Clear in {city}\"
""",
            encoding="utf-8",
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "weather",
                            "args": {"city": "Quito"},
                            "id": "weather-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="Clear skies"),
            ]
        )

        result, payloads = await _run(instance, model)

        assert isinstance(result, TurnOutcome)
        assert not any(isinstance(payload, ApprovalRequested) for payload in payloads)
        assert [payload for payload in payloads if isinstance(payload, ToolCall | ToolResult)] == [
            ToolCall(call_id="weather-1", name="weather", arguments={"city": "Quito"}),
            ToolResult(
                call_id="weather-1",
                name="weather",
                output="Clear in Quito",
                error=False,
            ),
        ]

    asyncio.run(scenario())


def test_deny_rule_returns_an_error_and_the_turn_continues(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        marker = instance.manifest.workspace.path / "ran.txt"
        (instance.path / "tools" / "write_note.py").write_text(
            """from kinby.plugins import ToolContext, tool

@tool(write=True)
def write_note(note: str, context: ToolContext) -> str:
    \"\"\"Write one note.\"\"\"
    (context.workspace / "ran.txt").write_text(note, encoding="utf-8")
    return note
""",
            encoding="utf-8",
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_note",
                            "args": {"note": "remember me"},
                            "id": "write-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="I will not write it."),
            ]
        )
        (instance.path / "permissions.toml").write_text(
            'mode = "read-only"\n',
            encoding="utf-8",
        )

        result, payloads = await _run(instance, model)

        assert isinstance(result, TurnOutcome)
        assert not marker.exists()
        assert next(
            payload for payload in payloads if isinstance(payload, ToolResult)
        ) == ToolResult(
            call_id="write-1",
            name="write_note",
            output='Tool "write_note" was denied by policy rule "mode.read-only.write".',
            error=True,
        )
        assert MessageDelta(text="I will not write it.") in payloads

    asyncio.run(scenario())


def test_tool_override_allows_one_plugin_write_without_changing_the_preset(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        marker = instance.manifest.workspace.path / "ran.txt"
        (instance.path / "tools" / "write_note.py").write_text(
            """from kinby.plugins import ToolContext, tool

@tool(write=True)
def write_note(note: str, context: ToolContext) -> str:
    \"\"\"Write one note.\"\"\"
    (context.workspace / \"ran.txt\").write_text(note, encoding=\"utf-8\")
    return note
""",
            encoding="utf-8",
        )
        (instance.path / "permissions.toml").write_text(
            '[tools]\nwrite_note = "allow"\n',
            encoding="utf-8",
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_note",
                            "args": {"note": "remember me"},
                            "id": "write-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="Done"),
            ]
        )

        result, payloads = await _run(instance, model)

        assert isinstance(result, TurnOutcome)
        assert marker.read_text(encoding="utf-8") == "remember me"
        assert not any(isinstance(payload, ApprovalRequested) for payload in payloads)

    asyncio.run(scenario())


def test_full_access_allows_bash_with_a_full_access_ceiling(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        marker = instance.manifest.workspace.path / "ran.txt"
        (instance.path / "tools" / "bash.py").write_text(
            """from kinby.plugins import ToolContext, tool

@tool(write=True)
def bash(command: str, context: ToolContext) -> str:
    \"\"\"Run one test command.\"\"\"
    (context.workspace / \"ran.txt\").write_text(command, encoding=\"utf-8\")
    return command
""",
            encoding="utf-8",
        )
        (instance.path / "permissions.toml").write_text(
            'mode = "full-access"\nceiling = "full-access"\n',
            encoding="utf-8",
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "bash",
                            "args": {"command": "touch ran.txt"},
                            "id": "bash-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="Done"),
            ]
        )

        result, payloads = await _run(instance, model)

        assert isinstance(result, TurnOutcome)
        assert marker.read_text(encoding="utf-8") == "touch ran.txt"
        assert not any(isinstance(payload, ApprovalRequested) for payload in payloads)

    asyncio.run(scenario())


def test_auto_asks_before_bash(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        marker = instance.manifest.workspace.path / "ran.txt"
        (instance.path / "tools" / "bash.py").write_text(
            """from kinby.plugins import ToolContext, tool

@tool(write=True)
def bash(command: str, context: ToolContext) -> str:
    \"\"\"Run one test command.\"\"\"
    (context.workspace / "ran.txt").write_text(command, encoding="utf-8")
    return command
""",
            encoding="utf-8",
        )
        (instance.path / "permissions.toml").write_text(
            'mode = "auto"\n',
            encoding="utf-8",
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "bash",
                            "args": {"command": "touch ran.txt"},
                            "id": "bash-1",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        )

        result, payloads = await _run(instance, model)

        assert isinstance(result, ParkedTurn)
        assert not marker.exists()
        assert any(isinstance(payload, ApprovalRequested) for payload in payloads)

    asyncio.run(scenario())


def test_auto_asks_before_a_write_tool_without_path_declarations(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        marker = instance.manifest.workspace.path / "ran.txt"
        (instance.path / "tools" / "write_note.py").write_text(
            """from kinby.plugins import ToolContext, tool

@tool(write=True)
def write_note(note: str, context: ToolContext) -> str:
    \"\"\"Write one note.\"\"\"
    (context.workspace / "ran.txt").write_text(note, encoding="utf-8")
    return note
""",
            encoding="utf-8",
        )
        (instance.path / "permissions.toml").write_text(
            'mode = "auto"\n',
            encoding="utf-8",
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_note",
                            "args": {"note": "remember me"},
                            "id": "write-1",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        )

        result, payloads = await _run(instance, model)

        assert isinstance(result, ParkedTurn)
        assert not marker.exists()
        assert any(isinstance(payload, ApprovalRequested) for payload in payloads)

    asyncio.run(scenario())


def test_auto_allows_an_edit_inside_the_workspace(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path, defaults=True)
        note = instance.manifest.workspace.path / "note.txt"
        note.write_text("before", encoding="utf-8")
        (instance.path / "permissions.toml").write_text(
            'mode = "auto"\n',
            encoding="utf-8",
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "edit",
                            "args": {"path": "note.txt", "old": "before", "new": "after"},
                            "id": "edit-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="Done"),
            ]
        )

        result, payloads = await _run(instance, model)

        assert isinstance(result, TurnOutcome)
        assert note.read_text(encoding="utf-8") == "after"
        assert not any(isinstance(payload, ApprovalRequested) for payload in payloads)
        assert [payload for payload in payloads if isinstance(payload, ToolCall | ToolResult)] == [
            ToolCall(
                call_id="edit-1",
                name="edit",
                arguments={"path": "note.txt", "old": "before", "new": "after"},
            ),
            ToolResult(
                call_id="edit-1",
                name="edit",
                output="Edited note.txt.",
                error=False,
            ),
        ]

    asyncio.run(scenario())


def test_auto_asks_before_a_write_outside_the_workspace(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        target = instance.path / "outside.txt"
        (instance.path / "tools" / "store.py").write_text(
            """from pathlib import Path

from kinby.plugins import tool

@tool(write=True, paths=("path",))
def store(path: str, content: str) -> str:
    \"\"\"Store text at one path.\"\"\"
    Path(path).write_text(content, encoding="utf-8")
    return content
""",
            encoding="utf-8",
        )
        (instance.path / "permissions.toml").write_text(
            'mode = "auto"\n',
            encoding="utf-8",
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "store",
                            "args": {"path": str(target), "content": "outside"},
                            "id": "store-1",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        )

        result, payloads = await _run(instance, model)

        assert isinstance(result, ParkedTurn)
        assert not target.exists()
        approval = next(payload for payload in payloads if isinstance(payload, ApprovalRequested))
        assert approval == ApprovalRequested(
            approval_id=approval.approval_id,
            name="store",
            arguments={"path": str(target), "content": "outside"},
            rule="mode.auto.write",
        )

    asyncio.run(scenario())


def test_full_access_allows_a_write_outside_the_workspace(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        target = instance.path / "outside.txt"
        (instance.path / "tools" / "store.py").write_text(
            """from pathlib import Path

from kinby.plugins import tool

@tool(write=True, paths=("path",))
def store(path: str, content: str) -> str:
    \"\"\"Store text at one path.\"\"\"
    Path(path).write_text(content, encoding="utf-8")
    return content
""",
            encoding="utf-8",
        )
        (instance.path / "permissions.toml").write_text(
            'mode = "full-access"\n',
            encoding="utf-8",
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "store",
                            "args": {"path": str(target), "content": "outside"},
                            "id": "store-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="Done"),
            ]
        )

        result, payloads = await _run(instance, model)

        assert isinstance(result, TurnOutcome)
        assert target.read_text(encoding="utf-8") == "outside"
        assert not any(isinstance(payload, ApprovalRequested) for payload in payloads)

    asyncio.run(scenario())


def test_permissions_are_reloaded_at_each_turn_boundary(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        marker = instance.manifest.workspace.path / "ran.txt"
        (instance.path / "tools" / "write_note.py").write_text(
            """from kinby.plugins import ToolContext, tool

@tool(write=True)
def write_note(note: str, context: ToolContext) -> str:
    \"\"\"Write one note.\"\"\"
    (context.workspace / \"ran.txt\").write_text(note, encoding=\"utf-8\")
    return note
""",
            encoding="utf-8",
        )
        permissions = instance.path / "permissions.toml"
        permissions.write_text('mode = "ask"\n', encoding="utf-8")
        tool_call = {
            "name": "write_note",
            "args": {"note": "remember me"},
            "id": "write-1",
            "type": "tool_call",
        }
        model = ScriptedModel(
            [
                AIMessageChunk(content="", tool_calls=[tool_call]),
                AIMessageChunk(content="", tool_calls=[tool_call]),
                AIMessageChunk(content="Done"),
            ]
        )
        runner = LangGraphRunner(instance, model_factory=lambda _: model)

        first_result, first_payloads = await _run_turn(runner)
        permissions.write_text('mode = "full-access"\n', encoding="utf-8")
        second_result, second_payloads = await _run_turn(runner)

        assert isinstance(first_result, ParkedTurn)
        assert any(isinstance(payload, ApprovalRequested) for payload in first_payloads)
        assert isinstance(second_result, TurnOutcome)
        assert marker.read_text(encoding="utf-8") == "remember me"
        assert not any(isinstance(payload, ApprovalRequested) for payload in second_payloads)

    asyncio.run(scenario())


def test_malformed_permissions_fail_loudly_at_the_turn_boundary(tmp_path: Path) -> None:
    instance = _instance(tmp_path)
    (instance.path / "permissions.toml").write_text(
        'mode = ["ask"\n',
        encoding="utf-8",
    )
    runner = LangGraphRunner(
        instance,
        model_factory=lambda _: ScriptedModel([]),
    )

    with pytest.raises(PermissionsError, match=r"^permissions\.toml:"):
        runner.prepare_for_turn()
