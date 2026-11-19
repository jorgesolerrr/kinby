"""Code mode on an instance's turns: a script calls the turn's tools as async functions, and only
what it returns and prints reaches the model. The gate checks each call it makes as a call to the
tool itself, and asks about none (ADR 0129)."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk, BaseMessage, ToolMessage
from pydantic import JsonValue

from kinby.contracts import (
    CallVia,
    GateOutcome,
    PermissionMode,
    RoutineGrant,
    RoutineName,
    ToolCall,
    ToolGated,
    ToolResult,
    ToolState,
    TurnCompleted,
)
from kinby.core.turn_metrics import turn_metrics
from kinby.instance import Instance
from kinby.instance.routine_grants import RoutineGrants
from tests.helpers import GRAPH_EVENT_TIMEOUT
from tests.test_instance_code import model_turn, scripted, turn
from tests.test_learning_review import failed_tool, work_turn
from tests.test_mcp_servers import asked, echo, launchers, pick  # noqa: F401
from tests.test_routines import RoutineModel, fire, routine_file
from tests.test_tool_groups import instance_with, sent
from tests.test_tool_search import pick_my_echo


def scripting(
    tmp_path: Path, *loaded: str, trust_hints: bool = False, browser: bool = False
) -> Instance:
    """An instance with the default tools that picks the echo server, its tools searchable but
    *loaded*."""
    instance = instance_with(tmp_path, browser=browser)
    pick(instance, trust_hints=trust_hints, echo=echo(*loaded, default_state=ToolState.SEARCHABLE))
    return instance


def script(code: str) -> tuple[str, dict[str, JsonValue]]:
    return ("run_code", {"code": code})


def ran(payloads: Sequence[object]) -> list[ToolResult]:
    """The results of the turn's scripts."""
    return [
        payload
        for payload in payloads
        if isinstance(payload, ToolResult) and payload.name == "run_code"
    ]


def ran_once(payloads: Sequence[object]) -> ToolResult:
    [result] = ran(payloads)
    return result


def gated(payloads: Sequence[object], name: str) -> GateOutcome:
    [gate] = [
        payload for payload in payloads if isinstance(payload, ToolGated) and payload.name == name
    ]
    return gate.action


def test_run_code_is_a_default_tool_an_instance_can_switch_off(tmp_path: Path) -> None:
    (tmp_path / "on").mkdir()
    (tmp_path / "off").mkdir()

    assert "run_code" in sent(instance_with(tmp_path / "on"))
    assert "run_code" not in sent(instance_with(tmp_path / "off", 'states = { run_code = "off" }'))


def test_a_script_builds_one_value_from_two_tools_and_only_that_value_reaches_the_model(
    tmp_path: Path,
) -> None:
    instance = scripting(tmp_path, "look", trust_hints=True)
    model = scripted(
        [
            script(
                "looked = await echo__look(text='hi')\n"
                "started = await echo__arguments()\n"
                "print('started with', len(started), 'arguments')\n"
                "return looked.upper()"
            )
        ]
    )

    payloads = asyncio.run(model_turn(instance, model, PermissionMode.ASK))

    assert asked(payloads) == []
    result = ran_once(payloads)
    assert (result.error, result.output) == (False, "started with 0 arguments\nLOOKED: HI")
    [message] = [message for message in model.sent[-1] if isinstance(message, ToolMessage)]
    assert message.text == result.output
    assert not any("looked: hi" in message.text for message in model.sent[-1])


def test_each_call_a_script_makes_is_marked_as_made_from_its_script(tmp_path: Path) -> None:
    instance = scripting(tmp_path, trust_hints=True)

    payloads = asyncio.run(
        turn(instance, [script("await echo__look(text='hi')")], PermissionMode.ASK)
    )

    calls = [payload for payload in payloads if isinstance(payload, ToolCall)]
    assert [(call.name, call.via, call.script_call_id) for call in calls] == [
        ("run_code", None, None),
        ("echo__look", CallVia.SCRIPT, "call-0"),
    ]


def test_turn_metrics_count_a_scripts_calls_apart_from_direct_calls(tmp_path: Path) -> None:
    instance = scripting(tmp_path, trust_hints=True)
    routine_file(instance, "description: Look")
    model = ScriptingModel("await echo__look(text='a')\nawait echo__look(text='b')")

    events = asyncio.run(fire(instance, model, seconds=60))

    [record] = turn_metrics(events).records
    assert record.tool_calls == {"run_code": 1}
    assert record.script_tool_calls == {"echo__look": 2}


def test_a_function_returns_parsed_json_or_else_the_text(tmp_path: Path) -> None:
    instance = scripting(tmp_path, trust_hints=True)

    payloads = asyncio.run(
        turn(
            instance,
            [script("return [await echo__arguments(), await echo__look(text='hi')]")],
            PermissionMode.ASK,
        )
    )

    assert ran_once(payloads).output == "[[], 'looked: hi']"


def test_a_script_calls_a_write_the_gate_allows(tmp_path: Path) -> None:
    instance = scripting(tmp_path)

    payloads = asyncio.run(
        turn(instance, [script("return await echo__post(text='hi')")], PermissionMode.FULL_ACCESS)
    )

    assert ran_once(payloads).output == "posted: hi"


def test_a_call_the_gate_would_ask_about_raises_in_the_script_and_asks_nothing(
    tmp_path: Path,
) -> None:
    instance = scripting(tmp_path)

    payloads = asyncio.run(
        turn(
            instance,
            [
                script(
                    "try:\n"
                    "    await echo__post(text='hi')\n"
                    "except Exception as error:\n"
                    "    return str(error)"
                )
            ],
            PermissionMode.ASK,
        )
    )

    assert asked(payloads) == []
    output = ran_once(payloads).output
    assert "echo__post" in output
    assert "directly" in output
    assert "posted" not in output
    assert gated(payloads, "echo__post") == GateOutcome.ASK


def test_a_denied_call_raises_with_the_gates_reason(tmp_path: Path) -> None:
    instance = scripting(tmp_path)

    payloads = asyncio.run(
        turn(
            instance,
            [
                script(
                    "try:\n"
                    "    await echo__post(text='hi')\n"
                    "except Exception as error:\n"
                    "    return str(error)"
                )
            ],
            PermissionMode.READ_ONLY,
        )
    )

    assert 'Tool "echo__post" was denied by policy rule' in ran_once(payloads).output
    assert gated(payloads, "echo__post") == GateOutcome.DENY


def test_a_tool_error_raises_with_the_tools_message_and_the_script_can_catch_it(
    tmp_path: Path,
) -> None:
    instance = instance_with(tmp_path, browser=False)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "fail.py").write_text(
        "from kinby.plugins import tool\n\n\n"
        "@tool(write=False)\n"
        "def fail(reason: str) -> str:\n"
        '    """Fail for a reason."""\n'
        "    raise ValueError(reason)\n",
        encoding="utf-8",
    )

    payloads = asyncio.run(
        turn(
            instance,
            [
                script(
                    "try:\n"
                    "    await fail(reason='out of paper')\n"
                    "except Exception as error:\n"
                    "    return f'caught {error}'"
                )
            ],
            PermissionMode.ASK,
        )
    )

    assert ran_once(payloads).output == "caught ValueError: out of paper"


@pytest.mark.parametrize(
    "name", ["run_code", "browser_takeover", "search_tools", "describe_tool", "call_tool"]
)
def test_a_script_cannot_call_run_code_a_takeover_or_the_search_tools(
    tmp_path: Path, name: str
) -> None:
    instance = scripting(tmp_path, browser=True)

    payloads = asyncio.run(turn(instance, [script(f"await {name}()")], PermissionMode.ASK))

    result = ran_once(payloads)
    assert result.error
    assert "NameError" in result.output


def test_a_script_cannot_call_two_tools_that_reach_one_python_name(tmp_path: Path) -> None:
    instance = instance_with(tmp_path, browser=False)
    pick_my_echo(instance)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "clash.py").write_text(
        "from kinby.plugins import tool\n\n\n"
        "@tool(write=False)\n"
        "def my_echo__look(text: str) -> str:\n"
        '    """Look somewhere else."""\n'
        "    return text\n",
        encoding="utf-8",
    )

    payloads = asyncio.run(
        turn(instance, [script("await my_echo__look(text='hi')")], PermissionMode.FULL_ACCESS)
    )

    assert "NameError" in ran_once(payloads).output


@pytest.mark.parametrize(
    "code",
    [
        "return open('kinby.toml').read()",
        "import socket",
        "import os\nreturn os.environ['PATH']",
        "import os\nreturn os.getenv('PATH')",
    ],
)
def test_a_script_reads_no_file_opens_no_socket_and_reads_no_environment(
    tmp_path: Path, code: str
) -> None:
    instance = scripting(tmp_path)

    payloads = asyncio.run(turn(instance, [script(code)], PermissionMode.FULL_ACCESS))

    assert ran_once(payloads).error


def test_each_script_starts_fresh(tmp_path: Path) -> None:
    instance = scripting(tmp_path)

    payloads = asyncio.run(
        turn(instance, [script("kept = 1\nreturn kept"), script("return kept")], PermissionMode.ASK)
    )

    first, second = ran(payloads)
    assert (first.error, first.output) == (False, "1")
    assert second.error
    assert "NameError" in second.output


def test_urls_in_a_scripts_result_are_not_seen_urls(tmp_path: Path) -> None:
    instance = scripting(tmp_path)

    payloads = asyncio.run(
        turn(
            instance,
            [
                script("return 'https://example.com/page'"),
                ("web_fetch", {"url": "https://example.com/page"}),
            ],
            PermissionMode.ASK,
        )
    )

    assert ran_once(payloads).output == "https://example.com/page"
    assert asked(payloads) == ["web_fetch"]


class ScriptingModel(RoutineModel):
    """Runs *code* at a routine turn's first step, then finishes."""

    def __init__(self, code: str) -> None:
        super().__init__()
        self._code = code

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.messages.append(list(messages))
        if isinstance(messages[-1], ToolMessage):
            yield AIMessageChunk(content="Done")
            return
        name, arguments = script(self._code)
        yield AIMessageChunk(
            content="",
            tool_calls=[{"id": "script", "name": name, "args": arguments, "type": "tool_call"}],
        )

    @property
    def ran(self) -> str:
        return str(self.messages[-1][-1].content)


def test_a_routine_grant_covers_a_call_from_a_script(tmp_path: Path) -> None:
    instance = scripting(tmp_path)
    routine_file(instance, "description: Post\nmode: ask")
    instance.manifest.state_dir.mkdir(exist_ok=True)
    RoutineGrants(instance.manifest.state_dir).grant(
        RoutineGrant(
            routine=RoutineName("news"),
            tool="echo__post",
            granted_at=datetime.now(UTC),
            approval_id=uuid4(),
        )
    )
    model = ScriptingModel("return await echo__post(text='hi')")

    events = asyncio.run(fire(instance, model, seconds=60))

    assert isinstance(events[-1].payload, TurnCompleted)
    assert asked([event.payload for event in events]) == []
    assert model.ran == "posted: hi"


def test_a_tool_the_turns_limits_remove_is_no_function_of_its_scripts(tmp_path: Path) -> None:
    instance = scripting(tmp_path, trust_hints=True)
    routine_file(instance, "description: Review\nrun: learning_review")
    work_turn(
        instance,
        "Fix the runtime.",
        failed_tool("Permission denied."),
        at=datetime.now(UTC) - timedelta(days=2),
    )
    model = ScriptingModel(
        "looked = await echo__look(text='hi')\n"
        "try:\n"
        "    await echo__post(text='hi')\n"
        "except NameError:\n"
        "    return looked + ', and no post'"
    )

    events = asyncio.run(fire(instance, model, seconds=60))

    assert isinstance(events[-1].payload, TurnCompleted)
    assert model.ran == "looked: hi, and no post"


def tool_file(tmp_path: Path, name: str, source: str) -> None:
    (tmp_path / "tools").mkdir(exist_ok=True)
    (tmp_path / "tools" / f"{name}.py").write_text(
        f"import asyncio\n\nfrom kinby.plugins import tool\n\n\n{source}", encoding="utf-8"
    )


def test_a_script_stops_after_its_running_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("kinby.plugins.code_mode.SCRIPT_SECONDS", 0.5)
    instance = scripting(tmp_path)

    payloads = asyncio.run(turn(instance, [script("while True:\n    pass")], PermissionMode.ASK))

    result = ran_once(payloads)
    assert result.error
    assert "The script passed its limit of 0.5 seconds of running time." in result.output


def test_time_a_script_waits_on_a_tool_is_not_its_running_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("kinby.plugins.code_mode.SCRIPT_SECONDS", 0.5)
    instance = instance_with(tmp_path, browser=False)
    tool_file(
        tmp_path,
        "nap",
        "@tool(write=False)\n"
        "async def nap() -> str:\n"
        '    """Nap for a second."""\n'
        "    await asyncio.sleep(1)\n"
        '    return "rested"\n',
    )

    payloads = asyncio.run(turn(instance, [script("return await nap()")], PermissionMode.ASK))

    assert ran_once(payloads).output == "rested"


def test_a_script_stops_at_its_memory(tmp_path: Path) -> None:
    instance = scripting(tmp_path)

    payloads = asyncio.run(
        turn(instance, [script("kept = 'a' * (300 * 1024 * 1024)")], PermissionMode.ASK)
    )

    result = ran_once(payloads)
    assert result.error
    assert "The script passed its limit of 256 MiB of memory." in result.output


def test_a_script_stops_at_its_tool_calls(tmp_path: Path) -> None:
    instance = instance_with(tmp_path, browser=False)
    tool_file(
        tmp_path,
        "tick",
        '@tool(write=False)\ndef tick() -> str:\n    """Tick."""\n    return "tick"\n',
    )

    payloads = asyncio.run(
        turn(
            instance,
            [
                script(
                    "try:\n"
                    "    for each in range(101):\n"
                    "        await tick()\n"
                    "except Exception:\n"
                    "    return 'kept going'"
                )
            ],
            PermissionMode.ASK,
        )
    )

    result = ran_once(payloads)
    assert result.error
    assert "The script passed its limit of 100 tool calls." in result.output
    ticks = [
        payload
        for payload in payloads
        if isinstance(payload, ToolResult) and payload.name == "tick"
    ]
    assert len(ticks) == 100


def test_a_syntax_error_comes_back_with_its_line(tmp_path: Path) -> None:
    instance = scripting(tmp_path)

    payloads = asyncio.run(turn(instance, [script("kept = 1\nkept = )")], PermissionMode.ASK))

    result = ran_once(payloads)
    assert result.error
    assert "line 2" in result.output
    assert "SyntaxError" in result.output


def test_a_runtime_error_comes_back_with_its_line(tmp_path: Path) -> None:
    instance = scripting(tmp_path)

    payloads = asyncio.run(
        turn(instance, [script("kept = 1\nreturn kept / 0")], PermissionMode.ASK)
    )

    result = ran_once(payloads)
    assert result.error
    assert "line 2" in result.output
    assert "ZeroDivisionError: division by zero" in result.output


def test_a_failed_scripts_result_names_the_tool_calls_that_finished(tmp_path: Path) -> None:
    instance = scripting(tmp_path, trust_hints=True)

    payloads = asyncio.run(
        turn(
            instance,
            [
                script(
                    "await echo__look(text='one')\n"
                    "try:\n"
                    "    await echo__post(text='two')\n"
                    "except Exception:\n"
                    "    pass\n"
                    "await echo__arguments()\n"
                    "raise ValueError('stopped')"
                )
            ],
            PermissionMode.ASK,
        )
    )

    output = ran_once(payloads).output
    assert "ValueError: stopped" in output
    assert output.endswith("The tool calls that finished: echo__look, echo__arguments.")


def test_a_script_that_fails_before_any_call_says_none_finished(tmp_path: Path) -> None:
    instance = scripting(tmp_path)

    payloads = asyncio.run(
        turn(instance, [script("raise ValueError('stopped')")], PermissionMode.ASK)
    )

    assert ran_once(payloads).output.endswith("No tool call finished.")


#: Set by the tool `wait` once a script calls it, and what became of each call.
waiting = asyncio.Event()
waits: list[str] = []


def test_interrupting_the_turn_cancels_the_script_and_its_call_in_flight(tmp_path: Path) -> None:
    instance = instance_with(tmp_path, browser=False)
    tool_file(
        tmp_path,
        "wait",
        "from tests import test_code_mode\n\n\n"
        "@tool(write=False)\n"
        "async def wait() -> str:\n"
        '    """Wait for good."""\n'
        "    test_code_mode.waiting.set()\n"
        "    try:\n"
        "        await asyncio.Event().wait()\n"
        "    except asyncio.CancelledError:\n"
        "        test_code_mode.waits.append('cancelled')\n"
        "        raise\n"
        "    return 'waited'\n",
    )

    async def interrupt() -> None:
        running = asyncio.create_task(
            turn(instance, [script("await wait()\nawait wait()")], PermissionMode.ASK)
        )
        await asyncio.wait_for(waiting.wait(), timeout=GRAPH_EVENT_TIMEOUT)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert waits == ["cancelled"]

    asyncio.run(interrupt())
