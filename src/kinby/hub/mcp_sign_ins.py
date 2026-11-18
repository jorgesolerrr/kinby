"""Whether a picked remote MCP server asks for an MCP sign-in, the sign-in the user runs from the
web app, and the sign-ins the hub keeps (ADR 0128)."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import aiohttp
from cryptography.fernet import Fernet

from kinby.contracts import (
    LoginState,
    McpHttpEntry,
    McpServerEntry,
    McpServerName,
    McpSignInSetup,
)
from kinby.core.errors import McpSignInFailed
from kinby.hub.secrets import replace_protected
from kinby.instance.skills_lock import LockedMcpServer
from kinby.plugins.mcp import filled

if TYPE_CHECKING:
    from kinby.hub.mcp_sign_in_flow import Failed, SignedIn, SignInFlow

#: How long the hub waits on a server it asks whether it needs a sign-in.
ASK_SECONDS = 10
#: The hub route the server's page sends the browser back to.
CALLBACK_PATH = "/mcp-sign-ins/callback"
#: How long a started sign-in waits for the server's page to send the browser back.
SIGN_IN_TIME = timedelta(minutes=10)

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "kinby", "version": "1"},
    },
}
ACCEPT = {"Accept": "application/json, text/event-stream"}
_RESOURCE_METADATA = re.compile(r'resource_metadata="?([^",\s]+)')


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
    headers = {name: filled(value, secrets) for name, value in entry.headers.items()}
    metadata = await _resource_metadata(filled(entry.url, secrets), {**headers, **ACCEPT})
    return metadata is not None


def sign_in_state(pick: LockedMcpServer) -> LoginState | None:
    """How the pick's MCP sign-in stands as its lock records it, before the hub's store says
    more: pending, or None when its server asks for none."""
    return LoginState.PENDING if pick.needs_sign_in else None


async def _resource_metadata(url: str, headers: Mapping[str, str] = ACCEPT) -> str | None:
    """Where the server at *url* keeps its protected resource metadata, when asked with *headers*
    and no sign-in's token it answers 401 and says so. None for a server that cannot be reached."""
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=ASK_SECONDS)) as session,
            session.post(url, json=INITIALIZE, headers=headers) as answer,
        ):
            found = _RESOURCE_METADATA.search(answer.headers.get("WWW-Authenticate", ""))
            return found[1] if answer.status == 401 and found else None
    except aiohttp.ClientError, TimeoutError, ValueError:
        return None


@dataclass(frozen=True)
class _Running:
    instance_id: UUID
    server: McpServerName
    deadline: datetime
    flow: SignInFlow
    task: asyncio.Task[None]


class McpSignIns:
    """Every instance's MCP sign-ins: those the hub keeps, in one file per instance encrypted
    with *key*, and those started and not yet sent back, by the state the hub issued.

    The MCP SDK loads only in a hub that runs a sign-in or keeps one.
    """

    def __init__(
        self, directory: Path, key: Callable[[], Fernet], clock: Callable[[], datetime]
    ) -> None:
        self._directory = directory
        self._key = key
        self._clock = clock
        self._running: dict[str, _Running] = {}
        #: The last sign-in started to each server, which may still run after the browser came
        #: back, until its token exchange ends.
        self._last: dict[tuple[UUID, McpServerName], asyncio.Task[None]] = {}

    def setup(self, instance_id: UUID, server: McpServerName) -> McpSignInSetup:
        kept = self._read(instance_id).get(server)
        if kept is None:
            return McpSignInSetup(server=server, state=LoginState.PENDING)
        from kinby.hub.mcp_sign_in_flow import Failed

        if isinstance(kept, Failed):
            return McpSignInSetup(server=server, state=LoginState.FAILED, detail=kept.reason)
        return McpSignInSetup(server=server, state=LoginState.SIGNED_IN)

    async def start(
        self,
        instance_id: UUID,
        server: McpServerName,
        entry: McpHttpEntry,
        secrets: Mapping[str, str],
        hub_url: str,
    ) -> str:
        """Start the sign-in to *server*, which *entry* defines, and return the server's page
        where the user approves it. That page sends the browser back under *hub_url*. A sign-in
        to the same server started before completes nothing afterwards."""
        from kinby.hub.mcp_sign_in_flow import Failed, SignInFlow

        self._end(instance_id, {server})
        flow = SignInFlow(entry, secrets, f"{hub_url}{CALLBACK_PATH}")
        task = asyncio.create_task(self._run(instance_id, server, flow))
        self._last[instance_id, server] = task
        await asyncio.wait((flow.authorization_url, task), return_when=asyncio.FIRST_COMPLETED)
        if not flow.authorization_url.done():
            failed = self._read(instance_id).get(server)
            assert isinstance(failed, Failed)
            raise McpSignInFailed(failed.reason)
        authorization_url = flow.authorization_url.result()
        [state] = parse_qs(urlsplit(authorization_url).query)["state"]
        self._running[state] = _Running(
            instance_id, server, self._clock() + SIGN_IN_TIME, flow, task
        )
        return authorization_url

    async def finish(self, query: Mapping[str, str]) -> str | None:
        """Complete the sign-in the server's page sent the browser back to, with the *query* it
        sent, and return the instance's page. None for a state the hub did not issue, or one
        already used or expired."""
        state = query.get("state", "")
        running = self._running.pop(state, None)
        if running is None:
            return None
        if self._clock() > running.deadline:
            running.task.cancel()
            return None
        running.flow.called_back(state, query)
        await asyncio.wait((running.task,))
        return f"/instances/{running.instance_id}"

    def sign_out(self, instance_id: UUID, server: McpServerName) -> None:
        """Delete the sign-in to *server*, and end one still running."""
        self._end(instance_id, {server})
        kept = self._read(instance_id)
        if kept.pop(server, None) is not None:
            self._write(instance_id, kept)

    def keep(self, instance_id: UUID, servers: Collection[McpServerName]) -> None:
        """Delete each sign-in of a server outside *servers*, as a dropped pick's, and end each
        one still running."""
        self._end(
            instance_id,
            {server for each, server in self._last if each == instance_id} - set(servers),
        )
        kept = self._read(instance_id)
        if kept.keys() - set(servers):
            self._write(instance_id, {name: kept[name] for name in kept if name in servers})

    async def recheck(
        self,
        instance_id: UUID,
        picks: Mapping[McpServerName, LockedMcpServer],
        secrets: Mapping[str, str],
    ) -> None:
        """Delete each sign-in whose pick asks for none now, or whose server moved to another
        address, filled from *secrets*, or names another issuer."""
        kept = self._read(instance_id)
        if not kept:
            return
        from kinby.hub.mcp_sign_in_flow import SignedIn

        signed_in = {name: each for name, each in kept.items() if isinstance(each, SignedIn)}
        issuers = dict(
            zip(
                signed_in,
                await asyncio.gather(*(_issuer(each.url) for each in signed_in.values())),
                strict=True,
            )
        )
        stale = {
            name
            for name, each in signed_in.items()
            if (pick := picks.get(name)) is None
            or not isinstance(pick.server.entry, McpHttpEntry)
            or not pick.needs_sign_in
            or filled(pick.server.entry.url, secrets) != each.url
            or issuers[name] not in (None, each.issuer)
        }
        self.keep(instance_id, picks.keys() - stale)

    def forget(self, instance_id: UUID) -> None:
        """Delete every sign-in of the instance, as its deletion does, and end each one still
        running."""
        self._end(instance_id, {server for each, server in self._last if each == instance_id})
        self._path(instance_id).unlink(missing_ok=True)

    def _end(self, instance_id: UUID, servers: Collection[McpServerName]) -> None:
        """Cancel the sign-ins running to *servers*, so none of them keeps a sign-in, and refuse
        their links."""
        for state, running in list(self._running.items()):
            if running.instance_id == instance_id and running.server in servers:
                del self._running[state]
        for server in servers:
            task = self._last.pop((instance_id, server), None)
            if task is not None:
                task.cancel()

    async def _run(self, instance_id: UUID, server: McpServerName, flow: SignInFlow) -> None:
        """Run the sign-in and keep how it ended. One nobody finished in time leaves the sign-in
        as it was."""
        ended = await flow.run()
        if ended is not None:
            self._write(instance_id, self._read(instance_id) | {server: ended})

    def _read(self, instance_id: UUID) -> dict[McpServerName, SignedIn | Failed]:
        path = self._path(instance_id)
        if not path.exists():
            return {}
        from kinby.hub.mcp_sign_in_flow import KEPT

        return KEPT.validate_json(self._key().decrypt(path.read_bytes()))

    def _write(self, instance_id: UUID, kept: dict[McpServerName, SignedIn | Failed]) -> None:
        if not kept:
            self.forget(instance_id)
            return
        from kinby.hub.mcp_sign_in_flow import KEPT

        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        replace_protected(
            self._path(instance_id), self._key().encrypt(KEPT.dump_json(kept)).decode()
        )

    def _path(self, instance_id: UUID) -> Path:
        return self._directory / f"{instance_id}.mcp-sign-ins"


async def _issuer(url: str) -> str | None:
    """The authorization server the MCP server at *url* names, or None when it names none the
    hub can read."""
    metadata = await _resource_metadata(url)
    if metadata is None:
        return None
    from kinby.hub.mcp_sign_in_flow import issuer

    return await issuer(metadata)
