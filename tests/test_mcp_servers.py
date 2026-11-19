"""MCP servers on an instance's turns: each picked server's loaded tools, named by server and
gated as writes until the user trusts the server's read-only hints (ADR 0111, ADR 0129)."""

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from kinby.contracts import (
    INSTANCE_LIST,
    ApprovalDecision,
    ApprovalRequested,
    ErrorCode,
    Event,
    InstanceListCommand,
    InstanceListResult,
    InstanceNotice,
    McpServer,
    McpServerFailed,
    Payload,
    PermissionMode,
    SystemPrompt,
    ToolResult,
    ToolState,
    TurnCompleted,
    TurnFailed,
)
from kinby.core.contract_server import CONTROL_TOKEN_VARIABLE
from kinby.core.turn_runner import LangGraphRunner
from kinby.core.turns import ApprovalAnswer, ParkedTurn, PreparedTurnRequest, TurnContext
from kinby.hub import Hub
from kinby.instance import Instance, Serve, inspect_instance
from kinby.instance.skills_lock import LockedMcpServer, SkillsLock, write_lock
from kinby.plugins import mcp_connection
from kinby.plugins.hub import HUB_SCOPES_VARIABLE, HUB_URL_VARIABLE
from kinby.plugins.listed_tools import mcp_tools_listed
from kinby.plugins.mcp import failed_mcp_servers
from kinby.plugins.mcp_connection import STDERR_TAIL
from tests.mcp_oauth_server import OAuthMcpServer
from tests.test_hub import FakeControl, hub_at, hub_client, started_instance
from tests.test_hub_mcp_sign_ins import _Clock, _picked, _signed_in
from tests.test_hub_routing import control_token
from tests.test_hub_server import served, url
from tests.test_instance_code import model_turn, scripted, turn
from tests.test_routines import RoutineModel, fire, instance_at, routine_file

SERVER = Path(__file__).with_name("mcp_echo_server.py")


def echo(
    *loaded: str,
    env: dict[str, str] | None = None,
    package: str = "echo-server",
    args: tuple[str, ...] = (),
    command: str = "uvx",
    default_state: ToolState = ToolState.OFF,
) -> McpServer:
    """The echo server's library entry, with the tools *loaded* and the others in
    *default_state*."""
    return McpServer.model_validate(
        {
            "entry": {"command": command, "args": [package, *args], "env": env or {}},
            "default_state": default_state,
            "tool_states": dict.fromkeys(loaded, ToolState.LOADED),
        }
    )


@pytest.fixture(autouse=True)
def launchers(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for uvx and npx: each starts the echo server for the package echo-server, with the
    arguments after the package, starts one that never answers for silent-server, floods stderr
    before failing for noisy-server, and fails as a package missing from the registry for any
    other."""
    folder = tmp_path_factory.mktemp("bin")
    for launcher in ("uvx", "npx"):
        shim = folder / launcher
        shim.write_text(
            f"""#!/bin/sh
case "$1" in
  echo-server) shift; exec "{sys.executable}" "{SERVER}" "$@" ;;
  silent-server) while read -r _; do :; done; exit ;;
  noisy-server) seq 100000 >&2; exit 1 ;;
  *) echo "error: $1 was not found in the package registry" >&2; exit 1 ;;
esac
""",
            encoding="utf-8",
        )
        shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")


def pick(instance: Instance, *, trust_hints: bool = False, **servers: McpServer) -> None:
    write_lock(
        instance.path,
        SkillsLock(
            mcp_servers={
                name: LockedMcpServer(server=server, trust_hints=trust_hints)
                for name, server in servers.items()
            }
        ),
    )


def result(payloads: Sequence[object]) -> ToolResult:
    [called] = [payload for payload in payloads if isinstance(payload, ToolResult)]
    return called


def asked(payloads: Sequence[object]) -> list[str]:
    return [payload.name for payload in payloads if isinstance(payload, ApprovalRequested)]


@pytest.mark.parametrize("command", ["uvx", "npx"])
def test_a_picked_servers_included_tool_is_named_by_its_server_and_a_turn_calls_it(
    tmp_path: Path, command: str
) -> None:
    instance = instance_at(tmp_path)
    pick(instance, echo=echo("look", "post", command=command))

    payloads = asyncio.run(
        turn(instance, [("echo__look", {"text": "hi"})], PermissionMode.FULL_ACCESS)
    )

    called = result(payloads)
    assert (called.error, called.output) == (False, "looked: hi")


def test_a_result_over_the_cap_is_cut_by_the_runner_not_the_server(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    pick(instance, echo=echo("look"))

    payloads = asyncio.run(
        turn(instance, [("echo__look", {"text": "x" * 40_000})], PermissionMode.FULL_ACCESS)
    )

    called = result(payloads)
    assert called.size == len("looked: ") + 40_000
    assert called.output.startswith("looked: " + "x" * (30_000 - len("looked: ")) + "\n")


def test_an_off_tool_cannot_be_called(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    pick(instance, echo=echo("look"))

    payloads = asyncio.run(turn(instance, [("echo__post", {"text": "hi"})], PermissionMode.ASK))

    called = result(payloads)
    assert called.error
    assert called.output == 'Tool "echo__post" is not available in this turn.'


def sent(instance: Instance) -> set[str]:
    """The echo server's tools the model is sent on a turn of *instance*."""
    model = scripted([])
    asyncio.run(model_turn(instance, model, PermissionMode.ASK))
    [tools] = model.bound_tools
    return {tool.name for tool in tools if tool.name.startswith("echo__")}


def test_a_loaded_tool_is_sent_to_the_model_and_a_searchable_or_off_one_is_not(
    tmp_path: Path,
) -> None:
    instance = instance_at(tmp_path)
    library = echo("look", default_state=ToolState.SEARCHABLE).model_copy(
        update={"tool_states": {"look": ToolState.LOADED, "post": ToolState.OFF}}
    )
    pick(instance, echo=library)

    assert sent(instance) == {"echo__look"}


def test_a_tool_the_server_adds_later_takes_its_groups_default(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    library = echo(default_state=ToolState.LOADED).model_copy(
        update={"tool_states": {"look": ToolState.OFF}}
    )
    pick(instance, echo=library)

    assert sent(instance) == {"echo__arguments", "echo__environment", "echo__headers", "echo__post"}


def test_the_picks_states_beat_the_library_entrys(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    changed = LockedMcpServer(
        server=echo("look", "post"),
        default_state=ToolState.LOADED,
        tool_states={"look": ToolState.OFF, "headers": ToolState.SEARCHABLE},
    )
    write_lock(instance.path, SkillsLock(mcp_servers={"echo": changed}))

    assert sent(instance) == {"echo__arguments", "echo__environment", "echo__post"}


def test_a_server_switched_off_offers_no_tool_even_one_with_its_own_state(
    tmp_path: Path,
) -> None:
    instance = instance_at(tmp_path)
    switched_off = LockedMcpServer(
        server=echo("look"), on=False, tool_states={"post": ToolState.LOADED}
    )
    write_lock(instance.path, SkillsLock(mcp_servers={"echo": switched_off}))

    assert sent(instance) == set()


def test_a_turn_resumed_from_an_approval_keeps_the_states_it_started_with(
    tmp_path: Path,
) -> None:
    instance = instance_at(tmp_path)
    pick(instance, echo=echo("look"))
    runner = LangGraphRunner(
        instance, model_factory=lambda _: scripted([("echo__look", {"text": "hi"})])
    )
    payloads: list[Payload] = []

    async def scenario() -> None:
        preparation = runner.prepare_for_turn()
        request = PreparedTurnRequest(
            thread_id=uuid4(),
            turn_id=uuid4(),
            message="Look.",
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
        pick(instance, echo=echo())
        await runner.resume(request, ApprovalAnswer(ApprovalDecision.APPROVE), context)

    asyncio.run(scenario())

    called = result(payloads)
    assert (called.error, called.output) == (False, "looked: hi")


@pytest.mark.parametrize("state", [ToolState.LOADED, ToolState.SEARCHABLE])
def test_a_routines_code_step_runs_a_loaded_or_searchable_tool(
    tmp_path: Path, state: ToolState
) -> None:
    instance = instance_at(tmp_path)
    pick(instance, echo=echo(default_state=state))
    routine_file(
        instance, 'description: Look\nmode: full-access\nrun: echo__look\narguments: {"text": "hi"}'
    )
    model = RoutineModel()

    events = asyncio.run(fire(instance, model, seconds=60))

    assert isinstance(events[-1].payload, TurnCompleted)
    assert "looked: hi" in str(model.messages[0][-1].content)


def test_a_routines_code_step_naming_an_off_tool_fails_as_a_missing_tool(
    tmp_path: Path,
) -> None:
    instance = instance_at(tmp_path)
    pick(instance, echo=echo())
    routine_file(
        instance, 'description: Look\nmode: full-access\nrun: echo__look\narguments: {"text": "hi"}'
    )

    events = asyncio.run(fire(instance, RoutineModel(), seconds=60))

    failed = events[-1].payload
    assert isinstance(failed, TurnFailed)
    assert failed.code is ErrorCode.NOT_FOUND
    assert 'Code step tool "echo__look"' in failed.message


@pytest.mark.parametrize("tool", ["echo__look", "echo__post"])
def test_every_call_asks_in_ask_mode_until_the_pick_trusts_the_servers_hints(
    tmp_path: Path, tool: str
) -> None:
    instance = instance_at(tmp_path)
    pick(instance, echo=echo("look", "post"))

    payloads = asyncio.run(turn(instance, [(tool, {"text": "hi"})], PermissionMode.ASK))

    assert asked(payloads) == [tool]


def test_a_trusted_server_runs_its_read_only_tool_unasked_and_asks_for_the_others(
    tmp_path: Path,
) -> None:
    instance = instance_at(tmp_path)
    pick(instance, trust_hints=True, echo=echo("look", "post"))

    looking = asyncio.run(turn(instance, [("echo__look", {"text": "hi"})], PermissionMode.ASK))
    posting = asyncio.run(turn(instance, [("echo__post", {"text": "hi"})], PermissionMode.ASK))

    assert (asked(looking), result(looking).output) == ([], "looked: hi")
    assert asked(posting) == ["echo__post"]


def test_a_local_server_sees_its_declared_variables_and_no_other_env_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = instance_at(tmp_path)
    (tmp_path / ".env").write_text("ECHO_KEY=declared\nOTHER_SECRET=kept\n", encoding="utf-8")
    # The container's environment holds every key of the instance's .env.
    monkeypatch.setenv("ECHO_KEY", "declared")
    monkeypatch.setenv("OTHER_SECRET", "kept")
    pick(instance, echo=echo("environment", env={"ECHO_KEY": "${ECHO_KEY}"}))

    payloads = asyncio.run(turn(instance, [("echo__environment", {})], PermissionMode.FULL_ACCESS))

    seen = json.loads(result(payloads).output)
    assert seen["ECHO_KEY"] == "declared"
    assert "OTHER_SECRET" not in seen


def test_an_env_value_takes_the_instances_secret_its_default_or_stays_as_written(
    tmp_path: Path,
) -> None:
    instance = instance_at(tmp_path)
    (tmp_path / ".env").write_text("ECHO_KEY=secret\n", encoding="utf-8")
    env = {
        "ECHO_KEY": "key-${ECHO_KEY}",
        "ECHO_SET": "${ECHO_KEY:-unused}",
        "ECHO_UNSET": "${ECHO_MODE:-quiet}",
        "ECHO_LITERAL": "$ECHO_KEY ${ECHO_KEY",
    }
    pick(instance, echo=echo("environment", env=env))

    payloads = asyncio.run(turn(instance, [("echo__environment", {})], PermissionMode.FULL_ACCESS))

    seen = json.loads(result(payloads).output)
    assert {name: seen[name] for name in env} == {
        "ECHO_KEY": "key-secret",
        "ECHO_SET": "secret",
        "ECHO_UNSET": "quiet",
        "ECHO_LITERAL": "$ECHO_KEY ${ECHO_KEY",
    }


def test_an_argument_takes_the_instances_secret(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    (tmp_path / ".env").write_text("ECHO_KEY=secret\n", encoding="utf-8")
    pick(instance, echo=echo("arguments", args=("--key", "${ECHO_KEY}", "${ECHO_MODE:-quiet}")))

    payloads = asyncio.run(turn(instance, [("echo__arguments", {})], PermissionMode.FULL_ACCESS))

    assert json.loads(result(payloads).output) == ["--key", "secret", "quiet"]


def test_a_remote_server_is_reached_at_its_filled_url_with_filled_and_fixed_headers(
    tmp_path: Path, echo_port: int
) -> None:
    instance = instance_at(tmp_path)
    (tmp_path / ".env").write_text(f"ECHO_PORT={echo_port}\nECHO_TOKEN=secret\n", encoding="utf-8")
    remote = McpServer.model_validate(
        {
            "entry": {
                "url": "http://127.0.0.1:${ECHO_PORT}/mcp",
                "headers": {
                    "Authorization": "Bearer ${ECHO_TOKEN}",
                    "X-Cockpit-Agent": "kinby",
                },
            },
            "tool_states": {"headers": "loaded"},
        }
    )
    pick(instance, echo=remote)

    payloads = asyncio.run(turn(instance, [("echo__headers", {})], PermissionMode.FULL_ACCESS))

    seen = json.loads(result(payloads).output)
    assert (seen["authorization"], seen["x-cockpit-agent"]) == ("Bearer secret", "kinby")


def test_a_pick_the_hub_converted_from_the_old_shape_runs_as_before(tmp_path: Path) -> None:
    async def scenario() -> Instance:
        hub = hub_at(tmp_path / "hub")
        created = await started_instance(hub_client(hub), hub)
        hub.close()
        return inspect_instance(hub.instances_directory / str(created.instance_id))

    instance = asyncio.run(scenario())
    old = {"transport": {"type": "uvx", "package": "echo-server"}, "include": ["look"]}
    (instance.path / "skills-lock.json").write_text(
        json.dumps({"version": 1, "skills": {}, "mcpServers": {"echo": {"server": old}}}),
        encoding="utf-8",
    )
    hub_at(tmp_path / "hub").close()

    payloads = asyncio.run(
        turn(instance, [("echo__look", {"text": "hi"})], PermissionMode.FULL_ACCESS)
    )

    called = result(payloads)
    assert (called.error, called.output) == (False, "looked: hi")


@pytest.mark.parametrize("command", ["uvx", "npx"])
def test_a_server_that_fails_to_start_leaves_the_turn_running_without_its_tools(
    tmp_path: Path, command: str
) -> None:
    instance = instance_at(tmp_path)
    broken = echo("look", package="missing-server", command=command)
    pick(instance, echo=echo("look"), broken=broken)

    payloads = asyncio.run(
        turn(
            instance,
            [("broken__look", {"text": "hi"}), ("echo__look", {"text": "hi"})],
            PermissionMode.FULL_ACCESS,
        )
    )

    outputs = [payload.output for payload in payloads if isinstance(payload, ToolResult)]
    assert outputs == ['Tool "broken__look" is not available in this turn.', "looked: hi"]


@pytest.mark.parametrize("command", ["uvx", "npx"])
def test_a_server_that_failed_at_the_last_turn_is_an_instance_notice_ending_with_what_it_wrote(
    tmp_path: Path, command: str
) -> None:
    async def scenario() -> list[InstanceNotice]:
        hub = hub_at(tmp_path / "hub")
        client = hub_client(hub)
        created = await started_instance(client, hub)
        instance = inspect_instance(hub.instances_directory / str(created.instance_id))
        pick(instance, broken=echo("look", package="missing-server", command=command))
        await turn(instance, [], PermissionMode.ASK)
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert isinstance(listed, InstanceListResult)
        [summary] = listed.instances
        return list(summary.notices)

    [notice] = asyncio.run(scenario())

    assert isinstance(notice, McpServerFailed)
    assert notice.server == "broken"
    assert notice.message.startswith('MCP server "broken" did not start or list its tools')
    assert notice.message.endswith("missing-server was not found in the package registry")


def failure(instance: Instance, server: McpServer) -> str:
    pick(instance, broken=server)
    asyncio.run(turn(instance, [], PermissionMode.ASK))
    return failed_mcp_servers(instance.manifest.state_dir)["broken"]


def test_a_turn_records_each_tool_a_server_listed_with_its_description_and_size(
    tmp_path: Path,
) -> None:
    instance = instance_at(tmp_path)
    pick(instance, echo=echo("look"))

    asyncio.run(turn(instance, [], PermissionMode.ASK))

    listed = mcp_tools_listed(instance.manifest.state_dir)["echo"]
    assert [tool.name for tool in listed] == ["arguments", "environment", "headers", "look", "post"]
    assert listed[3].description == "Repeat a line of text."
    assert all(tool.tokens > 0 for tool in listed)


def test_a_server_that_fails_keeps_what_it_listed_at_the_last_turn_connected_to_it(
    tmp_path: Path,
) -> None:
    instance = instance_at(tmp_path)
    pick(instance, echo=echo("look"))
    asyncio.run(turn(instance, [], PermissionMode.ASK))
    first = mcp_tools_listed(instance.manifest.state_dir)
    broken = echo("look", package="missing-server")
    pick(instance, echo=broken, other=broken)

    asyncio.run(turn(instance, [], PermissionMode.ASK))

    assert mcp_tools_listed(instance.manifest.state_dir) == first


def test_a_missing_package_is_told_by_its_cause_and_what_its_launcher_said(tmp_path: Path) -> None:
    message = failure(instance_at(tmp_path), echo("look", package="missing-server"))

    assert "ExceptionGroup" not in message
    assert "MCPError: Connection closed" in message
    assert "missing-server was not found in the package registry" in message


def test_only_the_tail_of_a_noisy_servers_stderr_is_told(tmp_path: Path) -> None:
    message = failure(instance_at(tmp_path), echo("look", package="noisy-server"))

    assert message.endswith("\n99999\n100000")
    assert len(message) < STDERR_TAIL + 200


def test_a_server_that_never_answers_is_told_to_have_timed_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp_connection, "CONNECT_SECONDS", 0.1)

    message = failure(instance_at(tmp_path), echo("look", package="silent-server"))

    assert message.endswith("without them: it timed out after 0.1 seconds")


def test_a_server_whose_variable_the_instance_lacks_is_left_out_with_a_notice_naming_it(
    tmp_path: Path,
) -> None:
    instance = instance_at(tmp_path)
    needy = echo("look", env={"ECHO_KEY": "${ECHO_KEY}", "ECHO_MODE": "${ECHO_MODE:-quiet}"})
    pick(instance, echo=echo("look"), needy=needy)

    payloads = asyncio.run(
        turn(
            instance,
            [("needy__look", {"text": "hi"}), ("echo__look", {"text": "hi"})],
            PermissionMode.FULL_ACCESS,
        )
    )

    outputs = [payload.output for payload in payloads if isinstance(payload, ToolResult)]
    assert outputs == ['Tool "needy__look" is not available in this turn.', "looked: hi"]
    assert failed_mcp_servers(instance.manifest.state_dir) == {
        "needy": (
            'MCP server "needy" needs ECHO_KEY, which the instance\'s secrets lack, '
            "so the turn ran without it."
        )
    }


def test_a_server_connects_once_the_instance_holds_the_variable_it_lacked(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    pick(instance, needy=echo("look", env={"ECHO_KEY": "${ECHO_KEY}"}))
    asyncio.run(turn(instance, [], PermissionMode.ASK))
    (tmp_path / ".env").write_text("ECHO_KEY=secret\n", encoding="utf-8")

    payloads = asyncio.run(
        turn(instance, [("needy__look", {"text": "hi"})], PermissionMode.FULL_ACCESS)
    )

    assert result(payloads).output == "looked: hi"
    assert failed_mcp_servers(instance.manifest.state_dir) == {}


def test_a_server_whose_sign_in_is_pending_is_left_out_with_a_notice_that_it_needs_one(
    tmp_path: Path, oauth_server: OAuthMcpServer
) -> None:
    instance = instance_at(tmp_path)
    remote = McpServer.model_validate(
        {"entry": {"url": oauth_server.url}, "tool_states": {"look": "loaded"}}
    )
    write_lock(
        instance.path,
        SkillsLock(
            mcp_servers={
                "echo": LockedMcpServer(server=echo("look")),
                "cockpit": LockedMcpServer(server=remote, needs_sign_in=True),
            }
        ),
    )

    payloads = asyncio.run(
        turn(
            instance,
            [("cockpit__look", {"text": "hi"}), ("echo__look", {"text": "hi"})],
            PermissionMode.FULL_ACCESS,
        )
    )

    outputs = [payload.output for payload in payloads if isinstance(payload, ToolResult)]
    assert outputs == ['Tool "cockpit__look" is not available in this turn.', "looked: hi"]
    assert failed_mcp_servers(instance.manifest.state_dir) == {
        "cockpit": 'MCP server "cockpit" needs a sign-in, so the turn ran without it.'
    }


@asynccontextmanager
async def signed_in(
    tmp_path: Path,
    oauth_server: OAuthMcpServer,
    monkeypatch: pytest.MonkeyPatch,
    clock: Callable[[], datetime] | None = None,
) -> AsyncIterator[tuple[Hub, Serve, Instance]]:
    """A served hub, and its instance that picked the server behind a sign-in as cockpit, with
    a fixed header, and signed in to it, holding its hub route."""
    hub = hub_at(tmp_path / "hub", control=FakeControl(), **({"clock": clock} if clock else {}))
    instance_id = await _picked(
        hub, {"url": oauth_server.url, "headers": {"X-Cockpit-Agent": "kinby"}}
    )
    async with served(hub) as address:
        await _signed_in(hub, instance_id, address)
        monkeypatch.setenv(HUB_URL_VARIABLE, url(address, f"/instances/{instance_id}/hub"))
        monkeypatch.setenv(HUB_SCOPES_VARIABLE, ",".join(hub.instance_scopes(instance_id)))
        monkeypatch.setenv(CONTROL_TOKEN_VARIABLE, control_token(hub, str(instance_id)))
        yield hub, address, inspect_instance(hub.instances_directory / str(instance_id))


def instance_id(instance: Instance) -> UUID:
    return UUID(instance.path.name)


def outputs(payloads: Sequence[object]) -> list[str]:
    return [payload.output for payload in payloads if isinstance(payload, ToolResult)]


def test_a_signed_in_server_is_called_with_the_hubs_token_beside_its_fixed_headers(
    tmp_path: Path, oauth_server: OAuthMcpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> tuple[Instance, list[str]]:
        async with signed_in(tmp_path, oauth_server, monkeypatch) as (_, _, instance):
            payloads = await turn(
                instance,
                [("cockpit__look", {"text": "hi"}), ("cockpit__headers", {})],
                PermissionMode.FULL_ACCESS,
            )
        return instance, outputs(payloads)

    instance, (looked, headers) = asyncio.run(scenario())

    assert looked == "looked: hi"
    assert json.loads(headers)["x-cockpit-agent"] == "kinby"
    assert failed_mcp_servers(instance.manifest.state_dir) == {}
    files = b"".join(path.read_bytes() for path in instance.path.rglob("*") if path.is_file())
    assert oauth_server.tokens()
    assert not [token for token in oauth_server.tokens() if token.encode() in files]


def test_a_token_the_server_stops_accepting_mid_turn_is_asked_for_once_more_and_the_call_retried(
    tmp_path: Path, oauth_server: OAuthMcpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> list[str]:
        async with signed_in(tmp_path, oauth_server, monkeypatch) as (_, _, instance):
            payloads = await turn(
                instance,
                [("cockpit__expire", {}), ("cockpit__look", {"text": "hi"})],
                PermissionMode.FULL_ACCESS,
            )
        return outputs(payloads)

    assert asyncio.run(scenario()) == ["expired", "looked: hi"]


def test_a_turn_after_the_token_ran_out_connects_with_the_one_the_hub_renewed(
    tmp_path: Path, oauth_server: OAuthMcpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _Clock()

    async def scenario() -> list[str]:
        async with signed_in(tmp_path, oauth_server, monkeypatch, clock) as (_, _, instance):
            clock.now += timedelta(hours=2)
            oauth_server.expire()
            payloads = await turn(
                instance, [("cockpit__look", {"text": "hi"})], PermissionMode.FULL_ACCESS
            )
        return outputs(payloads)

    assert asyncio.run(scenario()) == ["looked: hi"]


def test_a_refused_sign_in_leaves_the_server_out_with_a_notice_until_jorge_signs_in_again(
    tmp_path: Path, oauth_server: OAuthMcpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    needs = 'MCP server "cockpit" needs a sign-in, so the turn ran without it.'

    async def notices(hub: Hub) -> list[tuple[str, str]]:
        listed = await hub_client(hub).call(INSTANCE_LIST, InstanceListCommand())
        assert isinstance(listed, InstanceListResult)
        [summary] = listed.instances
        return [
            (notice.server, notice.message)
            for notice in summary.notices
            if isinstance(notice, McpServerFailed)
        ]

    async def scenario() -> None:
        async with signed_in(tmp_path, oauth_server, monkeypatch) as (hub, address, instance):
            oauth_server.revoke()
            refused = await turn(
                instance, [("cockpit__look", {"text": "hi"})], PermissionMode.FULL_ACCESS
            )

            assert outputs(refused) == ['Tool "cockpit__look" is not available in this turn.']
            assert await notices(hub) == [("cockpit", needs)]
            assert len(oauth_server.clients) == 1

            await _signed_in(hub, instance_id(instance), address)
            again = await turn(
                instance, [("cockpit__look", {"text": "hi"})], PermissionMode.FULL_ACCESS
            )

            assert outputs(again) == ["looked: hi"]
            assert await notices(hub) == []

    asyncio.run(scenario())
