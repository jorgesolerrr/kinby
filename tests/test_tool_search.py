"""Tool search on an instance's turns: a turn finds a searchable tool, reads its definition and
calls it by name, and the gate checks that call as a call to the tool itself (ADR 0129)."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk, BaseMessage, ToolMessage
from pydantic import JsonValue

from kinby.contracts import (
    ApprovalRequested,
    CallVia,
    PermissionMode,
    RoutineGrant,
    RoutineName,
    TakeoverRequested,
    ToolCall,
    ToolResult,
    ToolState,
    TurnCompleted,
    Warning,
)
from kinby.instance import Instance
from kinby.instance.routine_grants import RoutineGrants
from kinby.instance.skills_lock import LockedMcpServer, SkillsLock, write_lock
from tests.test_instance_code import model_turn, scripted, turn
from tests.test_learning_review import failed_tool, work_turn
from tests.test_mcp_servers import asked, echo, launchers, pick  # noqa: F401
from tests.test_routines import RoutineModel, fire, instance_at, routine_file
from tests.test_tool_groups import instance_with

SEARCH_TOOLS = {"search_tools", "describe_tool", "call_tool"}


def sent(instance: Instance) -> set[str]:
    """The tools the model is sent on a turn of *instance*."""
    model = scripted([])
    asyncio.run(model_turn(instance, model, PermissionMode.ASK))
    [tools] = model.bound_tools
    return {tool.name for tool in tools}


def results(payloads: Sequence[object]) -> dict[str, ToolResult]:
    return {payload.name: payload for payload in payloads if isinstance(payload, ToolResult)}


def searchable(tmp_path: Path, *loaded: str, trust_hints: bool = False) -> Instance:
    """An instance that picks the echo server with its tools searchable but *loaded*."""
    instance = instance_at(tmp_path)
    pick(instance, trust_hints=trust_hints, echo=echo(*loaded, default_state=ToolState.SEARCHABLE))
    return instance


def pick_my_echo(instance: Instance) -> None:
    """Pick the echo server as my-echo, a name a Python name cannot hold, its tools searchable."""
    server = LockedMcpServer(server=echo(default_state=ToolState.SEARCHABLE))
    write_lock(instance.path, SkillsLock(mcp_servers={"my-echo": server}))


def calling(name: str, arguments: dict[str, JsonValue]) -> tuple[str, dict[str, JsonValue]]:
    return ("call_tool", {"name": name, "arguments": arguments})


def test_a_turn_has_the_search_tools_only_while_one_of_its_tools_is_searchable(
    tmp_path: Path,
) -> None:
    instance = instance_at(tmp_path)
    pick(instance, echo=echo("look", "post"))
    without = sent(instance)
    pick(instance, echo=echo("look", default_state=ToolState.SEARCHABLE))

    assert not SEARCH_TOOLS & without
    assert sent(instance) >= SEARCH_TOOLS


def test_the_search_tools_definitions_stay_the_same_while_the_search_set_changes(
    tmp_path: Path,
) -> None:
    def definitions() -> dict[str, object]:
        model = scripted([])
        asyncio.run(model_turn(instance, model, PermissionMode.ASK))
        [tools] = model.bound_tools
        return {tool.name: tool.args for tool in tools if tool.name in SEARCH_TOOLS} | {
            f"{tool.name} description": tool.description
            for tool in tools
            if tool.name in SEARCH_TOOLS
        }

    instance = searchable(tmp_path, "look")
    before = definitions()
    pick(instance, echo=echo(default_state=ToolState.SEARCHABLE))

    assert definitions() == before


def test_search_ranks_by_a_word_in_the_description_and_gives_each_name_line_and_write_flag(
    tmp_path: Path,
) -> None:
    instance = searchable(tmp_path, trust_hints=True)

    payloads = asyncio.run(
        turn(instance, [("search_tools", {"query": "repeat a line"})], PermissionMode.ASK)
    )

    found = results(payloads)["search_tools"]
    assert not found.error, found.output
    lines = found.output.splitlines()
    assert lines[0] == "echo__look (reads): Repeat a line of text."
    assert "echo__post (writes): Post a line of text." in lines
    assert not any("echo__environment" in line for line in lines)
    assert asked(payloads) == []


def test_search_returns_at_most_its_limit(tmp_path: Path) -> None:
    instance = searchable(tmp_path)

    payloads = asyncio.run(
        turn(instance, [("search_tools", {"query": "text", "limit": 1})], PermissionMode.ASK)
    )

    assert len(results(payloads)["search_tools"].output.splitlines()) == 1


def test_describe_gives_the_description_the_schemas_and_the_python_signature(
    tmp_path: Path,
) -> None:
    instance = searchable(tmp_path)

    payloads = asyncio.run(
        turn(instance, [("describe_tool", {"name": "echo__look"})], PermissionMode.ASK)
    )

    described = results(payloads)["describe_tool"]
    assert not described.error, described.output
    assert "Repeat a line of text." in described.output
    assert '"text"' in described.output
    assert "Output schema" in described.output
    assert "async def echo__look(*, text: str)" in described.output
    assert asked(payloads) == []


def test_a_python_name_takes_an_underscore_for_each_character_a_name_cannot_hold(
    tmp_path: Path,
) -> None:
    instance = instance_at(tmp_path)
    pick_my_echo(instance)

    payloads = asyncio.run(
        turn(instance, [("describe_tool", {"name": "my-echo__look"})], PermissionMode.ASK)
    )

    assert "async def my_echo__look(*, text: str)" in results(payloads)["describe_tool"].output


def test_two_tools_that_reach_one_python_name_are_both_left_out_of_scripts(
    tmp_path: Path,
) -> None:
    instance = instance_at(tmp_path)
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
        turn(
            instance,
            [
                ("describe_tool", {"name": "my-echo__look"}),
                ("describe_tool", {"name": "my_echo__look"}),
            ],
            PermissionMode.ASK,
        )
    )

    described = [
        payload.output
        for payload in payloads
        if isinstance(payload, ToolResult) and payload.name == "describe_tool"
    ]
    assert len(described) == 2
    for output in described:
        assert "async def" not in output
        assert "Scripts leave this tool out" in output


def test_a_call_through_search_is_a_call_to_the_real_tool_and_asks_in_ask_mode(
    tmp_path: Path,
) -> None:
    instance = searchable(tmp_path)

    payloads = asyncio.run(
        turn(instance, [calling("echo__post", {"text": "hi"})], PermissionMode.ASK)
    )

    [approval] = [payload for payload in payloads if isinstance(payload, ApprovalRequested)]
    assert (approval.name, approval.arguments) == ("echo__post", {"text": "hi"})
    [call] = [payload for payload in payloads if isinstance(payload, ToolCall)]
    assert (call.name, call.arguments, call.write, call.via) == (
        "echo__post",
        {"text": "hi"},
        True,
        CallVia.TOOL_SEARCH,
    )


def test_a_trusted_read_only_tool_runs_unasked_through_search(tmp_path: Path) -> None:
    instance = searchable(tmp_path, trust_hints=True)

    payloads = asyncio.run(
        turn(instance, [calling("echo__look", {"text": "hi"})], PermissionMode.ASK)
    )

    assert asked(payloads) == []
    called = results(payloads)["echo__look"]
    assert (called.error, called.output) == (False, "looked: hi")


def test_call_tool_calls_a_loaded_tool_too(tmp_path: Path) -> None:
    instance = searchable(tmp_path, "look")

    payloads = asyncio.run(
        turn(instance, [calling("echo__look", {"text": "hi"})], PermissionMode.FULL_ACCESS)
    )

    assert results(payloads)["echo__look"].output == "looked: hi"


def test_wrong_arguments_are_refused_before_any_approval_naming_each_wrong_field(
    tmp_path: Path,
) -> None:
    instance = searchable(tmp_path)

    payloads = asyncio.run(
        turn(instance, [calling("echo__post", {"text": 3, "loud": True})], PermissionMode.ASK)
    )

    assert asked(payloads) == []
    refused = results(payloads)["echo__post"]
    assert refused.error
    assert "text" in refused.output
    assert "3 is not of type 'string'" in refused.output


def test_a_missing_argument_is_named(tmp_path: Path) -> None:
    instance = searchable(tmp_path)

    payloads = asyncio.run(turn(instance, [calling("echo__post", {})], PermissionMode.ASK))

    assert asked(payloads) == []
    assert "'text' is a required property" in results(payloads)["echo__post"].output


@pytest.mark.parametrize("name", ["echo__post", "echo__nothing"])
def test_an_off_or_unknown_tool_is_an_error_that_points_to_search(
    tmp_path: Path, name: str
) -> None:
    instance = instance_at(tmp_path)
    library = echo(default_state=ToolState.SEARCHABLE).model_copy(
        update={"tool_states": {"post": ToolState.OFF}}
    )
    pick(instance, echo=library)

    payloads = asyncio.run(turn(instance, [calling(name, {"text": "hi"})], PermissionMode.ASK))

    assert asked(payloads) == []
    refused = results(payloads)["call_tool"]
    assert refused.error
    assert name in refused.output
    assert "search_tools" in refused.output


def test_a_large_result_through_search_is_cut_and_saved(tmp_path: Path) -> None:
    instance = searchable(tmp_path)

    payloads = asyncio.run(
        turn(instance, [calling("echo__look", {"text": "x" * 40_000})], PermissionMode.FULL_ACCESS)
    )

    called = results(payloads)["echo__look"]
    assert called.size == len("looked: ") + 40_000
    assert called.output.startswith("looked: " + "x" * (30_000 - len("looked: ")) + "\n")


def test_the_takeover_still_asks_through_its_own_request(tmp_path: Path) -> None:
    instance = instance_with(tmp_path, 'default_states = { browser = "searchable" }')

    payloads = asyncio.run(
        turn(instance, [calling("browser_takeover", {"reason": "Sign in"})], PermissionMode.ASK)
    )

    assert asked(payloads) == []
    assert any(isinstance(payload, TakeoverRequested) for payload in payloads)


def test_the_system_prompt_lists_each_searchable_group_with_its_count_and_line(
    tmp_path: Path,
) -> None:
    instance = instance_with(tmp_path, 'default_states = { memory = "searchable" }')
    pick(instance, echo=echo("look", default_state=ToolState.SEARCHABLE))
    model = scripted([])

    asyncio.run(model_turn(instance, model, PermissionMode.ASK))

    system = str(model.sent[0][0].content)
    assert "- MCP server echo (4 tools): Repeats and posts lines of text." in system
    assert "- memory tools (" in system
    assert "default tools" not in system


def test_the_system_prompt_names_no_group_without_a_searchable_tool(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    pick(instance, echo=echo("look"))
    model = scripted([])

    asyncio.run(model_turn(instance, model, PermissionMode.ASK))

    assert str(model.sent[0][0].content) == "System prompt"


class SearchingModel(RoutineModel):
    """Searches for *query* at a routine turn's first step, then finishes."""

    def __init__(self, query: str) -> None:
        super().__init__()
        self._query = query

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.messages.append(list(messages))
        if isinstance(messages[-1], ToolMessage):
            yield AIMessageChunk(content="Done")
            return
        yield AIMessageChunk(
            content="",
            tool_calls=[
                {
                    "id": "search",
                    "name": "search_tools",
                    "args": {"query": self._query},
                    "type": "tool_call",
                }
            ],
        )

    @property
    def found(self) -> str:
        return str(self.messages[-1][-1].content)


def test_a_routines_code_step_tool_is_not_in_its_search_set(tmp_path: Path) -> None:
    instance = searchable(tmp_path)
    routine_file(
        instance, 'description: Look\nmode: full-access\nrun: echo__look\narguments: {"text": "hi"}'
    )
    model = SearchingModel("text")

    events = asyncio.run(fire(instance, model, seconds=60))

    assert isinstance(events[-1].payload, TurnCompleted)
    assert "echo__post" in model.found
    assert "echo__look" not in model.found


def test_the_learning_review_searches_no_write(tmp_path: Path) -> None:
    instance = searchable(tmp_path, trust_hints=True)
    routine_file(instance, "description: Review\nrun: learning_review")
    work_turn(
        instance,
        "Fix the runtime.",
        failed_tool("Permission denied."),
        at=datetime.now(UTC) - timedelta(days=2),
    )
    model = SearchingModel("text")

    events = asyncio.run(fire(instance, model, seconds=60))

    assert isinstance(events[-1].payload, TurnCompleted)
    assert "echo__look" in model.found
    assert "echo__post" not in model.found


class PostingModel(RoutineModel):
    """Posts through tool search at a routine turn's first step, then finishes."""

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.messages.append(list(messages))
        if isinstance(messages[-1], ToolMessage):
            yield AIMessageChunk(content="Done")
            return
        name, arguments = calling("echo__post", {"text": "hi"})
        yield AIMessageChunk(
            content="",
            tool_calls=[{"id": "post", "name": name, "args": arguments, "type": "tool_call"}],
        )

    @property
    def posted(self) -> str:
        return str(self.messages[-1][-1].content)


def test_a_routine_grant_covers_a_call_through_search(tmp_path: Path) -> None:
    instance = searchable(tmp_path)
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
    model = PostingModel()

    events = asyncio.run(fire(instance, model, seconds=60))

    assert isinstance(events[-1].payload, TurnCompleted)
    assert not any(isinstance(event.payload, ApprovalRequested) for event in events)
    assert "posted: hi" in model.posted


def test_a_tool_named_like_a_search_tool_is_replaced_by_it_with_a_warning(
    tmp_path: Path,
) -> None:
    instance = searchable(tmp_path)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "namesake.py").write_text(
        "from kinby.plugins import tool\n\n\n"
        "@tool(write=False)\n"
        "def search_tools(query: str) -> str:\n"
        '    """Search somewhere else."""\n'
        '    return "elsewhere"\n',
        encoding="utf-8",
    )
    model = scripted([("search_tools", {"query": "post"})])

    payloads = asyncio.run(model_turn(instance, model, PermissionMode.ASK))

    [tools] = model.bound_tools
    assert [tool.name for tool in tools].count("search_tools") == 1
    assert results(payloads)["search_tools"].output.startswith("echo__post")
    assert any(
        isinstance(payload, Warning) and '"search_tools"' in payload.message for payload in payloads
    )


def test_call_tool_does_not_reach_a_replaced_namesake(tmp_path: Path) -> None:
    instance = searchable(tmp_path)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "namesake.py").write_text(
        "from kinby.plugins import tool\n\n\n"
        "@tool(write=False)\n"
        "def describe_tool(name: str) -> str:\n"
        '    """Describe somewhere else."""\n'
        '    return "elsewhere"\n',
        encoding="utf-8",
    )

    payloads = asyncio.run(
        turn(instance, [calling("describe_tool", {"name": "echo__look"})], PermissionMode.ASK)
    )

    refused = results(payloads)["call_tool"]
    assert refused.error
    assert "elsewhere" not in str(payloads)
