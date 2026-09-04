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
    PermissionMode,
    ToolCall,
    ToolResult,
)
from kinby.core import LangGraphRunner
from kinby.core.turns import ParkedTurn, TurnContext, TurnOutcome, TurnRequest, TurnResult
from kinby.instance import Instance, load_instance
from kinby.instance.permissions import BashPolicy, GatePolicy, PermissionsError

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


def _write_store_tool(instance: Instance) -> None:
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

    result = await runner.run(
        TurnRequest(
            thread_id=thread_id,
            turn_id=turn_id,
            message="Remember this",
            model=preparation.model,
            permission_mode=preparation.default_mode,
        ),
        TurnContext(preparation.budgets, emit),
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


@pytest.mark.parametrize(
    ("command", "rule"),
    [
        ("rm -rf /instance", "bash.deny[0]"),
        ("printf x\nrm -rf /instance", "bash.deny[0]"),
        ("git reset --hard HEAD~1", "bash.deny[1]"),
        ("git push --force origin main", "bash.deny[2]"),
    ],
)
def test_shipped_bash_rule_denies_disasters_in_full_access_and_turn_continues(
    tmp_path: Path,
    command: str,
    rule: str,
) -> None:
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
                            "args": {"command": command},
                            "id": "bash-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="I will not run it."),
            ]
        )

        result, payloads = await _run(instance, model)

        assert isinstance(result, TurnOutcome)
        assert not marker.exists()
        assert next(
            payload for payload in payloads if isinstance(payload, ToolResult)
        ) == ToolResult(
            call_id="bash-1",
            name="bash",
            output=f'Tool "bash" was denied by policy rule "{rule}".',
            error=True,
        )
        assert MessageDelta(text="I will not run it.") in payloads

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mode",
    [PermissionMode.ASK, PermissionMode.AUTO, PermissionMode.FULL_ACCESS],
)
def test_bash_ask_rule_parks_in_every_mode_above_read_only(
    tmp_path: Path,
    mode: PermissionMode,
) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        (instance.path / "tools" / "bash.py").write_text(
            """from kinby.plugins import tool

@tool(write=True)
def bash(command: str) -> str:
    \"\"\"Run one test command.\"\"\"
    return command
""",
            encoding="utf-8",
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "bash",
                            "args": {"command": "deploy production"},
                            "id": "bash-1",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        )
        gate_policy = None
        if mode is PermissionMode.AUTO:
            gate_policy = GatePolicy(
                mode=mode,
                bash=BashPolicy(ask=(r"^deploy production$",)),
            )
        else:
            (instance.path / "permissions.toml").write_text(
                (f'mode = "{mode.value}"\n\n[bash]\nask = ["^deploy production$"]\n'),
                encoding="utf-8",
            )
        runner = LangGraphRunner(
            instance,
            model_factory=lambda _: model,
            gate_policy=gate_policy,
        )

        result, payloads = await _run_turn(runner)

        assert isinstance(result, ParkedTurn)
        approval = next(payload for payload in payloads if isinstance(payload, ApprovalRequested))
        assert approval == ApprovalRequested(
            approval_id=approval.approval_id,
            name="bash",
            arguments={"command": "deploy production"},
            rule="bash.ask[0]",
        )

    asyncio.run(scenario())


def test_instance_bash_deny_list_replaces_shipped_defaults(tmp_path: Path) -> None:
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
            'mode = "full-access"\n\n[bash]\ndeny = []\n',
            encoding="utf-8",
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "bash",
                            "args": {"command": "git push --force origin main"},
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
        assert marker.read_text(encoding="utf-8") == "git push --force origin main"
        assert not any(isinstance(payload, ApprovalRequested) for payload in payloads)

    asyncio.run(scenario())


def test_bash_deny_rule_wins_when_ask_rule_also_matches(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        (instance.path / "tools" / "bash.py").write_text(
            """from kinby.plugins import tool

@tool(write=True)
def bash(command: str) -> str:
    \"\"\"Run one test command.\"\"\"
    return command
""",
            encoding="utf-8",
        )
        (instance.path / "permissions.toml").write_text(
            (
                'mode = "full-access"\n\n'
                "[bash]\n"
                'deny = ["^deploy production$"]\n'
                'ask = ["^deploy production$"]\n'
            ),
            encoding="utf-8",
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "bash",
                            "args": {"command": "deploy production"},
                            "id": "bash-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="I will not deploy."),
            ]
        )

        result, payloads = await _run(instance, model)

        assert isinstance(result, TurnOutcome)
        assert not any(isinstance(payload, ApprovalRequested) for payload in payloads)
        assert next(
            payload for payload in payloads if isinstance(payload, ToolResult)
        ) == ToolResult(
            call_id="bash-1",
            name="bash",
            output='Tool "bash" was denied by policy rule "bash.deny[0]".',
            error=True,
        )

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


def test_auto_resolves_declared_paths_against_the_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        workspace_target = instance.manifest.workspace.path / "inside.txt"
        instance_target = instance.path / "inside.txt"
        _write_store_tool(instance)
        (instance.path / "permissions.toml").write_text(
            'mode = "auto"\n',
            encoding="utf-8",
        )
        monkeypatch.chdir(instance.path)
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "store",
                            "args": {"path": "inside.txt", "content": "inside"},
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
        assert workspace_target.read_text(encoding="utf-8") == "inside"
        assert not instance_target.exists()
        assert not any(isinstance(payload, ApprovalRequested) for payload in payloads)

    asyncio.run(scenario())


def test_auto_asks_before_a_write_outside_the_workspace(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        target = instance.path / "outside.txt"
        _write_store_tool(instance)
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
        _write_store_tool(instance)
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


def test_malformed_bash_regex_fails_loudly_at_the_turn_boundary(tmp_path: Path) -> None:
    instance = _instance(tmp_path)
    (instance.path / "permissions.toml").write_text(
        '[bash]\nask = ["["]\n',
        encoding="utf-8",
    )
    runner = LangGraphRunner(
        instance,
        model_factory=lambda _: ScriptedModel([]),
    )

    with pytest.raises(
        PermissionsError,
        match=r"^permissions\.toml: bash\.ask\.0: invalid regex:",
    ):
        runner.prepare_for_turn()


def test_malformed_injected_bash_regex_fails_at_the_turn_boundary(tmp_path: Path) -> None:
    runner = LangGraphRunner(
        _instance(tmp_path),
        model_factory=lambda _: ScriptedModel([]),
        gate_policy=GatePolicy(bash=BashPolicy(ask=("[",))),
    )

    with pytest.raises(
        PermissionsError,
        match=r"^gate policy override: bash\.ask\.0: invalid regex:",
    ):
        runner.prepare_for_turn()
