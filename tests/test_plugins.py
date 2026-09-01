import asyncio
import subprocess
from collections.abc import AsyncIterator, Sequence
from dataclasses import asdict, replace
from importlib import import_module
from importlib.metadata import entry_points as installed_entry_points
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from langchain_core.messages import AIMessageChunk, BaseMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

from kinby.contracts import (
    AcceptedResult,
    ApprovalRequested,
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
from tests.helpers import GRAPH_EVENT_TIMEOUT

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


class _BashProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        return_code: int = 0,
        times_out: bool = False,
    ) -> None:
        self.stdout = BytesIO(stdout)
        self.stderr = BytesIO(stderr)
        self.return_code = return_code
        self.times_out = times_out
        self.killed = False
        self.wait_timeouts: list[float | None] = []

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        if self.times_out and not self.killed:
            assert timeout is not None
            raise subprocess.TimeoutExpired(("bash", "-c"), timeout)
        return self.return_code

    def kill(self) -> None:
        self.killed = True


def test_tool_decorator_attaches_the_declaration_record() -> None:
    @tool(write=True, paths=("note",))
    def remember(note: str) -> str:
        """Remember one note."""
        return note

    assert isinstance(remember, Tool)
    assert remember.name == "remember"
    assert remember.write
    assert remember.paths == ("note",)
    assert remember.source == Path(__file__).resolve()
    assert remember.runnable.description == "Remember one note."
    assert remember.runnable.args == {"note": {"title": "Note", "type": "string"}}


def test_tool_decorator_rejects_an_unknown_path_parameter() -> None:
    with pytest.raises(
        ValueError,
        match=r'^Tool "remember" declares unknown path parameter "missing"\.$',
    ):

        @tool(write=True, paths=("missing",))
        def remember(note: str) -> str:
            """Remember one note."""
            return note


def _instance(tmp_path: Path, *, defaults: bool = True) -> Instance:
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


def _write_skill(
    root: Path,
    *,
    directory: str,
    name: str,
    description: str,
    body: str,
) -> Path:
    skill_path = root / directory / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(
        f"""---
name: {name}
description: {description}
---

{body}
""",
        encoding="utf-8",
    )
    return skill_path


async def _start_turn(
    instance: Instance,
    model: ScriptedModel,
    message: str = "Use the tool",
    approval_answers: Sequence[str] = (),
) -> list[Event]:
    dispatcher, thread_id = await _session(instance, model)
    events, _ = await _turn_events(
        dispatcher,
        thread_id,
        message,
        approval_answers=approval_answers,
    )
    return events


async def _session(instance: Instance, model: ScriptedModel) -> tuple[Dispatcher, UUID]:
    runner = LangGraphRunner(instance, model_factory=lambda _: model)
    dispatcher = build_dispatcher(
        instance.manifest.state_dir,
        turns=TurnConfig(
            runner.prepare_for_turn,
            runner.permission_ceiling,
            runner,
        ),
    )
    created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
    assert isinstance(created, ThreadCreateResult)
    return dispatcher, created.id


async def _turn_events(
    dispatcher: Dispatcher,
    thread_id: UUID,
    message: str,
    after_sequence: int = 0,
    approval_answers: Sequence[str] = (),
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
    answers = iter(approval_answers)
    while True:
        event = await asyncio.wait_for(anext(subscription), timeout=GRAPH_EVENT_TIMEOUT)
        assert isinstance(event, Event)
        events.append(event)
        if isinstance(event.payload, ApprovalRequested):
            try:
                answer = next(answers)
            except StopIteration as exc:
                raise AssertionError("The turn requested an unexpected approval.") from exc
            responded = await dispatcher.dispatch(
                "thread.approval.respond",
                {
                    "thread_id": thread_id,
                    "approval_id": event.payload.approval_id,
                    "answer": answer,
                },
                {Scope.THREAD_OPERATE},
            )
            assert isinstance(responded, AcceptedResult)
        if isinstance(event.payload, TurnCompleted):
            await subscription.aclose()
            return events, event.sequence


def test_fresh_instance_binds_the_default_tools(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path, defaults=True)
        model = ScriptedModel([AIMessageChunk(content="Done")])

        await _start_turn(instance, model)

        assert [tool.name for tool in model.bound_tools[0]] == [
            "bash",
            "edit",
            "glob",
            "grep",
            "memory_open",
            "memory_search",
            "read",
            "skill",
            "write",
        ]

    asyncio.run(scenario())


def test_default_entry_point_declares_tool_gate_metadata() -> None:
    default_entry_point = next(
        entry_point
        for entry_point in installed_entry_points(group="kinby.tools")
        if entry_point.name == "defaults"
    )

    tools = default_entry_point.load()

    assert default_entry_point.dist is not None
    assert default_entry_point.dist.name == "kinby"
    assert [(tool.name, tool.write, tool.paths) for tool in tools] == [
        ("read", False, ()),
        ("write", True, ("path",)),
        ("edit", True, ("path",)),
        ("grep", False, ()),
        ("glob", False, ()),
        ("bash", True, ()),
    ]


def test_registry_reads_packaged_tools_once_per_session(tmp_path: Path, monkeypatch) -> None:
    @tool(write=False)
    def packaged() -> str:
        """Return a packaged result."""
        return "packaged"

    groups: list[str] = []

    def installed(*, group: str) -> tuple[SimpleNamespace, ...]:
        groups.append(group)
        return (
            SimpleNamespace(
                name="package",
                value="example.tools:TOOLS",
                load=lambda: (packaged,),
            ),
        )

    registry = import_module("kinby.plugins.registry")
    monkeypatch.setattr(registry, "entry_points", installed)

    async def scenario() -> None:
        instance = _instance(tmp_path, defaults=False)
        model = ScriptedModel([AIMessageChunk(content="First"), AIMessageChunk(content="Second")])
        dispatcher, thread_id = await _session(instance, model)

        _, sequence = await _turn_events(dispatcher, thread_id, "First")
        await _turn_events(dispatcher, thread_id, "Second", sequence)

        assert groups == ["kinby.tools"]
        assert [[tool.name for tool in turn] for turn in model.bound_tools] == [
            ["memory_open", "memory_search", "packaged", "skill"],
            ["memory_open", "memory_search", "packaged", "skill"],
        ]

    asyncio.run(scenario())


def test_two_entry_points_exporting_one_name_emit_a_warning(
    tmp_path: Path,
    monkeypatch,
) -> None:
    @tool(write=False)
    def shared() -> str:
        """Return the first result."""
        return "first"

    first = replace(shared, source=tmp_path / "first.py")

    @tool(write=False)
    def shared() -> str:
        """Return the second result."""
        return "second"

    second = replace(shared, source=tmp_path / "second.py")

    @tool(write=False)
    def shared() -> str:
        """Return the third result."""
        return "third"

    third = replace(shared, source=tmp_path / "third.py")

    @tool(write=False)
    def available() -> str:
        """Return an unambiguous packaged result."""
        return "available"

    def installed(*, group: str) -> tuple[SimpleNamespace, ...]:
        assert group == "kinby.tools"
        return (
            SimpleNamespace(
                name="first",
                value="first:TOOLS",
                load=lambda: (first, available),
            ),
            SimpleNamespace(name="second", value="second:TOOLS", load=lambda: (second,)),
            SimpleNamespace(name="third", value="third:TOOLS", load=lambda: (third,)),
        )

    registry = import_module("kinby.plugins.registry")
    monkeypatch.setattr(registry, "entry_points", installed)

    async def scenario() -> None:
        instance = _instance(tmp_path, defaults=False)
        model = ScriptedModel([AIMessageChunk(content="First"), AIMessageChunk(content="Second")])
        dispatcher, thread_id = await _session(instance, model)

        events, sequence = await _turn_events(dispatcher, thread_id, "First")
        repeated, _ = await _turn_events(dispatcher, thread_id, "Second", sequence)

        warnings = [event.payload for event in events if isinstance(event.payload, Warning)]
        warnings_again = [event.payload for event in repeated if isinstance(event.payload, Warning)]
        assert [warning.sources for warning in warnings] == [
            (str(first.source), str(second.source)),
            (str(first.source), str(third.source)),
        ]
        assert all(
            warning.message == 'Tool "shared" is exported by both sources.' for warning in warnings
        )
        assert warnings_again == warnings
        assert [[tool.name for tool in turn] for turn in model.bound_tools] == [
            ["available", "memory_open", "memory_search", "skill"],
            ["available", "memory_open", "memory_search", "skill"],
        ]

    asyncio.run(scenario())


def test_disabling_defaults_keeps_a_third_party_entry_point_named_defaults(
    tmp_path: Path,
    monkeypatch,
) -> None:
    @tool(write=False)
    def packaged() -> str:
        """Return a third-party result."""
        return "third-party"

    def installed(*, group: str) -> tuple[SimpleNamespace, ...]:
        assert group == "kinby.tools"
        return (
            SimpleNamespace(
                name="defaults",
                value="third_party.tools:TOOLS",
                dist=SimpleNamespace(name="third-party"),
                load=lambda: (packaged,),
            ),
        )

    registry = import_module("kinby.plugins.registry")
    monkeypatch.setattr(registry, "entry_points", installed)

    async def scenario() -> None:
        instance = _instance(tmp_path, defaults=False)
        model = ScriptedModel([AIMessageChunk(content="Done")])

        await _start_turn(instance, model)

        assert [tool.name for tool in model.bound_tools[0]] == [
            "memory_open",
            "memory_search",
            "packaged",
            "skill",
        ]

    asyncio.run(scenario())


def test_manifest_can_disable_default_tools(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance_path = tmp_path / "instance"
        instance_path.mkdir()
        (instance_path / "tools").mkdir()
        (instance_path / "workspace").mkdir()
        (instance_path / "kinby.toml").write_text(
            f'id = "test"\n\n[models]\nmain = "{_MODEL}"\n\n[tools]\ndefaults = false\n',
            encoding="utf-8",
        )
        instance = load_instance(instance_path)
        model = ScriptedModel([AIMessageChunk(content="Done")])

        await _start_turn(instance, model)

        assert asdict(instance.manifest)["tools"] == {"defaults": False}
        assert [[tool.name for tool in turn] for turn in model.bound_tools] == [
            ["memory_open", "memory_search", "skill"]
        ]

    asyncio.run(scenario())


def test_broken_instance_tool_keeps_default_tools_on_the_first_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path, defaults=True)
        broken = instance.path / "tools" / "broken.py"
        broken.write_text("def broken(:\n", encoding="utf-8")
        model = ScriptedModel([AIMessageChunk(content="Done")])

        events = await _start_turn(instance, model)

        warning = next(event.payload for event in events if isinstance(event.payload, Warning))
        assert warning.sources == (str(broken),)
        assert [tool.name for tool in model.bound_tools[0]] == [
            "bash",
            "edit",
            "glob",
            "grep",
            "memory_open",
            "memory_search",
            "read",
            "skill",
            "write",
        ]

    asyncio.run(scenario())


def test_instance_bash_shadows_the_packaged_bash(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path, defaults=True)
        (instance.path / "tools" / "bash.py").write_text(
            """from kinby.plugins import tool

@tool(write=False)
def bash(command: str) -> str:
    \"\"\"Return the instance command.\"\"\"
    return f\"instance: {command}\"
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
                            "args": {"command": "status"},
                            "id": "bash-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="Done"),
            ]
        )

        events = await _start_turn(instance, model)

        assert [tool.name for tool in model.bound_tools[0]].count("bash") == 1
        result = next(event.payload for event in events if isinstance(event.payload, ToolResult))
        assert result.output == "instance: status"
        assert not result.error

    asyncio.run(scenario())


def test_default_read_resolves_paths_from_the_workspace(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path, defaults=True)
        notes = instance.manifest.workspace.path / "notes"
        notes.mkdir()
        (notes / "today.txt").write_text("Ship the default tools.\n", encoding="utf-8")
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "read",
                            "args": {"path": "notes/today.txt"},
                            "id": "read-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="Done"),
            ]
        )

        events = await _start_turn(instance, model)

        result = next(event.payload for event in events if isinstance(event.payload, ToolResult))
        assert result == ToolResult(
            call_id="read-1",
            name="read",
            output="Ship the default tools.\n",
            error=False,
        )

    asyncio.run(scenario())


def test_default_read_rejects_a_path_outside_the_workspace(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path, defaults=True)
        (instance.path / "secret.txt").write_text("private\n", encoding="utf-8")
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "read",
                            "args": {"path": "../secret.txt"},
                            "id": "read-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="Done"),
            ]
        )

        events = await _start_turn(instance, model)

        result = next(event.payload for event in events if isinstance(event.payload, ToolResult))
        assert result == ToolResult(
            call_id="read-1",
            name="read",
            output='ValueError: Path "../secret.txt" is outside the workspace.',
            error=True,
        )

    asyncio.run(scenario())


def test_default_grep_and_glob_resolve_paths_from_the_workspace(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path, defaults=True)
        docs = instance.manifest.workspace.path / "docs"
        docs.mkdir()
        (docs / "first.txt").write_text("needle\nother\n", encoding="utf-8")
        (docs / "second.txt").write_text("nothing\nneedle again\n", encoding="utf-8")
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "grep",
                            "args": {"pattern": "needle", "path": "docs"},
                            "id": "grep-1",
                            "type": "tool_call",
                        },
                        {
                            "name": "glob",
                            "args": {"pattern": "docs/*.txt"},
                            "id": "glob-1",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessageChunk(content="Done"),
            ]
        )

        events = await _start_turn(instance, model)

        results = [event.payload for event in events if isinstance(event.payload, ToolResult)]
        assert [(result.name, result.output, result.error) for result in results] == [
            (
                "grep",
                "docs/first.txt:1:needle\ndocs/second.txt:2:needle again",
                False,
            ),
            ("glob", "docs/first.txt\ndocs/second.txt", False),
        ]

    asyncio.run(scenario())


def test_default_bash_uses_the_workspace_timeout_and_output_cap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path, defaults=True)
        calls: list[tuple[tuple[str, ...], Path]] = []
        process = _BashProcess(stdout=b"x" * 40_000)

        def popen(
            command: Sequence[str],
            **options: object,
        ) -> _BashProcess:
            calls.append((tuple(command), Path(str(options["cwd"]))))
            assert options["stdout"] is subprocess.PIPE
            assert options["stderr"] is subprocess.PIPE
            return process

        monkeypatch.setattr("kinby.plugins.defaults.shell.subprocess.Popen", popen)
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "bash",
                            "args": {"command": "status"},
                            "id": "bash-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="Done"),
            ]
        )

        events = await _start_turn(instance, model, approval_answers=("yes",))

        result = next(event.payload for event in events if isinstance(event.payload, ToolResult))
        assert calls == [(("bash", "-c", "status"), instance.manifest.workspace.path)]
        assert process.wait_timeouts == [120.0]
        assert result.output == "x" * 30_000
        assert not result.error

    asyncio.run(scenario())


def test_default_bash_reports_a_nonzero_exit_code(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path, defaults=True)
        process = _BashProcess(stderr=b"bad command", return_code=2)

        def popen(
            command: Sequence[str],
            **options: object,
        ) -> _BashProcess:
            return process

        monkeypatch.setattr("kinby.plugins.defaults.shell.subprocess.Popen", popen)
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "bash",
                            "args": {"command": "missing"},
                            "id": "bash-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="Done"),
            ]
        )

        events = await _start_turn(instance, model, approval_answers=("yes",))

        result = next(event.payload for event in events if isinstance(event.payload, ToolResult))
        assert result == ToolResult(
            call_id="bash-1",
            name="bash",
            output="Exit code: 2\nstderr:\nbad command",
            error=False,
        )

    asyncio.run(scenario())


def test_default_bash_kills_a_timed_out_process_and_returns_partial_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path, defaults=True)
        process = _BashProcess(stdout=b"partial output", times_out=True)

        def popen(
            command: Sequence[str],
            **options: object,
        ) -> _BashProcess:
            return process

        monkeypatch.setattr("kinby.plugins.defaults.shell.subprocess.Popen", popen)
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "bash",
                            "args": {"command": "slow"},
                            "id": "bash-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="Done"),
            ]
        )

        events = await _start_turn(instance, model, approval_answers=("yes",))

        result = next(event.payload for event in events if isinstance(event.payload, ToolResult))
        assert process.killed
        assert process.wait_timeouts == [120.0, None]
        assert result == ToolResult(
            call_id="bash-1",
            name="bash",
            output="TimeoutError: Bash timed out after 120 seconds.\npartial output",
            error=True,
        )

    asyncio.run(scenario())


def test_default_write_and_edit_change_workspace_files(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path, defaults=True)
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "write",
                            "args": {"path": "notes/today.txt", "content": "draft"},
                            "id": "write-1",
                            "type": "tool_call",
                        },
                        {
                            "name": "edit",
                            "args": {
                                "path": "notes/today.txt",
                                "old": "draft",
                                "new": "done",
                            },
                            "id": "edit-1",
                            "type": "tool_call",
                        },
                        {
                            "name": "read",
                            "args": {"path": "notes/today.txt"},
                            "id": "read-1",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessageChunk(content="Done"),
            ]
        )

        events = await _start_turn(
            instance,
            model,
            approval_answers=("yes", "yes"),
        )

        results = [event.payload for event in events if isinstance(event.payload, ToolResult)]
        assert [(result.name, result.output, result.error) for result in results] == [
            ("write", "Wrote notes/today.txt.", False),
            ("edit", "Edited notes/today.txt.", False),
            ("read", "done", False),
        ]

    asyncio.run(scenario())


def test_tool_file_is_bound_and_called_on_the_next_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path, defaults=False)
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

        assert len(model.bound_tools) == 1
        assert [tool.name for tool in model.bound_tools[0]] == [
            "greet",
            "memory_open",
            "memory_search",
            "skill",
        ]
        bound = next(tool for tool in model.bound_tools[0] if tool.name == "greet")
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


async def _park_write_tool(
    tmp_path: Path,
) -> tuple[Instance, Dispatcher, UUID, Event, ScriptedModel]:
    instance = _instance(tmp_path)
    (instance.path / "tools" / "write_note.py").write_text(
        """from kinby.plugins import ToolContext, tool

@tool(write=True)
def write_note(note: str, context: ToolContext) -> str:
    \"\"\"Write a note to the workspace.\"\"\"
    (context.workspace / "note.txt").write_text(note, encoding="utf-8")
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
            AIMessageChunk(content="Done"),
        ]
    )
    dispatcher, thread_id = await _session(instance, model)

    accepted = await dispatcher.dispatch(
        "thread.turn.start",
        {"thread_id": thread_id, "message": "Remember this"},
        {Scope.THREAD_OPERATE},
    )
    assert isinstance(accepted, AcceptedResult)
    subscription = dispatcher.subscribe(
        "thread.subscribe",
        {"thread_id": thread_id},
        {Scope.THREAD_READ},
    )
    started = await asyncio.wait_for(anext(subscription), timeout=GRAPH_EVENT_TIMEOUT)
    requested = await asyncio.wait_for(anext(subscription), timeout=GRAPH_EVENT_TIMEOUT)
    await subscription.aclose()
    assert isinstance(started, Event)
    assert isinstance(requested, Event)
    return instance, dispatcher, thread_id, requested, model


async def _answer_write_tool(
    dispatcher: Dispatcher,
    thread_id: UUID,
    requested: Event,
    answer: str,
) -> list[Event]:
    assert isinstance(requested.payload, ApprovalRequested)
    accepted = await dispatcher.dispatch(
        "thread.approval.respond",
        {
            "thread_id": thread_id,
            "approval_id": requested.payload.approval_id,
            "answer": answer,
        },
        {Scope.THREAD_OPERATE},
    )
    assert isinstance(accepted, AcceptedResult)
    subscription = dispatcher.subscribe(
        "thread.subscribe",
        {"thread_id": thread_id, "after_sequence": requested.sequence},
        {Scope.THREAD_READ},
    )
    events: list[Event] = []
    while not events or not isinstance(events[-1].payload, TurnCompleted):
        event = await asyncio.wait_for(anext(subscription), timeout=GRAPH_EVENT_TIMEOUT)
        assert isinstance(event, Event)
        events.append(event)
    await subscription.aclose()
    return events


def test_write_tool_parks_before_running(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance, _, _, requested, _ = await _park_write_tool(tmp_path)

        assert isinstance(requested.payload, ApprovalRequested)
        assert requested.payload == ApprovalRequested(
            approval_id=requested.payload.approval_id,
            name="write_note",
            arguments={"note": "remember me"},
            rule="mode.ask.write",
        )
        assert not (instance.manifest.workspace.path / "note.txt").exists()

    asyncio.run(scenario())


def test_yes_runs_the_parked_write_tool_and_completes(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance, dispatcher, thread_id, requested, _ = await _park_write_tool(tmp_path)
        events = await _answer_write_tool(dispatcher, thread_id, requested, "yes")

        assert [
            event.payload for event in events if isinstance(event.payload, ToolCall | ToolResult)
        ] == [
            ToolCall(call_id="write-1", name="write_note", arguments={"note": "remember me"}),
            ToolResult(
                call_id="write-1",
                name="write_note",
                output="remember me",
                error=False,
            ),
        ]
        assert (instance.manifest.workspace.path / "note.txt").read_text(
            encoding="utf-8"
        ) == "remember me"
        assert isinstance(events[-1].payload, TurnCompleted)

    asyncio.run(scenario())


def test_resume_does_not_repeat_tools_before_approval(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        (instance.path / "tools" / "count.py").write_text(
            """from kinby.plugins import tool

calls = 0

@tool(write=False)
def count() -> str:
    \"\"\"Return the number of calls.\"\"\"
    global calls
    calls += 1
    return str(calls)
""",
            encoding="utf-8",
        )
        (instance.path / "tools" / "write_note.py").write_text(
            """from kinby.plugins import ToolContext, tool

@tool(write=True)
def write_note(note: str, context: ToolContext) -> str:
    \"\"\"Write a note to the workspace.\"\"\"
    (context.workspace / "note.txt").write_text(note, encoding="utf-8")
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
                            "name": "count",
                            "args": {},
                            "id": "count-1",
                            "type": "tool_call",
                        },
                        {
                            "name": "write_note",
                            "args": {"note": "remember me"},
                            "id": "write-1",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessageChunk(content="Done"),
            ]
        )
        dispatcher, thread_id = await _session(instance, model)

        accepted = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": thread_id, "message": "Count and remember"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(accepted, AcceptedResult)
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": thread_id},
            {Scope.THREAD_READ},
        )
        requested: Event | None = None
        events: list[Event] = []
        while requested is None:
            event = await asyncio.wait_for(anext(subscription), timeout=GRAPH_EVENT_TIMEOUT)
            assert isinstance(event, Event)
            events.append(event)
            if isinstance(event.payload, ApprovalRequested):
                requested = event
        await subscription.aclose()

        events.extend(await _answer_write_tool(dispatcher, thread_id, requested, "yes"))

        assert [
            event.payload
            for event in events
            if isinstance(event.payload, ToolCall | ToolResult)
            and event.payload.call_id == "count-1"
        ] == [
            ToolCall(call_id="count-1", name="count", arguments={}),
            ToolResult(call_id="count-1", name="count", output="1", error=False),
        ]

    asyncio.run(scenario())


def test_any_other_answer_denies_the_write_tool_and_completes(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance, dispatcher, thread_id, requested, _ = await _park_write_tool(tmp_path)
        events = await _answer_write_tool(
            dispatcher,
            thread_id,
            requested,
            "not this time",
        )

        assert not any(isinstance(event.payload, ToolCall) for event in events)
        result = next(event.payload for event in events if isinstance(event.payload, ToolResult))
        assert result == ToolResult(
            call_id="write-1",
            name="write_note",
            output='Tool "write_note" was denied by the user.',
            error=True,
        )
        assert not (instance.manifest.workspace.path / "note.txt").exists()

    asyncio.run(scenario())


def test_interrupting_approval_after_restart_keeps_the_next_turn_history_valid(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance, _, thread_id, requested, _ = await _park_write_tool(tmp_path)
        model = ScriptedModel([AIMessageChunk(content="Start over")])
        restarted_runner = LangGraphRunner(instance, model_factory=lambda _: model)
        restarted = build_dispatcher(
            instance.manifest.state_dir,
            turns=TurnConfig(
                restarted_runner.prepare_for_turn,
                restarted_runner.permission_ceiling,
                restarted_runner,
            ),
        )
        interrupted = await restarted.dispatch(
            "thread.turn.interrupt",
            {"thread_id": thread_id},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(interrupted, AcceptedResult)

        events, _ = await _turn_events(
            restarted,
            thread_id,
            "Start again",
            requested.sequence,
        )

        assert isinstance(events[-1].payload, TurnCompleted)
        assert not any(getattr(message, "tool_calls", ()) for message in model.messages[-1])

    asyncio.run(scenario())


def test_write_tool_approval_resumes_after_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance, _, thread_id, requested, _ = await _park_write_tool(tmp_path)
        assert isinstance(requested.payload, ApprovalRequested)
        restarted_runner = LangGraphRunner(
            instance,
            model_factory=lambda _: ScriptedModel([AIMessageChunk(content="Done")]),
        )
        restarted = build_dispatcher(
            instance.manifest.state_dir,
            turns=TurnConfig(
                restarted_runner.prepare_for_turn,
                restarted_runner.permission_ceiling,
                restarted_runner,
            ),
        )

        events = await _answer_write_tool(restarted, thread_id, requested, "yes")

        assert (instance.manifest.workspace.path / "note.txt").read_text(
            encoding="utf-8"
        ) == "remember me"
        assert isinstance(events[-1].payload, TurnCompleted)

    asyncio.run(scenario())


def test_turn_binds_tools_sorted_by_name(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path, defaults=False)
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

        assert [tool.name for tool in model.bound_tools[0]] == [
            "alpha",
            "memory_open",
            "memory_search",
            "skill",
            "zulu",
        ]

    asyncio.run(scenario())


def test_instance_skill_is_catalogued_and_skill_tool_is_bound(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path, defaults=False)
        _write_skill(
            instance.path / "skills",
            directory="planning",
            name="planning",
            description="Plan work before changing files.",
            body="These are the detailed planning instructions.",
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "skill",
                            "args": {"name": "planning"},
                            "id": "skill-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="Done"),
            ]
        )

        events = await _start_turn(instance, model)

        system_message = next(
            message for message in model.messages[0] if isinstance(message, SystemMessage)
        )
        assert (
            "# Skills\n"
            "Use the `skill` tool to read a skill's full instructions.\n"
            "- planning: Plan work before changing files."
        ) in system_message.text
        assert "These are the detailed planning instructions." not in system_message.text
        assert [tool.name for tool in model.bound_tools[0]] == [
            "memory_open",
            "memory_search",
            "skill",
        ]
        result = next(event.payload for event in events if isinstance(event.payload, ToolResult))
        assert result == ToolResult(
            call_id="skill-1",
            name="skill",
            output="These are the detailed planning instructions.",
            error=False,
        )

    asyncio.run(scenario())


def test_skills_follow_source_order_and_instance_skill_shadows_workspace(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        original = _instance(tmp_path)
        instance_path = original.path
        workspace_path = instance_path / "workspace"
        (instance_path / "memory").mkdir()
        (instance_path / "memory" / "profile.md").write_text(
            "USER PROFILE",
            encoding="utf-8",
        )
        (workspace_path / "AGENTS.md").write_text("WORKSPACE RULES", encoding="utf-8")
        (instance_path / "kinby.toml").write_text(
            f'id = "test"\n\n[models]\nmain = "{_MODEL}"\n\n'
            "[workspace.conventions]\n"
            "enabled = true\n"
            'instructions = ["AGENTS.md"]\n'
            'skills = [".agents/skills", "team-skills"]\n',
            encoding="utf-8",
        )
        _write_skill(
            instance_path / "skills",
            directory="a-first",
            name="instance-first",
            description="First instance skill.",
            body="First body.",
        )
        _write_skill(
            instance_path / "skills",
            directory="b-shared",
            name="shared",
            description="Instance copy.",
            body="Instance shared body.",
        )
        _write_skill(
            workspace_path / ".agents" / "skills",
            directory="a-shared",
            name="shared",
            description="Hidden workspace copy.",
            body="Workspace shared body.",
        )
        _write_skill(
            workspace_path / ".agents" / "skills",
            directory="b-first",
            name="workspace-first",
            description="First workspace directory.",
            body="First workspace body.",
        )
        _write_skill(
            workspace_path / "team-skills",
            directory="only",
            name="workspace-second",
            description="Second workspace directory.",
            body="Second workspace body.",
        )
        instance = load_instance(instance_path)
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "skill",
                            "args": {"name": "shared"},
                            "id": "shared-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="Done"),
            ]
        )

        events = await _start_turn(instance, model)

        system_message = next(
            message for message in model.messages[0] if isinstance(message, SystemMessage)
        )
        ordered_text = (
            "WORKSPACE RULES",
            "- instance-first: First instance skill.",
            "- shared: Instance copy.",
            "- workspace-first: First workspace directory.",
            "- workspace-second: Second workspace directory.",
            "USER PROFILE",
        )
        positions = [system_message.text.index(text) for text in ordered_text]
        assert positions == sorted(positions)
        assert "Hidden workspace copy." not in system_message.text
        result = next(event.payload for event in events if isinstance(event.payload, ToolResult))
        assert result.output == "Instance shared body."

    asyncio.run(scenario())


def test_unknown_skill_returns_a_tool_error(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "skill",
                            "args": {"name": "missing"},
                            "id": "missing-skill",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="Recovered"),
            ]
        )

        events = await _start_turn(instance, model)

        result = next(event.payload for event in events if isinstance(event.payload, ToolResult))
        assert result == ToolResult(
            call_id="missing-skill",
            name="skill",
            output='SkillNotFoundError: Skill "missing" is not available in this turn.',
            error=True,
        )

    asyncio.run(scenario())


def test_adding_a_skill_between_turns_updates_the_second_catalogue(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        model = ScriptedModel(
            [
                AIMessageChunk(content="First done"),
                AIMessageChunk(content="Second done"),
            ]
        )
        dispatcher, thread_id = await _session(instance, model)

        _, sequence = await _turn_events(dispatcher, thread_id, "First")
        _write_skill(
            instance.path / "skills",
            directory="fresh",
            name="fresh",
            description="Added after the first turn.",
            body="Fresh instructions.",
        )
        await _turn_events(dispatcher, thread_id, "Second", sequence)

        system_prompts = [
            next(message.text for message in call if isinstance(message, SystemMessage))
            for call in model.messages
        ]
        assert "- fresh: Added after the first turn." not in system_prompts[0]
        assert "- fresh: Added after the first turn." in system_prompts[1]

    asyncio.run(scenario())


def test_invalid_skill_frontmatter_warns_and_skips_the_files(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        skills_path = instance.path / "skills"
        without_frontmatter = skills_path / "plain" / "SKILL.md"
        without_frontmatter.parent.mkdir(parents=True)
        without_frontmatter.write_text("Plain instructions.", encoding="utf-8")
        without_name = skills_path / "unnamed" / "SKILL.md"
        without_name.parent.mkdir(parents=True)
        without_name.write_text(
            """---
description: Missing its name.
---

Unnamed instructions.
""",
            encoding="utf-8",
        )
        without_description = skills_path / "undescribed" / "SKILL.md"
        without_description.parent.mkdir(parents=True)
        without_description.write_text(
            """---
name: undescribed
---

Instructions without a description.
""",
            encoding="utf-8",
        )
        model = ScriptedModel([AIMessageChunk(content="Done")])

        events = await _start_turn(instance, model)

        warnings = [event.payload for event in events if isinstance(event.payload, Warning)]
        assert warnings == [
            Warning(
                sources=(str(without_frontmatter),),
                message="Skill frontmatter is missing.",
            ),
            Warning(
                sources=(str(without_description),),
                message='Skill frontmatter must contain "description".',
            ),
            Warning(
                sources=(str(without_name),),
                message='Skill frontmatter must contain "name".',
            ),
        ]
        system_message = next(
            message for message in model.messages[0] if isinstance(message, SystemMessage)
        )
        assert "- undescribed" not in system_message.text

    asyncio.run(scenario())


def test_unreadable_skill_file_warns_and_skips_the_file(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        skills_path = instance.path / "skills"
        unreadable = skills_path / "binary" / "SKILL.md"
        unreadable.parent.mkdir(parents=True)
        unreadable.write_bytes(b"\xff\xfe not utf-8")
        _write_skill(
            skills_path,
            directory="readable",
            name="readable",
            description="Still loads.",
            body="Readable body.",
        )
        model = ScriptedModel([AIMessageChunk(content="Done")])

        events = await _start_turn(instance, model)

        warning = next(event.payload for event in events if isinstance(event.payload, Warning))
        assert warning.sources == (str(unreadable),)
        assert "decode" in warning.message
        system_message = next(
            message for message in model.messages[0] if isinstance(message, SystemMessage)
        )
        assert "- readable: Still loads." in system_message.text

    asyncio.run(scenario())


def test_same_tier_duplicate_skill_names_warn_with_both_sources(tmp_path: Path) -> None:
    async def scenario() -> None:
        original = _instance(tmp_path)
        instance_path = original.path
        workspace_path = instance_path / "workspace"
        (instance_path / "kinby.toml").write_text(
            f'id = "test"\n\n[models]\nmain = "{_MODEL}"\n\n'
            "[workspace.conventions]\n"
            "enabled = true\n"
            'skills = [".agents/skills", "team-skills"]\n',
            encoding="utf-8",
        )
        instance_first = _write_skill(
            instance_path / "skills",
            directory="a-first",
            name="instance-shared",
            description="First instance skill.",
            body="First body.",
        )
        instance_second = _write_skill(
            instance_path / "skills",
            directory="b-second",
            name="instance-shared",
            description="Second instance skill.",
            body="Second body.",
        )
        workspace_first = _write_skill(
            workspace_path / ".agents" / "skills",
            directory="first",
            name="workspace-shared",
            description="First workspace skill.",
            body="First body.",
        )
        workspace_second = _write_skill(
            workspace_path / "team-skills",
            directory="second",
            name="workspace-shared",
            description="Second workspace skill.",
            body="Second body.",
        )
        instance = load_instance(instance_path)
        model = ScriptedModel([AIMessageChunk(content="Done")])

        events = await _start_turn(instance, model)

        warnings = [event.payload for event in events if isinstance(event.payload, Warning)]
        assert warnings == [
            Warning(
                sources=(str(instance_first), str(instance_second)),
                message='Skill "instance-shared" is declared by both sources.',
            ),
            Warning(
                sources=(str(workspace_first), str(workspace_second)),
                message='Skill "workspace-shared" is declared by both sources.',
            ),
        ]
        system_message = next(
            message for message in model.messages[0] if isinstance(message, SystemMessage)
        )
        assert "- instance-shared: First instance skill." in system_message.text
        assert "Second instance skill." not in system_message.text
        assert "- workspace-shared: First workspace skill." in system_message.text
        assert "Second workspace skill." not in system_message.text

    asyncio.run(scenario())


def test_skill_body_is_fixed_for_the_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        skill_path = _write_skill(
            instance.path / "skills",
            directory="stable",
            name="stable",
            description="Keep one turn consistent.",
            body="Original instructions.",
        )

        class EditingModel(ScriptedModel):
            async def astream(
                self,
                messages: Sequence[BaseMessage],
            ) -> AsyncIterator[AIMessageChunk]:
                if not self.messages:
                    skill_path.write_text("Changed after prompt assembly.", encoding="utf-8")
                async for chunk in super().astream(messages):
                    yield chunk

        model = EditingModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "skill",
                            "args": {"name": "stable"},
                            "id": "stable-skill",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="Done"),
            ]
        )

        events = await _start_turn(instance, model)

        result = next(event.payload for event in events if isinstance(event.payload, ToolResult))
        assert result.output == "Original instructions."
        assert not result.error

    asyncio.run(scenario())


def test_core_skill_tool_warns_and_replaces_a_namesake_plugin(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path)
        plugin_path = instance.path / "tools" / "skill.py"
        plugin_path.write_text(
            '''from kinby.plugins import tool

@tool(write=False)
def skill(name: str) -> str:
    """Return a plugin result."""
    return "plugin"
''',
            encoding="utf-8",
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": "skill",
                            "args": {"name": "missing"},
                            "id": "core-skill",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessageChunk(content="Done"),
            ]
        )

        events = await _start_turn(instance, model)

        warning = next(event.payload for event in events if isinstance(event.payload, Warning))
        assert warning.sources[0] == str(plugin_path)
        assert Path(warning.sources[1]).name == "skills.py"
        assert warning.message == 'Plugin tool "skill" was replaced by the core tool.'
        result = next(event.payload for event in events if isinstance(event.payload, ToolResult))
        assert result.output == (
            'SkillNotFoundError: Skill "missing" is not available in this turn.'
        )
        assert result.error

    asyncio.run(scenario())


def test_editing_and_deleting_a_tool_file_changes_the_next_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path, defaults=False)
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
        assert [tool.name for tool in model.bound_tools[0]] == [
            "memory_open",
            "memory_search",
            "skill",
            "version",
        ]
        assert [tool.name for tool in model.bound_tools[1]] == [
            "memory_open",
            "memory_search",
            "skill",
            "version",
        ]
        assert [tool.name for tool in model.bound_tools[2]] == [
            "memory_open",
            "memory_search",
            "skill",
        ]
        assert not any(isinstance(event.payload, ToolCall) for event in third)

    asyncio.run(scenario())


def test_syntax_errors_warn_for_each_file_and_keep_the_previous_tool_set(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance = _instance(tmp_path, defaults=False)
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
        assert [warning.sources for warning in warnings] == [
            (str(path),) for path in sorted(broken)
        ]
        assert all("SyntaxError" in warning.message for warning in warnings)
        assert [tool.name for tool in model.bound_tools[1]] == [
            "memory_open",
            "memory_search",
            "skill",
            "stable",
        ]
        repeated = [event.payload for event in again if isinstance(event.payload, Warning)]
        assert [warning.sources for warning in repeated] == [
            (str(path),) for path in sorted(broken)
        ]
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
        instance = _instance(tmp_path, defaults=False)
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
        assert warning.sources == (str(first_source), str(second_source))
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
        instance = _instance(tmp_path, defaults=False)
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
        instance = _instance(tmp_path, defaults=False)
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

        where = next(tool for tool in model.bound_tools[0] if tool.name == "where")
        assert where.args == {"label": {"title": "Label", "type": "string"}}
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
        instance = _instance(tmp_path, defaults=False)
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
        instance = _instance(tmp_path, defaults=False)
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
        instance = _instance(tmp_path, defaults=False)
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
            event = await asyncio.wait_for(anext(subscription), timeout=GRAPH_EVENT_TIMEOUT)
            assert isinstance(event, Event)
            events.append(event)

        interrupted = await dispatcher.dispatch(
            "thread.turn.interrupt",
            {"thread_id": thread_id},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(interrupted, AcceptedResult)
        while not any(isinstance(event.payload, TurnInterrupted) for event in events):
            event = await asyncio.wait_for(anext(subscription), timeout=GRAPH_EVENT_TIMEOUT)
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
        assert [
            message.content
            for message in model.messages[-1]
            if not isinstance(message, SystemMessage)
        ] == ["Continue"]

    asyncio.run(scenario())
