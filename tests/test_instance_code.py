import asyncio
import zipfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk
from pydantic import JsonValue

from kinby.contracts import (
    ApprovalRequested,
    ConfigActor,
    Event,
    GateOutcome,
    Payload,
    PermissionMode,
    SystemPrompt,
    ToolGated,
    ToolResult,
)
from kinby.core import LangGraphRunner
from kinby.core.turns import PreparedTurnRequest, TurnContext
from kinby.instance import Instance, load_instance
from kinby.instance.config_changes import ConfigChangeLog
from kinby.plugins import Tool, ToolContext
from kinby.plugins.instance_tools import instance_tools
from tests.test_instance_tools import ScriptedModel
from tests.test_routines import instance_at


@pytest.fixture(autouse=True)
def offline_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UV_OFFLINE", "1")


def greeting_wheel(directory: Path) -> Path:
    """A wheel of the package greeting, built by hand so uv installs it without an index."""
    files = {
        "greeting/__init__.py": "def hello():\n    return 'hello from greeting'\n",
        "greeting-1.0.dist-info/METADATA": "Metadata-Version: 2.1\nName: greeting\nVersion: 1.0\n",
        "greeting-1.0.dist-info/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    record = "greeting-1.0.dist-info/RECORD"
    files[record] = "".join(f"{name},,\n" for name in [*files, record])
    wheel = directory / "greeting-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return wheel


def code_tool(instance: Instance, name: str) -> Tool:
    return next(tool for tool in instance_tools(instance) if tool.name == name)


async def call(instance: Instance, name: str, arguments: dict[str, JsonValue]) -> str:
    result = await code_tool(instance, name).ainvoke(
        arguments, ToolContext(instance=instance, thread_id=uuid4())
    )
    assert isinstance(result, str)
    return result


async def turn(
    instance: Instance,
    calls: Sequence[tuple[str, dict[str, JsonValue]]],
    mode: PermissionMode,
) -> list[Payload]:
    """Run one turn whose model makes *calls* one after another, in *mode*."""
    model = ScriptedModel(
        [
            *(
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {
                            "name": name,
                            "args": arguments,
                            "id": f"call-{index}",
                            "type": "tool_call",
                        }
                    ],
                )
                for index, (name, arguments) in enumerate(calls)
            ),
            AIMessageChunk(content="Done."),
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

    await runner.run(
        PreparedTurnRequest(
            thread_id=thread_id,
            turn_id=turn_id,
            message="Keep a greeting script.",
            model=preparation.model,
            permission_mode=mode,
            system_prompt=SystemPrompt("System prompt"),
        ),
        TurnContext(preparation.budgets, emit),
    )
    return payloads


def test_a_turn_writes_a_script_adds_its_package_and_runs_it(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    wheel = greeting_wheel(tmp_path)
    script = "import sys\nimport greeting\n\nprint(greeting.hello(), *sys.argv[1:])\n"

    payloads = asyncio.run(
        turn(
            instance,
            [
                ("code_write", {"path": "greet.py", "content": script}),
                ("code_add_package", {"name": str(wheel)}),
                ("code_run", {"script": "greet.py", "arguments": ["and", "Jorge"]}),
            ],
            PermissionMode.FULL_ACCESS,
        )
    )

    results = {p.name: p for p in payloads if isinstance(p, ToolResult)}
    assert not any(result.error for result in results.values()), results
    assert results["code_run"].output == "hello from greeting and Jorge\n"
    assert (tmp_path / "code" / "uv.lock").is_file()
    changes = ConfigChangeLog(instance.manifest.state_dir).history(None, 10)
    assert [(change.file, change.actor) for change in reversed(changes)] == [
        ("code/greet.py", ConfigActor.AGENT),
        ("code/pyproject.toml", ConfigActor.AGENT),
        ("code/pyproject.toml", ConfigActor.AGENT),
    ]
    assert all(change.turn_id is not None for change in changes)
    assert '+    "greeting",' in changes[0].diff


def test_code_delete_removes_a_file_and_records_the_change(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    asyncio.run(call(instance, "code_write", {"path": "tools/old.py", "content": "x = 1\n"}))

    result = asyncio.run(call(instance, "code_delete", {"path": "tools/old.py"}))

    assert result == "Deleted code/tools/old.py."
    assert not (tmp_path / "code" / "tools" / "old.py").exists()
    deleted = ConfigChangeLog(instance.manifest.state_dir).history(None, 1)[0]
    assert deleted.file == "code/tools/old.py"
    assert deleted.actor is ConfigActor.AGENT
    assert "-x = 1" in deleted.diff


@pytest.mark.parametrize("path", ["../escape.py", "{root}/escape.py", "tools/../../escape.py", "."])
@pytest.mark.parametrize("name", ["code_write", "code_delete"])
def test_code_writes_refuse_a_path_outside_the_instance_code(
    tmp_path: Path, name: str, path: str
) -> None:
    instance = instance_at(tmp_path)
    (tmp_path / "escape.py").write_text("kept\n", encoding="utf-8")
    arguments: dict[str, JsonValue] = {"path": path.format(root=tmp_path)}
    if name == "code_write":
        arguments["content"] = "print('escaped')\n"

    with pytest.raises(ValueError, match="inside the instance code"):
        asyncio.run(call(instance, name, arguments))

    assert (tmp_path / "escape.py").read_text(encoding="utf-8") == "kept\n"
    assert ConfigChangeLog(instance.manifest.state_dir).history(None, 10) == []


def test_code_run_kills_a_script_past_the_bash_timeout(tmp_path: Path) -> None:
    (tmp_path / "kinby.toml").write_text(
        'id = "test"\n[models]\nmain = "openai:gpt-5"\n[tools]\nbash_timeout_seconds = 1\n'
    )
    instance = load_instance(tmp_path)
    asyncio.run(
        call(
            instance,
            "code_write",
            {
                "path": "slow.py",
                "content": "import time\nprint('started', flush=True)\ntime.sleep(30)\n",
            },
        )
    )

    with pytest.raises(TimeoutError, match=r"slow\.py timed out after 1 seconds\.\nstarted"):
        asyncio.run(call(instance, "code_run", {"script": "slow.py", "arguments": []}))


def test_code_run_cuts_output_past_the_bash_cap(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    asyncio.run(
        call(instance, "code_write", {"path": "loud.py", "content": "print('x' * 40_000)\n"})
    )

    output = asyncio.run(call(instance, "code_run", {"script": "loud.py", "arguments": []}))

    assert output == "x" * 30_000


def test_code_run_refuses_a_script_outside_the_instance_code(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    (tmp_path / "outside.py").write_text("print('ran')\n", encoding="utf-8")

    with pytest.raises(ValueError, match="inside the instance code"):
        asyncio.run(call(instance, "code_run", {"script": "../outside.py", "arguments": []}))


def test_a_secret_reaches_a_script_only_once_the_project_declares_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FOO_TOKEN", "tok-private")
    monkeypatch.setenv("FOO_PLAIN", "plain")
    instance = instance_at(tmp_path)
    script = "import os\n\nprint(os.environ.get('FOO_TOKEN'), os.environ.get('FOO_PLAIN'))\n"
    asyncio.run(call(instance, "code_write", {"path": "env.py", "content": script}))

    stripped = asyncio.run(call(instance, "code_run", {"script": "env.py", "arguments": []}))
    project = (tmp_path / "code" / "pyproject.toml").read_text(encoding="utf-8")
    asyncio.run(
        call(
            instance,
            "code_write",
            {
                "path": "pyproject.toml",
                "content": project.replace("secrets = []", 'secrets = ["FOO_TOKEN"]'),
            },
        )
    )
    declared = asyncio.run(call(instance, "code_run", {"script": "env.py", "arguments": []}))

    assert stripped == "None plain\n"
    assert declared == "tok-private plain\n"


def test_the_code_run_approval_names_the_script_and_its_arguments(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    arguments: dict[str, JsonValue] = {"script": "greet.py", "arguments": ["--name", "Jorge"]}

    payloads = asyncio.run(turn(instance, [("code_run", arguments)], PermissionMode.ASK))

    approval = next(p for p in payloads if isinstance(p, ApprovalRequested))
    assert approval.name == "code_run"
    assert approval.arguments == arguments
    assert approval.rule == "mode.ask.write"


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("code_write", {"path": "greet.py", "content": "print('hi')\n"}),
        ("code_delete", {"path": "greet.py"}),
        ("code_add_package", {"name": "requests"}),
        ("code_run", {"script": "greet.py", "arguments": []}),
    ],
)
def test_code_tools_ask_in_ask_mode_and_are_denied_in_read_only(
    tmp_path: Path, name: str, arguments: dict[str, JsonValue]
) -> None:
    (tmp_path / "asked").mkdir()
    (tmp_path / "denied").mkdir()
    asked = instance_at(tmp_path / "asked")
    denied = instance_at(tmp_path / "denied")

    asked_payloads = asyncio.run(turn(asked, [(name, arguments)], PermissionMode.ASK))
    denied_payloads = asyncio.run(turn(denied, [(name, arguments)], PermissionMode.READ_ONLY))

    assert any(isinstance(p, ApprovalRequested) for p in asked_payloads)
    gated = next(p for p in denied_payloads if isinstance(p, ToolGated))
    assert gated.action is GateOutcome.DENY
    assert not (tmp_path / "asked" / "code").exists()
    assert not (tmp_path / "denied" / "code").exists()
