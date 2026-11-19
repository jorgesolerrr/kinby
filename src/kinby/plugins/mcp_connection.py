"""Start an MCP server a turn picked, and adapt the tools it lists to kinby tools (ADR 0111)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from collections.abc import AsyncGenerator, Mapping
from contextlib import AsyncExitStack
from functools import partial
from pathlib import Path
from typing import TextIO

import httpx2
from langchain_core.tools import StructuredTool
from mcp import Client, StdioServerParameters
from mcp.client import Transport
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import TextContent
from mcp.types import Tool as McpTool

from kinby.contracts import (
    MCP_SIGN_IN_TOKEN,
    ErrorCode,
    McpHttpEntry,
    McpServerEntry,
    McpSignInTokenCommand,
    McpSignInTokenResult,
    Scope,
)
from kinby.instance.skills_lock import LockedMcpServer
from kinby.plugins.errors import exception_message
from kinby.plugins.hub import HUB_SCOPES_VARIABLE, HUB_URL_VARIABLE, HubRefused, call_hub
from kinby.plugins.mcp import filled
from kinby.plugins.tools import Tool

#: How long one server may take to start and list its tools at turn start.
CONNECT_SECONDS = 60
#: At most how many bytes of a failed server's stderr end its notice.
STDERR_TAIL = 2000


async def connected_tools(
    name: str,
    pick: LockedMcpServer,
    secrets: Mapping[str, str],
    source: Path,
    stack: AsyncExitStack,
) -> list[tuple[str, Tool]]:
    """Each tool of the server *pick* names, by the name the server gives it, its entry filled
    from *secrets*, connected until *stack* closes, with the hub's access token when it asks for a
    sign-in. A server that does not start or list its tools raises NotConnected, and one whose
    sign-in the hub does not hold, SignInNeeded."""
    auth = _HubToken(name, await _hub_token(name)) if pick.needs_sign_in else None
    try:
        client, listed = await _connect(pick.server.entry, secrets, auth, stack)
    except NotConnected:
        if auth is not None and auth.pending:
            raise SignInNeeded from None
        raise
    return [(each.name, _adapted(name, pick, each, client, source)) for each in listed]


class NotConnected(Exception):
    """Why a server did not start or list its tools."""


class SignInNeeded(Exception):
    """The hub holds no sign-in to the server that the server accepts."""


async def _hub_token(server: str, refused: str | None = None) -> str:
    """A current access token for the instance's sign-in to *server*, from its hub route, the
    server having *refused* the one before."""
    granted = os.environ.get(HUB_SCOPES_VARIABLE, "").split(",")
    if not os.environ.get(HUB_URL_VARIABLE) or Scope.MCP_TOKEN not in granted:
        raise SignInNeeded
    command = McpSignInTokenCommand(server=server, refused=refused)
    try:
        answer = await call_hub(
            MCP_SIGN_IN_TOKEN.name, command.model_dump(mode="json"), "the MCP sign-in"
        )
    except HubRefused as refusal:
        if refusal.error.code is ErrorCode.MCP_SIGN_IN_PENDING:
            raise SignInNeeded from refusal
        raise NotConnected(str(refusal)) from refusal
    except ConnectionError as exc:
        raise NotConnected(str(exc)) from exc
    return McpSignInTokenResult.model_validate(answer).access_token


class _HubToken(httpx2.Auth):
    """The hub's access token for one MCP sign-in, held in memory only. A request the server
    refuses it on is sent once more with the token the hub gives next."""

    def __init__(self, server: str, token: str) -> None:
        self._server = server
        self._token = token
        #: Whether the hub said the sign-in needs a new one once the server refused its token.
        self.pending = False

    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        sent = self._token
        request.headers["Authorization"] = f"Bearer {sent}"
        response = yield request
        if response.status_code != 401:
            return
        try:
            self._token = await _hub_token(self._server, refused=sent)
        except SignInNeeded:
            self.pending = True
            return
        except NotConnected:
            return
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request


class _Stderr:
    """Where a local server writes its stderr: passed on to the instance's own, with its tail kept
    to tell why the server failed. A remote server writes none."""

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
    entry: McpServerEntry,
    secrets: Mapping[str, str],
    auth: httpx2.Auth | None,
    stack: AsyncExitStack,
) -> tuple[Client, list[McpTool]]:
    """A client of the server *entry* defines that *stack* closes, and every tool it lists. A
    server that fails is closed at once, and raises why, ending with what it last wrote to
    stderr."""
    stderr = _Stderr()
    try:
        async with asyncio.timeout(CONNECT_SECONDS), AsyncExitStack() as connecting:
            connecting.callback(stderr.close)
            client = await connecting.enter_async_context(
                Client(_transport(entry, secrets, auth, stderr.file))
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


def _transport(
    entry: McpServerEntry, secrets: Mapping[str, str], auth: httpx2.Auth | None, stderr: TextIO
) -> Transport:
    """How to reach the server *entry* defines, each of its values filled from *secrets*: its
    URL with its headers and *auth*, or its command over stdio with only the env it declares and
    its stderr to *stderr*."""
    if isinstance(entry, McpHttpEntry):
        headers = {name: filled(value, secrets) for name, value in entry.headers.items()}
        return streamable_http_client(
            filled(entry.url, secrets),
            http_client=create_mcp_http_client(headers=headers, auth=auth),
        )
    parameters = StdioServerParameters(
        command=entry.command,
        args=[filled(arg, secrets) for arg in entry.args],
        env={name: filled(value, secrets) for name, value in entry.env.items()},
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
    return output
