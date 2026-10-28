"""Connect a turn to the MCP servers its instance picked, and offer their tools (ADR 0111)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial
from pathlib import Path

from dotenv import dotenv_values
from langchain_core.tools import StructuredTool
from mcp import Client, StdioServerParameters
from mcp.client import Transport
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import TextContent
from mcp.types import Tool as McpTool
from pydantic import TypeAdapter

from kinby.contracts import McpServer, McpUrlTransport
from kinby.instance import Instance
from kinby.instance.layout import ENV_NAME, MCP_FAILURES_NAME, SKILLS_LOCK_NAME
from kinby.instance.skills_lock import LockedMcpServer, read_lock
from kinby.plugins.defaults.shell import OUTPUT_CAP
from kinby.plugins.errors import exception_message
from kinby.plugins.tools import Tool

#: How long one server may take to start and list its tools at turn start.
CONNECT_SECONDS = 60

_FAILURES = TypeAdapter(dict[str, str])


@asynccontextmanager
async def mcp_tools(instance: Instance) -> AsyncIterator[tuple[Tool, ...]]:
    """The included tools of each MCP server the instance picked, connected while the body runs.

    A server that does not start or list its tools is left out, and recorded for the hub to
    report as an instance notice until a turn connects to it again.
    """
    picks = read_lock(instance.path).mcp_servers
    env = {
        name: value
        for name, value in dotenv_values(instance.path / ENV_NAME, interpolate=False).items()
        if value is not None
    }
    source = (instance.path / SKILLS_LOCK_NAME).resolve()
    tools: list[Tool] = []
    failures: dict[str, str] = {}
    async with AsyncExitStack() as stack:
        for name, pick in sorted(picks.items()):
            try:
                async with asyncio.timeout(CONNECT_SECONDS):
                    client, listed = await _connect(pick.server, env, stack)
            except Exception as exc:
                failures[name] = (
                    f'MCP server "{name}" did not start or list its tools, so the turn ran '
                    f"without them: {exception_message(exc)}"
                )
                continue
            tools.extend(
                _adapted(name, pick, each, client, source)
                for each in listed
                if each.name in pick.server.include
            )
        _record_failures(instance.manifest.state_dir, failures)
        yield tuple(tools)


def failed_mcp_servers(state_dir: Path) -> dict[str, str]:
    """Why each MCP server that failed at the instance's last turn failed, by its name. A record
    that does not parse holds none, so it never stops the hub listing the instance."""
    try:
        return _FAILURES.validate_json((state_dir / MCP_FAILURES_NAME).read_bytes())
    except FileNotFoundError, ValueError:
        return {}


def _record_failures(state_dir: Path, failures: dict[str, str]) -> None:
    path = state_dir / MCP_FAILURES_NAME
    if not failures:
        path.unlink(missing_ok=True)
        return
    staging = path.with_name(f".{path.name}.staging")
    staging.write_bytes(_FAILURES.dump_json(failures))
    staging.replace(path)


async def _connect(
    server: McpServer, env: Mapping[str, str], stack: AsyncExitStack
) -> tuple[Client, list[McpTool]]:
    """A client of *server* that *stack* closes, and every tool it lists. A server that fails
    is closed at once."""
    async with AsyncExitStack() as connecting:
        client = await connecting.enter_async_context(Client(_transport(server, env)))
        listed: list[McpTool] = []
        cursor: str | None = None
        while True:
            page = await client.list_tools(cursor=cursor)
            listed.extend(page.tools)
            cursor = page.next_cursor
            if cursor is None:
                break
        stack.push_async_exit(connecting.pop_all())
    return client, listed


def _transport(server: McpServer, env: Mapping[str, str]) -> Transport | StdioServerParameters:
    """How to reach *server*: its URL with the headers its variables fill, or uvx over stdio with
    only the variables it declares."""
    transport = server.transport
    if isinstance(transport, McpUrlTransport):
        headers = {header: env.get(variable, "") for header, variable in transport.headers.items()}
        return streamable_http_client(
            transport.url, http_client=create_mcp_http_client(headers=headers)
        )
    return StdioServerParameters(
        command="uvx",
        args=[transport.package, *transport.args],
        env={name: env[name] for name in server.env if name in env},
    )


def _adapted(
    server: str, pick: LockedMcpServer, listed: McpTool, client: Client, source: Path
) -> Tool:
    """The server's tool as a kinby tool: a write, unless the user trusts the server's hints and
    the tool declares itself read-only."""
    name = f"{server}__{listed.name}"
    read_only = listed.annotations is not None and listed.annotations.read_only_hint is True
    return Tool(
        name=name,
        write=not (pick.trust_hints and read_only),
        source=source,
        runnable=StructuredTool(
            name=name,
            description=listed.description or listed.name,
            args_schema=listed.input_schema,
            coroutine=partial(_call, client, listed.name),
        ),
    )


async def _call(client: Client, tool: str, /, **arguments: object) -> str:
    result = await client.call_tool(tool, arguments)
    output = "\n".join(
        block.text if isinstance(block, TextContent) else block.model_dump_json()
        for block in result.content
    )
    if result.is_error:
        raise ValueError(output or json.dumps(result.structured_content))
    return output[:OUTPUT_CAP]
