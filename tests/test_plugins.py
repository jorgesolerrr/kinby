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

from langchain_core.messages import AIMessageChunk, BaseMessage, SystemMessage, ToolMessage
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
        turns=TurnConfig(runner.prepare_for_turn, runner),
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
            "read",
            "write",
        ]

    asyncio.run(scenario())


def test_default_entry_point_declares_tool_write_flags() -> None:
    default_entry_point = next(
        entry_point
        for entry_point in installed_entry_points(group="kinby.tools")
        if entry_point.name == "defaults"
    )

    tools = default_entry_point.load()

    assert default_entry_point.dist is not None
    assert default_entry_point.dist.name == "kinby"
    assert [(tool.name, tool.write) for tool in tools] == [
        ("read", False),
        ("write", True),
        ("edit", True),
        ("grep", False),
        ("glob", False),
        ("bash", True),
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
        instance = _instance(tmp_path)
        model = ScriptedModel([AIMessageChunk(content="First"), AIMessageChunk(content="Second")])
        dispatcher, thread_id = await _session(instance, model)

        _, sequence = await _turn_events(dispatcher, thread_id, "First")
        await _turn_events(dispatcher, thread_id, "Second", sequence)

        assert groups == ["kinby.tools"]
        assert [[tool.name for tool in turn] for turn in model.bound_tools] == [
            ["packaged"],
            ["packaged"],
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
        instance = _instance(tmp_path)
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
            ["available"],
            ["available"],
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
        instance = _instance(tmp_path)
        model = ScriptedModel([AIMessageChunk(content="Done")])

        await _start_turn(instance, model)

        assert [tool.name for tool in model.bound_tools[0]] == ["packaged"]

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
        assert model.bound_tools == []

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
            "read",
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

        events = await _start_turn(instance, model)

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

        events = await _start_turn(instance, model)

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

        events = await _start_turn(instance, model)

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

        events = await _start_turn(instance, model)

        results = [event.payload for event in events if isinstance(event.payload, ToolResult)]
        assert [(result.name, result.output, result.error) for result in results] == [
            ("write", "Wrote notes/today.txt.", False),
            ("edit", "Edited notes/today.txt.", False),
            ("read", "done", False),
        ]

    asyncio.run(scenario())


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

        assert len(model.bound_tools) == 1
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
        assert [tool.name for tool in model.bound_tools[1]] == ["version"]
        assert len(model.bound_tools) == 2
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
        assert [warning.sources for warning in warnings] == [
            (str(path),) for path in sorted(broken)
        ]
        assert all("SyntaxError" in warning.message for warning in warnings)
        assert [tool.name for tool in model.bound_tools[1]] == ["stable"]
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
        assert [
            message.content
            for message in model.messages[-1]
            if not isinstance(message, SystemMessage)
        ] == ["Continue"]

    asyncio.run(scenario())
