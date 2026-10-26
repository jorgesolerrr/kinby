"""Authored tools: functions the agent exposes from its instance code, gated as writes until the
user marks them read-only (ADR 0109)."""

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest

from kinby.contracts import (
    ApprovalRequested,
    ConfigActor,
    ConfigFile,
    ErrorCode,
    ErrorEnvelope,
    GateOutcome,
    PermissionMode,
    ToolResult,
    is_turn_closing,
)
from kinby.instance import Instance, Tools
from kinby.instance.code import exposed_tools
from kinby.instance.config_changes import ConfigChangeLog
from kinby.plugins import ToolContext
from kinby.plugins.registry import ToolRegistry
from tests.test_instance_code import call, greeting_wheel, turn
from tests.test_routine_grants import CallingModel, answer, gates, next_stop, run_routine
from tests.test_routines import instance_at, routine_file, signal_runtime
from tests.test_scheduler import call as dispatch

ECHO = '''from kinby.plugins import tool


@tool(write=False)
def echo(text: str) -> str:
    """Repeat a line of text."""
    return f"echo: {text}"
'''


@pytest.fixture(autouse=True)
def offline_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UV_OFFLINE", "1")


def expose(instance: Instance, name: str = "echo", module: str = ECHO) -> str:
    asyncio.run(call(instance, "code_write", {"path": f"tools/{name}.py", "content": module}))
    return asyncio.run(call(instance, "code_expose_tool", {"name": name}))


def test_a_turn_exposes_a_function_and_the_next_turn_calls_it(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    wheel = greeting_wheel(tmp_path)
    module = '''import greeting

from kinby.plugins import tool


@tool(write=False)
def greet(name: str) -> str:
    """Greet someone."""
    return f"{greeting.hello()}, {name}"
'''
    exposing = asyncio.run(
        turn(
            instance,
            [
                ("code_add_package", {"name": str(wheel)}),
                ("code_write", {"path": "tools/greet.py", "content": module}),
                ("code_expose_tool", {"name": "greet"}),
            ],
            PermissionMode.FULL_ACCESS,
        )
    )

    calling = asyncio.run(
        turn(instance, [("greet", {"name": "Jorge"})], PermissionMode.FULL_ACCESS)
    )

    exposed = [p for p in exposing if isinstance(p, ToolResult)]
    assert not any(result.error for result in exposed), exposed
    result = next(p for p in calling if isinstance(p, ToolResult))
    assert (result.error, result.output) == (False, "hello from greeting, Jorge")
    assert exposed_tools(tmp_path) == ("greet",)


@pytest.mark.parametrize(
    ("module", "error"),
    [
        ("def echo(:\n", "code/tools/echo.py does not parse"),
        ("import missing_package\n", "ModuleNotFoundError: No module named 'missing_package'"),
        ("def echo(text: str) -> str:\n    return text\n", 'defines no tool "echo"'),
    ],
)
def test_exposing_a_module_that_does_not_load_as_a_tool_fails_and_exposes_nothing(
    tmp_path: Path, module: str, error: str
) -> None:
    instance = instance_at(tmp_path)

    with pytest.raises(ValueError, match="echo") as raised:
        expose(instance, module=module)

    assert error in str(raised.value)
    assert exposed_tools(tmp_path) == ()


@pytest.mark.parametrize("name", ["routine_list", "post"])
def test_an_authored_tool_cannot_take_a_core_or_an_instance_tool_name(
    tmp_path: Path, name: str
) -> None:
    instance = instance_at(tmp_path)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "post.py").write_text(
        "from kinby.plugins import tool\n\n\n@tool(write=True)\ndef post(text: str) -> str:\n"
        '    """Post."""\n    return text\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="did not author"):
        expose(instance, name, ECHO.replace("echo", name))

    assert exposed_tools(tmp_path) == ()


def test_a_broken_module_keeps_the_last_valid_tools_with_a_warning(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    expose(instance)
    registry = ToolRegistry(tmp_path, tools=Tools(defaults=False))
    loaded, _ = registry.refresh()
    module = tmp_path / "code" / "tools" / "echo.py"
    module.write_text("import missing_package\n", encoding="utf-8")
    later = module.stat().st_mtime_ns + 1_000_000
    os.utime(module, ns=(later, later))

    kept, warnings = registry.refresh()

    echo = kept.get("echo")
    assert echo is not None
    assert echo is loaded.get("echo")
    [warning] = warnings
    assert warning.sources == (str(module),)
    assert "missing_package" in warning.message
    called = asyncio.run(echo.ainvoke({"text": "hi"}, ToolContext(instance, uuid4())))
    assert called == "echo: hi"


def test_an_authored_tool_result_is_cut_at_the_bash_cap(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    expose(instance, module=ECHO.replace('f"echo: {text}"', "text * 40_000"))
    tools, _ = ToolRegistry(tmp_path, tools=Tools(defaults=False)).refresh()
    echo = tools.get("echo")
    assert echo is not None

    called = asyncio.run(echo.ainvoke({"text": "x"}, ToolContext(instance, uuid4())))

    assert called == "x" * 30_000


def test_an_authored_tool_that_declares_it_reads_still_asks_in_ask_mode(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    expose(instance)

    payloads = asyncio.run(turn(instance, [("echo", {"text": "hi"})], PermissionMode.ASK))

    approval = next(p for p in payloads if isinstance(p, ApprovalRequested))
    assert (approval.name, approval.rule) == ("echo", "authored.write")


def test_mark_read_only_approves_records_the_user_and_stops_asking_until_unmarked(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        await call(instance, "code_write", {"path": "tools/echo.py", "content": ECHO})
        await call(instance, "code_expose_tool", {"name": "echo"})
        routine_file(instance, "description: Echo")
        dispatcher, _ = signal_runtime(instance, CallingModel("echo"))

        parked = await run_routine(dispatcher)
        await answer(dispatcher, parked, "mark_read_only")
        closed = await next_stop(dispatcher, parked.thread_id, parked.sequence)
        unasked = await run_routine(dispatcher)
        await dispatch(dispatcher, "tool.read_only.set", name="echo", read_only=False)
        asked_again = await run_routine(dispatcher)

        assert isinstance(parked.payload, ApprovalRequested)
        assert is_turn_closing(closed.payload)
        [first] = await gates(dispatcher, parked.thread_id)
        assert (first.action, first.rule) == (GateOutcome.ALLOW, "authored.write")
        assert is_turn_closing(unasked.payload)
        [second] = await gates(dispatcher, unasked.thread_id)
        assert (second.action, second.rule) == (GateOutcome.ALLOW, "mode.ask.read")
        assert isinstance(asked_again.payload, ApprovalRequested)
        log = ConfigChangeLog(instance.manifest.state_dir)
        marked, unmarked = reversed(log.history(ConfigFile("read-only-tools.json"), 2))
        assert (marked.actor, unmarked.actor) == (ConfigActor.APP, ConfigActor.APP)
        assert '+  "echo"' in marked.diff
        assert '-  "echo"' in unmarked.diff

    asyncio.run(scenario())


def test_only_an_authored_tool_approval_marks_a_tool_read_only(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: Run")
        dispatcher, _ = signal_runtime(instance, CallingModel("code_write"))
        parked = await run_routine(dispatcher)

        refused = await answer(dispatcher, parked, "mark_read_only")

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT

    asyncio.run(scenario())


def test_the_agent_cannot_delete_or_reexpose_a_tool_it_did_not_expose(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    expose(instance)
    # The user's own exposure, made by hand rather than through the agent's tools.
    project = tmp_path / "code" / "pyproject.toml"
    text = project.read_text(encoding="utf-8")
    project.write_text(text.replace('"echo",', '"echo",\n    "shout",'), encoding="utf-8")
    (tmp_path / "code" / "tools" / "shout.py").write_text(
        ECHO.replace("echo", "shout"), encoding="utf-8"
    )

    with pytest.raises(PermissionError, match="did not expose"):
        asyncio.run(call(instance, "code_delete", {"path": "tools/shout.py"}))
    with pytest.raises(PermissionError, match="did not expose"):
        asyncio.run(call(instance, "code_write", {"path": "tools/shout.py", "content": ECHO}))
    with pytest.raises(PermissionError, match="did not expose"):
        asyncio.run(call(instance, "code_expose_tool", {"name": "shout"}))
    with pytest.raises(ValueError, match="inside the instance code"):
        asyncio.run(call(instance, "code_delete", {"path": "../tools/post.py"}))
    rewritten = asyncio.run(
        call(instance, "code_write", {"path": "tools/echo.py", "content": ECHO})
    )
    deleted = asyncio.run(call(instance, "code_delete", {"path": "tools/echo.py"}))

    assert rewritten == "Wrote code/tools/echo.py."
    assert deleted == "Deleted code/tools/echo.py."
    assert (tmp_path / "code" / "tools" / "shout.py").read_text(encoding="utf-8") == ECHO.replace(
        "echo", "shout"
    )


def test_a_routine_code_step_naming_an_authored_tool_runs_only_once_marked_read_only(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        await call(instance, "code_write", {"path": "tools/echo.py", "content": ECHO})
        await call(instance, "code_expose_tool", {"name": "echo"})
        routine_file(instance, 'description: Echo\nrun: echo\narguments: {"text": "news"}')
        dispatcher, _ = signal_runtime(instance, CallingModel())

        refused = await run_routine(dispatcher)
        await dispatch(dispatcher, "tool.read_only.set", name="echo", read_only=True)
        ran = await run_routine(dispatcher)

        [refused_gate] = await gates(dispatcher, refused.thread_id)
        assert (refused_gate.action, refused_gate.rule) == (GateOutcome.DENY, "authored.write")
        [ran_gate] = await gates(dispatcher, ran.thread_id)
        assert ran_gate.action is GateOutcome.ALLOW
        assert ran_gate.rule == "mode.ask.read"

    asyncio.run(scenario())


def test_the_approval_to_expose_a_tool_shows_its_declared_flag_and_source(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    asyncio.run(call(instance, "code_write", {"path": "tools/echo.py", "content": ECHO}))

    payloads = asyncio.run(
        turn(instance, [("code_expose_tool", {"name": "echo"})], PermissionMode.ASK)
    )

    approval = next(p for p in payloads if isinstance(p, ApprovalRequested))
    assert approval.arguments == {"name": "echo"}
    assert approval.preview == f"echo declares write=False.\n\ncode/tools/echo.py:\n{ECHO}"
