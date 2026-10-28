"""MCP servers on an instance's turns: each picked server's included tools, named by server and
gated as writes until the user trusts the server's read-only hints (ADR 0111)."""

import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from kinby.contracts import (
    INSTANCE_LIST,
    ApprovalRequested,
    InstanceListCommand,
    InstanceListResult,
    InstanceNotice,
    McpServer,
    McpServerFailed,
    PermissionMode,
    ToolResult,
)
from kinby.instance import Instance, inspect_instance
from kinby.instance.skills_lock import LockedMcpServer, SkillsLock, write_lock
from tests.test_hub import hub_at, hub_client, started_instance
from tests.test_instance_code import turn
from tests.test_routines import instance_at

SERVER = Path(__file__).with_name("mcp_echo_server.py")


def echo(*include: str, env: tuple[str, ...] = (), package: str = "echo-server") -> McpServer:
    return McpServer.model_validate(
        {
            "transport": {"type": "uvx", "package": package},
            "env": list(env),
            "include": list(include),
        }
    )


@pytest.fixture(autouse=True)
def uvx(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for uvx: it starts the echo server for the package echo-server, and fails for
    any other."""
    folder = tmp_path_factory.mktemp("bin")
    shim = folder / "uvx"
    shim.write_text(
        f'#!/bin/sh\n[ "$1" = "echo-server" ] || exit 1\nexec "{sys.executable}" "{SERVER}"\n',
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


def test_a_picked_servers_included_tool_is_named_by_its_server_and_a_turn_calls_it(
    tmp_path: Path,
) -> None:
    instance = instance_at(tmp_path)
    pick(instance, echo=echo("look", "post"))

    payloads = asyncio.run(
        turn(instance, [("echo__look", {"text": "hi"})], PermissionMode.FULL_ACCESS)
    )

    called = result(payloads)
    assert (called.error, called.output) == (False, "looked: hi")


def test_a_tool_outside_the_include_filter_is_not_offered(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    pick(instance, echo=echo("look"))

    payloads = asyncio.run(turn(instance, [("echo__post", {"text": "hi"})], PermissionMode.ASK))

    called = result(payloads)
    assert called.error
    assert called.output == 'Tool "echo__post" is not available in this turn.'


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
    pick(instance, echo=echo("environment", env=("ECHO_KEY",)))

    payloads = asyncio.run(turn(instance, [("echo__environment", {})], PermissionMode.FULL_ACCESS))

    seen = json.loads(result(payloads).output)
    assert seen["ECHO_KEY"] == "declared"
    assert "OTHER_SECRET" not in seen


def test_a_server_that_fails_to_start_leaves_the_turn_running_without_its_tools(
    tmp_path: Path,
) -> None:
    instance = instance_at(tmp_path)
    pick(instance, echo=echo("look"), broken=echo("look", package="missing-server"))

    payloads = asyncio.run(
        turn(
            instance,
            [("broken__look", {"text": "hi"}), ("echo__look", {"text": "hi"})],
            PermissionMode.FULL_ACCESS,
        )
    )

    outputs = [payload.output for payload in payloads if isinstance(payload, ToolResult)]
    assert outputs == ['Tool "broken__look" is not available in this turn.', "looked: hi"]


def test_a_server_that_failed_at_the_last_turn_is_an_instance_notice(tmp_path: Path) -> None:
    async def scenario() -> list[InstanceNotice]:
        hub = hub_at(tmp_path / "hub")
        client = hub_client(hub)
        created = await started_instance(client, hub)
        instance = inspect_instance(hub.instances_directory / str(created.instance_id))
        pick(instance, broken=echo("look", package="missing-server"))
        await turn(instance, [], PermissionMode.ASK)
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert isinstance(listed, InstanceListResult)
        [summary] = listed.instances
        return list(summary.notices)

    [notice] = asyncio.run(scenario())

    assert isinstance(notice, McpServerFailed)
    assert notice.server == "broken"
    assert notice.message.startswith('MCP server "broken" did not start or list its tools')
