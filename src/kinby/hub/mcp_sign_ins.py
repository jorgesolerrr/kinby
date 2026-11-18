"""Whether a picked remote MCP server asks for an MCP sign-in, and how a pick's sign-in stands
(ADR 0128)."""

from __future__ import annotations

from collections.abc import Mapping

import aiohttp

from kinby.contracts import LoginState, McpHttpEntry, McpServerEntry
from kinby.instance.skills_lock import LockedMcpServer
from kinby.plugins.mcp import filled

#: How long the hub waits on a server it asks whether it needs a sign-in.
ASK_SECONDS = 10

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "kinby", "version": "1"},
    },
}


async def asks_for_sign_in(entry: McpServerEntry, secrets: Mapping[str, str]) -> bool:
    """Whether the server *entry* defines asks for a sign-in: asked at its URL with its headers,
    each filled from *secrets*, and no sign-in's token, it answers 401 and points to its protected
    resource metadata.

    A local server and an entry that sets its own Authorization header are never asked. A server
    that cannot be reached asks for none.
    """
    if not isinstance(entry, McpHttpEntry) or any(
        name.lower() == "authorization" for name in entry.headers
    ):
        return False
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=ASK_SECONDS)) as session,
            session.post(
                filled(entry.url, secrets),
                json=_INITIALIZE,
                headers={
                    **{name: filled(value, secrets) for name, value in entry.headers.items()},
                    "Accept": "application/json, text/event-stream",
                },
            ) as answer,
        ):
            return answer.status == 401 and "resource_metadata=" in answer.headers.get(
                "WWW-Authenticate", ""
            )
    except aiohttp.ClientError, TimeoutError, ValueError:
        return False


def sign_in_state(pick: LockedMcpServer) -> LoginState | None:
    """How the pick's MCP sign-in stands, or None when its server asks for none. The hub holds
    no sign-in yet, so each is pending."""
    return LoginState.PENDING if pick.needs_sign_in else None
