"""Start an MCP server a turn picked, and adapt the tools it lists to kinby tools (ADR 0111)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from collections.abc import Mapping
from contextlib import AsyncExitStack
from functools import partial
from pathlib import Path
from typing import TextIO

from langchain_core.tools import StructuredTool
from mcp import Client, StdioServerParameters
from mcp.client import Transport
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import TextContent
from mcp.types import Tool as McpTool

from kinby.contracts import McpServer, McpUrlTransport
from kinby.instance.skills_lock import LockedMcpServer
from kinby.plugins.defaults.shell import OUTPUT_CAP
from kinby.plugins.errors import exception_message
from kinby.plugins.tools import Tool

#: How long one server may take to start and list its tools at turn start.
CONNECT_SECONDS = 60
#: At most how many bytes of a failed server's stderr end its notice.
STDERR_TAIL = 2000


async def connected_tools(
    name: str, pick: LockedMcpServer, env: Mapping[str, str], source: Path, stack: AsyncExitStack
) -> list[Tool]:
    """The included tools of the server *pick* names, connected until *stack* closes. A server
    that does not start or list its tools raises NotConnected."""
    client, listed = await _connect(pick.server, env, stack)
    return [
        _adapted(name, pick, each, client, source)
        for each in listed
        if each.name in pick.server.include
    ]


class NotConnected(Exception):
    """Why a server did not start or list its tools."""


class _Stderr:
    """Where a uvx server writes its stderr: passed on to the instance's own, with its tail kept
    to tell why the server failed. A URL server writes none."""

    def __init__(self) -> None:
        read, write = os.pipe()
        self.file = open(write, "w")  # noqa: SIM115 - open until the server is closed
        self._forward = sys.stderr.fileno()
        self._tail = b""
        self._reader = threading.Thread(target=self._pass_on, args=(read,), daemon=True)
        self._reader.start()

    def tail(self) -> str:
        return self._tail.decode(errors="replace").strip()

    def close(self) -> None:
        self.file.close()
        # Once the server's processes exit, the pipe closes and the reader holds all they wrote.
        self._reader.join(timeout=1)

    def _pass_on(self, read: int) -> None:
        with open(read, "rb", buffering=0) as pipe:
            while chunk := pipe.read(65536):
                os.write(self._forward, chunk)
                self._tail = (self._tail + chunk)[-STDERR_TAIL:]


async def _connect(
    server: McpServer, env: Mapping[str, str], stack: AsyncExitStack
) -> tuple[Client, list[McpTool]]:
    """A client of *server* that *stack* closes, and every tool it lists. A server that fails
    is closed at once, and raises why, ending with what it last wrote to stderr."""
    stderr = _Stderr()
    try:
        async with asyncio.timeout(CONNECT_SECONDS), AsyncExitStack() as connecting:
            connecting.callback(stderr.close)
            client = await connecting.enter_async_context(
                Client(_transport(server, env, stderr.file))
            )
            listed: list[McpTool] = []
            cursor: str | None = None
            while True:
                page = await client.list_tools(cursor=cursor)
                listed.extend(page.tools)
                cursor = page.next_cursor
                if cursor is None:
                    break
            stack.push_async_exit(connecting.pop_all())
    except TimeoutError:
        reason = f"it timed out after {CONNECT_SECONDS} seconds"
    except Exception as exc:
        reason = exception_message(exc)
    else:
        return client, listed
    tail = stderr.tail()
    raise NotConnected(f"{reason}. Its stderr ended with: {tail}" if tail else reason)


def _transport(server: McpServer, env: Mapping[str, str], stderr: TextIO) -> Transport:
    """How to reach *server*: its URL with the headers its variables fill, or uvx over stdio with
    only the variables it declares and its stderr to *stderr*."""
    transport = server.transport
    if isinstance(transport, McpUrlTransport):
        headers = {header: env.get(variable, "") for header, variable in transport.headers.items()}
        return streamable_http_client(
            transport.url, http_client=create_mcp_http_client(headers=headers)
        )
    parameters = StdioServerParameters(
        command="uvx",
        args=[transport.package, *transport.args],
        env={name: env[name] for name in server.env if name in env},
    )
    return stdio_client(parameters, errlog=stderr)


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
