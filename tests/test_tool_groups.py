"""The built-in tool groups on an instance's turns: the default tools, the memory tools, the
browser tools and the authored tools, each tool loaded, searchable or off (ADR 0129)."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import JsonValue

from kinby.contracts import (
    ApprovalDecision,
    ErrorCode,
    Event,
    Payload,
    PermissionMode,
    SystemPrompt,
    ToolResult,
    TurnFailed,
)
from kinby.core.turn_runner import LangGraphRunner
from kinby.core.turns import ApprovalAnswer, ParkedTurn, PreparedTurnRequest, TurnContext
from kinby.instance import Instance, load_instance
from kinby.plugins.listed_tools import authored_tools_listed
from tests.test_authored_tools import expose, offline_uv  # noqa: F401
from tests.test_instance_code import model_turn, scripted
from tests.test_mcp_servers import result
from tests.test_routines import RoutineModel, fire, routine_file


def instance_with(path: Path, tools: str = "", *, browser: bool = True) -> Instance:
    """An instance whose manifest's `[tools]` table holds *tools*."""
    (path / "kinby.toml").write_text(
        f'id = "test"\nbrowser = {str(browser).lower()}\n[models]\nmain = "openai:gpt-5"\n'
        f'recap = "custom:unpriced"\n[tools]\n{tools}\n'
    )
    return load_instance(path)


def sent(instance: Instance) -> set[str]:
    """The tools the model is sent on a turn of *instance*."""
    model = scripted([])
    asyncio.run(model_turn(instance, model, PermissionMode.ASK))
    [tools] = model.bound_tools
    return {tool.name for tool in tools}


def reachable(instance: Instance, tool: str) -> bool:
    """Whether a routine's code step finds *tool*, which a turn's search set holds too. The call
    itself may fail."""
    routine_file(instance, f"description: Run\nmode: full-access\nrun: {tool}\narguments: {{}}")
    ended = asyncio.run(fire(instance, RoutineModel(), seconds=60))[-1].payload
    return not (
        isinstance(ended, TurnFailed)
        and ended.code is ErrorCode.NOT_FOUND
        and f'Code step tool "{tool}"' in ended.message
    )


def test_a_manifest_that_changes_nothing_loads_every_group(tmp_path: Path) -> None:
    instance = instance_with(tmp_path)
    expose(instance)

    assert {"bash", "read", "memory_search", "remember", "browser_open", "echo"} <= sent(instance)


@pytest.mark.parametrize(
    ("group", "tool"),
    [
        ("defaults", "glob"),
        ("memory", "memory_search"),
        ("browser", "browser_profiles"),
        ("authored", "echo"),
    ],
)
def test_a_searchable_group_is_not_sent_and_its_tools_are_in_the_search_set(
    tmp_path: Path, group: str, tool: str
) -> None:
    instance = instance_with(tmp_path, f'default_states = {{ {group} = "searchable" }}')
    expose(instance)

    assert tool not in sent(instance)
    assert reachable(instance, tool)


@pytest.mark.parametrize(
    ("switch", "tool"),
    [
        ("defaults = false", "glob"),
        ("memory = false", "memory_search"),
        ("authored = false", "echo"),
    ],
)
def test_a_group_switched_off_offers_none_of_its_tools(
    tmp_path: Path, switch: str, tool: str
) -> None:
    instance = instance_with(tmp_path, f'{switch}\nstates = {{ {tool} = "loaded" }}')
    expose(instance)

    assert tool not in sent(instance)
    assert not reachable(instance, tool)


def test_the_browser_switch_is_the_browser_groups(tmp_path: Path) -> None:
    instance = instance_with(tmp_path, 'states = { browser_profiles = "loaded" }', browser=False)

    assert not any(name.startswith("browser_") for name in sent(instance))
    assert not reachable(instance, "browser_profiles")


def test_a_group_whose_default_is_off_offers_only_its_tools_with_a_state(
    tmp_path: Path,
) -> None:
    instance = instance_with(
        tmp_path, 'default_states = { memory = "off" }\nstates = { memory_open = "loaded" }'
    )

    offered = sent(instance)

    assert "memory_open" in offered
    assert "memory_search" not in offered
    assert not reachable(instance, "memory_search")


def test_a_tool_with_its_own_state_beats_its_groups_default(tmp_path: Path) -> None:
    instance = instance_with(
        tmp_path,
        'default_states = { memory = "searchable" }\n'
        'states = { remember = "loaded", forget = "off" }',
    )

    offered = sent(instance)

    assert "remember" in offered
    assert not {"memory_search", "memory_open", "forget"} & offered
    assert reachable(instance, "memory_search")
    assert not reachable(instance, "forget")


def test_a_manifest_that_switched_default_tools_off_keeps_them_off(tmp_path: Path) -> None:
    instance = instance_with(tmp_path, 'off = ["bash", "glob"]')

    offered = sent(instance)

    assert not {"bash", "glob"} & offered
    assert {"read", "grep"} <= offered
    assert not reachable(instance, "glob")


def test_a_manifest_that_switched_all_default_tools_off_has_the_group_off(
    tmp_path: Path,
) -> None:
    instance = instance_with(tmp_path, "defaults = false")

    assert not {"bash", "read", "glob", "web_fetch"} & sent(instance)


@pytest.mark.parametrize("state", ["loaded", "searchable"])
def test_web_search_needs_its_backends_key_whatever_its_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    instance = instance_with(tmp_path, f'states = {{ web_search = "{state}" }}')
    monkeypatch.delenv(instance.manifest.tools.search.key_variable, raising=False)

    assert "web_search" not in sent(instance)
    assert not reachable(instance, "web_search")


def test_a_tool_outside_the_groups_stays_loaded_whatever_state_it_is_given(
    tmp_path: Path,
) -> None:
    instance = instance_with(
        tmp_path,
        'default_states = { defaults = "off", memory = "off" }\n'
        'states = { routine_list = "off", skill = "searchable" }',
    )

    assert {"routine_list", "skill"} <= sent(instance)


def test_a_turn_records_the_authored_tools_it_loaded_with_their_descriptions(
    tmp_path: Path,
) -> None:
    instance = instance_with(tmp_path)
    expose(instance)
    assert authored_tools_listed(instance.manifest.state_dir) is None

    sent(instance)

    listed = authored_tools_listed(instance.manifest.state_dir)
    assert listed is not None
    [echo] = listed
    assert (echo.name, echo.description) == ("echo", "Repeat a line of text.")
    assert echo.tokens > 0


def resumed(
    instance: Instance, path: Path, call: tuple[str, dict[str, JsonValue]], changed: str
) -> ToolResult:
    """The result of *call*, which parks a turn of *instance* on an approval, when the manifest's
    `[tools]` table is *changed* and another turn prepared before the approval resumes it."""
    model = scripted([call])
    runner = LangGraphRunner(instance, model_factory=lambda _: model)
    payloads: list[Payload] = []

    async def scenario() -> None:
        preparation = runner.prepare_for_turn()
        request = PreparedTurnRequest(
            thread_id=uuid4(),
            turn_id=uuid4(),
            message="Go.",
            model=preparation.model,
            permission_mode=PermissionMode.ASK,
            system_prompt=SystemPrompt("System prompt"),
        )

        async def emit(payload: Payload) -> Event:
            payloads.append(payload)
            return Event(
                sequence=len(payloads),
                thread_id=request.thread_id,
                turn_id=request.turn_id,
                payload=payload,
                timestamp=datetime.now(UTC),
            )

        context = TurnContext(preparation.budgets, emit)
        assert isinstance(await runner.run(request, context), ParkedTurn)
        instance_with(path, changed)
        runner.prepare_for_turn()
        await runner.resume(request, ApprovalAnswer(ApprovalDecision.APPROVE), context)

    asyncio.run(scenario())
    return result(payloads)


@pytest.mark.parametrize(
    ("call", "changed"),
    [
        (
            ("remember", {"description": "Tea", "subjects": ["Jorge"], "body": "Green."}),
            'default_states = { memory = "searchable" }',
        ),
        (("write", {"path": "note.txt", "content": "hi"}), 'states = { write = "off" }'),
        (("write", {"path": "note.txt", "content": "hi"}), "defaults = false"),
    ],
)
def test_a_turn_resumed_from_an_approval_keeps_the_states_it_started_with(
    tmp_path: Path, call: tuple[str, dict[str, JsonValue]], changed: str
) -> None:
    called = resumed(instance_with(tmp_path), tmp_path, call, changed)

    assert called.error is False, called.output
