import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

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
from kinby.core.turns import ParkedTurn, TurnOutcome, TurnRequest, TurnResult
from kinby.instance import Instance, load_instance
from kinby.instance.permissions import SHIPPED_POLICY, GatePolicy

_MODEL = "openai:gpt-5"


class ScriptedModel:
    def __init__(self, responses: Sequence[AIMessageChunk]) -> None:
        self._responses = iter(responses)

    def bind_tools(self, tools: Sequence[StructuredTool]) -> ScriptedModel:
        return self

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        yield next(self._responses)


def _instance(tmp_path: Path) -> Instance:
    instance_path = tmp_path / "instance"
    instance_path.mkdir()
    (instance_path / "tools").mkdir()
    (instance_path / "workspace").mkdir()
    (instance_path / "kinby.toml").write_text(
        f'id = "test"\n\n[models]\nmain = "{_MODEL}"\n\n[tools]\ndefaults = false\n',
        encoding="utf-8",
    )
    return load_instance(instance_path)


async def _run(
    instance: Instance,
    model: ScriptedModel,
    gate_policy: GatePolicy = SHIPPED_POLICY,
) -> tuple[TurnResult, list[Payload]]:
    runner = LangGraphRunner(
        instance,
        model_factory=lambda _: model,
        gate_policy=gate_policy,
    )
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
        policy = GatePolicy(mode=PermissionMode.READ_ONLY)

        result, payloads = await _run(instance, model, policy)

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
