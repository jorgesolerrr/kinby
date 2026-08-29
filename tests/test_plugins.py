import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from uuid import UUID

from langchain_core.messages import AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.tools import StructuredTool

from kinby.contracts import (
    AcceptedResult,
    Event,
    Scope,
    ThreadCreateResult,
    ToolCall,
    ToolResult,
    TurnCompleted,
    TurnInterrupted,
    Warning,
)
from kinby.core import Dispatcher, LangGraphRunner, TurnConfig, build_dispatcher
from kinby.instance import Instance, load_instance
from kinby.plugins import Tool, tool

_MODEL = "openai:gpt-5"


class ScriptedModel:
    def __init__(self, responses: Sequence[AIMessageChunk]) -> None:
        self._responses = iter(responses)
        self.bound_tools: list[tuple[StructuredTool, ...]] = []
        self.messages: list[tuple[BaseMessage, ...]] = []

    def bind_tools(self, tools: Sequence[StructuredTool]) -> ScriptedModel:
        self.bound_tools.append(tuple(tools))
        return self

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.messages.append(tuple(messages))
        yield next(self._responses)


def test_tool_decorator_attaches_the_declaration_record() -> None:
    @tool(write=True)
    def remember(note: str) -> str:
        """Remember one note."""
        return note

    assert isinstance(remember, Tool)
    assert remember.name == "remember"
    assert remember.write
    assert remember.source == Path(__file__).resolve()
    assert remember.runnable.description == "Remember one note."
    assert remember.runnable.args == {"note": {"title": "Note", "type": "string"}}


def _instance(tmp_path: Path) -> Instance:
    instance_path = tmp_path / "instance"
    instance_path.mkdir()
    (instance_path / "tools").mkdir()
    (instance_path / "workspace").mkdir()
    (instance_path / "kinby.toml").write_text(
        f'id = "test"\n\n[models]\nmain = "{_MODEL}"\n',
        encoding="utf-8",
    )
    return load_instance(instance_path)


async def _start_turn(
    instance: Instance,
    model: ScriptedModel,
    message: str = "Use the tool",
) -> list[Event]:
    dispatcher, thread_id = await _session(instance, model)
    events, _ = await _turn_events(dispatcher, thread_id, message)
    return events


async def _session(instance: Instance, model: ScriptedModel) -> tuple[Dispatcher, UUID]:
    runner = LangGraphRunner(instance, model_factory=lambda _: model)
    dispatcher = build_dispatcher(
        instance.manifest.state_dir,
        turns=TurnConfig(runner.model_for_turn, runner),
    )
    created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
    assert isinstance(created, ThreadCreateResult)
    return dispatcher, created.id


async def _turn_events(
    dispatcher: Dispatcher,
    thread_id: UUID,
    message: str,
    after_sequence: int = 0,
) -> tuple[list[Event], int]:
    accepted = await dispatcher.dispatch(
        "thread.turn.start",
        {"thread_id": thread_id, "message": message},
        {Scope.THREAD_OPERATE},
    )
    assert isinstance(accepted, AcceptedResult)
    subscription = dispatcher.subscribe(
        "thread.subscribe",
        {"thread_id": thread_id, "after_sequence": after_sequence},
        {Scope.THREAD_READ},
    )
    events: list[Event] = []
    while True:
        event = await asyncio.wait_for(anext(subscription), timeout=1)
        assert isinstance(event, Event)
        events.append(event)
        if isinstance(event.payload, TurnCompleted):
            await subscription.aclose()
            return events, event.sequence


def test_tool_file_is_bound_and_called_on_the_next_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        (instance.path / "tools" / "greet.py").write_text(
            """from kinby.plugins import tool

@tool(write=False)
def greet(name: str) -> str:
    \"\"\"Greet someone by name.\"\"\"
    return f\"Hello, {name}\"
""",
            encoding="utf-8",
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "greet",
                            "args": {"name": "Jorge"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="Done"),
            ]
        )

        events = await _start_turn(instance, model)

        assert [tool.name for tool in model.bound_tools[0]] == ["greet"]
        bound = model.bound_tools[0][0]
        assert bound.description == "Greet someone by name."
        assert bound.args == {"name": {"title": "Name", "type": "string"}}
        activity = [
            event.payload for event in events if isinstance(event.payload, ToolCall | ToolResult)
        ]
        assert activity == [
            ToolCall(call_id="call-1", name="greet", arguments={"name": "Jorge"}),
            ToolResult(
                call_id="call-1",
                name="greet",
                output="Hello, Jorge",
                error=False,
            ),
        ]

    asyncio.run(scenario())


def test_turn_binds_tools_sorted_by_name(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        for file_name, tool_name in (("zulu.py", "zulu"), ("alpha.py", "alpha")):
            (instance.path / "tools" / file_name).write_text(
                f"""from kinby.plugins import tool

@tool(write=False)
def {tool_name}() -> str:
    \"\"\"Return this tool's name.\"\"\"
    return \"{tool_name}\"
""",
                encoding="utf-8",
            )
        model = ScriptedModel([AIMessageChunk(content="Done")])

        await _start_turn(instance, model)

        assert [tool.name for tool in model.bound_tools[0]] == ["alpha", "zulu"]

    asyncio.run(scenario())


def test_editing_and_deleting_a_tool_file_changes_the_next_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        tool_path = instance.path / "tools" / "version.py"
        tool_path.write_text(
            """from kinby.plugins import tool

@tool(write=False)
def version() -> str:
    \"\"\"Return the tool version.\"\"\"
    return \"one\"
""",
            encoding="utf-8",
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {"name": "version", "args": {}, "id": "first", "type": "tool_call"}
                    ],
                ),
                AIMessageChunk(content="First done"),
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {"name": "version", "args": {}, "id": "second", "type": "tool_call"}
                    ],
                ),
                AIMessageChunk(content="Second done"),
                AIMessageChunk(content="No tool"),
            ]
        )
        dispatcher, thread_id = await _session(instance, model)

        first, sequence = await _turn_events(dispatcher, thread_id, "First")
        tool_path.write_text(
            tool_path.read_text(encoding="utf-8").replace('return "one"', 'return "two"'),
            encoding="utf-8",
        )
        second, sequence = await _turn_events(dispatcher, thread_id, "Second", sequence)
        tool_path.unlink()
        third, _ = await _turn_events(dispatcher, thread_id, "Third", sequence)

        results = [
            event.payload.output
            for event in [*first, *second]
            if isinstance(event.payload, ToolResult)
        ]
        assert results == ["one", "two"]
        assert [tool.name for tool in model.bound_tools[0]] == ["version"]
        assert [tool.name for tool in model.bound_tools[2]] == ["version"]
        assert len(model.bound_tools) == 4
        assert not any(isinstance(event.payload, ToolCall) for event in third)

    asyncio.run(scenario())


def test_syntax_errors_warn_for_each_file_and_keep_the_previous_tool_set(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        (instance.path / "tools" / "stable.py").write_text(
            """from kinby.plugins import tool

@tool(write=False)
def stable() -> str:
    \"\"\"Return a stable result.\"\"\"
    return \"still works\"
""",
            encoding="utf-8",
        )

        def call(call_id: str) -> AIMessageChunk:
            return AIMessageChunk(
                content="",
                tool_calls=[{"name": "stable", "args": {}, "id": call_id, "type": "tool_call"}],
            )

        model = ScriptedModel(
            [
                call("first"),
                AIMessageChunk(content="First done"),
                call("second"),
                AIMessageChunk(content="Second done"),
                call("third"),
                AIMessageChunk(content="Third done"),
            ]
        )
        dispatcher, thread_id = await _session(instance, model)

        _, sequence = await _turn_events(dispatcher, thread_id, "First")
        fresh_source = instance.path / "tools" / "fresh.py"
        fresh_source.write_text(
            """from pathlib import Path

from kinby.plugins import tool

counter = Path(__file__).with_suffix(".count")
loads = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
counter.write_text(str(loads + 1), encoding="utf-8")

@tool(write=False)
def fresh() -> str:
    \"\"\"Return a fresh result.\"\"\"
    return \"fresh\"
""",
            encoding="utf-8",
        )
        broken = [instance.path / "tools" / name for name in ("broken.py", "also_broken.py")]
        for path in broken:
            path.write_text("def broken(:\n", encoding="utf-8")
        events, sequence = await _turn_events(dispatcher, thread_id, "Second", sequence)
        again, _ = await _turn_events(dispatcher, thread_id, "Third", sequence)

        warnings = [event.payload for event in events if isinstance(event.payload, Warning)]
        assert [warning.source for warning in warnings] == [str(path) for path in sorted(broken)]
        assert all("SyntaxError" in warning.message for warning in warnings)
        assert [tool.name for tool in model.bound_tools[2]] == ["stable"]
        repeated = [event.payload for event in again if isinstance(event.payload, Warning)]
        assert [warning.source for warning in repeated] == [str(path) for path in sorted(broken)]
        assert fresh_source.with_suffix(".count").read_text(encoding="utf-8") == "1"
        assert any(
            isinstance(event.payload, ToolResult)
            and event.payload.output == "still works"
            and not event.payload.error
            for event in events
        )

    asyncio.run(scenario())


def test_duplicate_tool_name_warns_with_both_sources_and_keeps_the_previous_set(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        first_source = instance.path / "tools" / "first.py"
        first_source.write_text(
            """from kinby.plugins import tool

@tool(write=False)
def shared() -> str:
    \"\"\"Return the first result.\"\"\"
    return \"first\"
""",
            encoding="utf-8",
        )

        def call(call_id: str) -> AIMessageChunk:
            return AIMessageChunk(
                content="",
                tool_calls=[{"name": "shared", "args": {}, "id": call_id, "type": "tool_call"}],
            )

        model = ScriptedModel(
            [
                call("before"),
                AIMessageChunk(content="Before"),
                call("after"),
                AIMessageChunk(content="After"),
                call("again"),
                AIMessageChunk(content="Again"),
            ]
        )
        dispatcher, thread_id = await _session(instance, model)
        _, sequence = await _turn_events(dispatcher, thread_id, "Before duplicate")
        second_source = instance.path / "tools" / "second.py"
        second_source.write_text(
            """from pathlib import Path

from kinby.plugins import tool

counter = Path(__file__).with_suffix(".count")
loads = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
counter.write_text(str(loads + 1), encoding="utf-8")

@tool(write=False)
def shared() -> str:
    \"\"\"Return the second result.\"\"\"
    return \"second\"
""",
            encoding="utf-8",
        )

        events, sequence = await _turn_events(dispatcher, thread_id, "After duplicate", sequence)
        await _turn_events(dispatcher, thread_id, "Duplicate remains", sequence)

        warning = next(event.payload for event in events if isinstance(event.payload, Warning))
        assert str(first_source) in warning.source
        assert str(second_source) in warning.source
        assert warning.message == 'Tool "shared" is exported by both sources.'
        result = next(event.payload for event in events if isinstance(event.payload, ToolResult))
        assert result.output == "first"
        assert not result.error
        assert second_source.with_suffix(".count").read_text(encoding="utf-8") == "1"

    asyncio.run(scenario())


def test_tool_loop_usage_accumulates_within_a_turn_and_resets_next_turn(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {"name": "missing", "args": {}, "id": "missing", "type": "tool_call"}
                    ],
                    usage_metadata={"input_tokens": 5, "output_tokens": 1, "total_tokens": 6},
                ),
                AIMessageChunk(
                    content="First done",
                    usage_metadata={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                ),
                AIMessageChunk(
                    content="Second done",
                    usage_metadata={"input_tokens": 7, "output_tokens": 4, "total_tokens": 11},
                ),
            ]
        )
        dispatcher, thread_id = await _session(instance, model)

        first, sequence = await _turn_events(dispatcher, thread_id, "First")
        second, _ = await _turn_events(dispatcher, thread_id, "Second", sequence)

        first_completed = next(
            event.payload for event in first if isinstance(event.payload, TurnCompleted)
        )
        second_completed = next(
            event.payload for event in second if isinstance(event.payload, TurnCompleted)
        )
        assert first_completed == TurnCompleted(input_tokens=7, output_tokens=4)
        assert second_completed == TurnCompleted(input_tokens=7, output_tokens=4)

    asyncio.run(scenario())


def test_tool_context_is_injected_and_hidden_from_the_model_schema(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        (instance.path / "tools" / "where.py").write_text(
            """from __future__ import annotations

from kinby.plugins import ToolContext, tool

@tool(write=False)
def where(label: str, context: ToolContext) -> str:
    \"\"\"Return where this tool is running.\"\"\"
    return f\"{label}|{context.instance.manifest.id}|{context.workspace}|{context.thread_id}\"
""",
            encoding="utf-8",
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "where",
                            "args": {"label": "here"},
                            "id": "where-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="Located"),
            ]
        )
        dispatcher, thread_id = await _session(instance, model)

        events, _ = await _turn_events(dispatcher, thread_id, "Where are you?")

        assert model.bound_tools[0][0].args == {"label": {"title": "Label", "type": "string"}}
        result = next(event.payload for event in events if isinstance(event.payload, ToolResult))
        assert result == ToolResult(
            call_id="where-1",
            name="where",
            output=f"here|test|{instance.manifest.workspace.path}|{thread_id}",
            error=False,
        )

    asyncio.run(scenario())


def test_unknown_tool_returns_an_error_to_the_model_and_the_turn_completes(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {"name": "missing", "args": {}, "id": "missing-1", "type": "tool_call"}
                    ],
                ),
                AIMessageChunk(content="Recovered"),
            ]
        )

        events = await _start_turn(instance, model)

        result = next(event.payload for event in events if isinstance(event.payload, ToolResult))
        assert result == ToolResult(
            call_id="missing-1",
            name="missing",
            output='Tool "missing" is not available in this turn.',
            error=True,
        )
        tool_message = model.messages[1][-1]
        assert isinstance(tool_message, ToolMessage)
        assert tool_message.status == "error"
        assert isinstance(events[-1].payload, TurnCompleted)

    asyncio.run(scenario())


def test_raised_tool_exception_is_an_error_result_and_the_turn_completes(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        (instance.path / "tools" / "fail.py").write_text(
            """from kinby.plugins import tool

@tool(write=False)
def fail() -> str:
    \"\"\"Raise a tool failure.\"\"\"
    raise ValueError(\"bad weather\")
""",
            encoding="utf-8",
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[{"name": "fail", "args": {}, "id": "fail-1", "type": "tool_call"}],
                ),
                AIMessageChunk(content="Handled"),
            ]
        )

        events = await _start_turn(instance, model)

        result = next(event.payload for event in events if isinstance(event.payload, ToolResult))
        assert result == ToolResult(
            call_id="fail-1",
            name="fail",
            output="ValueError: bad weather",
            error=True,
        )
        assert isinstance(events[-1].payload, TurnCompleted)

    asyncio.run(scenario())


def test_interrupt_during_a_tool_loop_stops_before_the_next_call(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        (instance.path / "tools" / "loop.py").write_text(
            """import time

from kinby.plugins import tool

@tool(write=False)
def slow() -> str:
    \"\"\"Wait before returning.\"\"\"
    time.sleep(1)
    return \"slow\"

@tool(write=False)
def second() -> str:
    \"\"\"Return the second result.\"\"\"
    return \"second\"
""",
            encoding="utf-8",
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {"name": "slow", "args": {}, "id": "slow-1", "type": "tool_call"},
                        {
                            "name": "second",
                            "args": {},
                            "id": "second-1",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessageChunk(content="Recovered"),
            ]
        )
        dispatcher, thread_id = await _session(instance, model)
        accepted = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": thread_id, "message": "Run both"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(accepted, AcceptedResult)
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": thread_id},
            {Scope.THREAD_READ},
        )
        events: list[Event] = []
        while not any(isinstance(event.payload, ToolCall) for event in events):
            event = await asyncio.wait_for(anext(subscription), timeout=1)
            assert isinstance(event, Event)
            events.append(event)

        interrupted = await dispatcher.dispatch(
            "thread.turn.interrupt",
            {"thread_id": thread_id},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(interrupted, AcceptedResult)
        while not any(isinstance(event.payload, TurnInterrupted) for event in events):
            event = await asyncio.wait_for(anext(subscription), timeout=1)
            assert isinstance(event, Event)
            events.append(event)
        await subscription.aclose()

        calls = [event.payload.name for event in events if isinstance(event.payload, ToolCall)]
        assert calls == ["slow"]
        assert isinstance(events[-1].payload, TurnInterrupted)

        recovered, _ = await _turn_events(
            dispatcher,
            thread_id,
            "Continue",
            events[-1].sequence,
        )
        assert isinstance(recovered[-1].payload, TurnCompleted)
        assert [message.content for message in model.messages[-1]] == ["Continue"]

    asyncio.run(scenario())
