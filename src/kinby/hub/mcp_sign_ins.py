"""Whether a picked remote MCP server asks for an MCP sign-in, the sign-in the user runs from the
web app, and the sign-ins the hub keeps (ADR 0128).

The MCP SDK's OAuth client runs the sign-in: discovery, registration, PKCE, the ``resource``
parameter and the token exchange. kinby writes its token store and the two ends of the redirect.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import aiohttp
import httpx2
from cryptography.fernet import Fernet
from mcp.client.auth import OAuthClientProvider, OAuthRegistrationError
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)
from pydantic import AnyUrl, TypeAdapter

from kinby.contracts import (
    LoginState,
    McpHttpEntry,
    McpOAuth,
    McpServerEntry,
    McpServerName,
    McpSignInSetup,
)
from kinby.core.errors import McpSignInFailed
from kinby.hub.secrets import replace_protected
from kinby.instance.skills_lock import LockedMcpServer
from kinby.plugins.mcp import filled

#: How long the hub waits on a server it asks whether it needs a sign-in.
ASK_SECONDS = 10
#: The hub route the server's page sends the browser back to.
CALLBACK_PATH = "/mcp-sign-ins/callback"
#: How long a started sign-in waits for the server's page to send the browser back.
SIGN_IN_TIME = timedelta(minutes=10)

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
_ACCEPT = {"Accept": "application/json, text/event-stream"}
_RESOURCE_METADATA = re.compile(r'resource_metadata="?([^",\s]+)')
_NO_REGISTRATION = (
    "The server offers no way to register kinby. "
    "Give its entry the oauth clientId the server knows kinby as."
)


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
    metadata = await _resource_metadata(filled(entry.url, secrets), {**headers, **_ACCEPT})
    return metadata is not None


def sign_in_state(pick: LockedMcpServer) -> LoginState | None:
    """How the pick's MCP sign-in stands as its lock records it, before the hub's store says
    more: pending, or None when its server asks for none."""
    return LoginState.PENDING if pick.needs_sign_in else None


async def _resource_metadata(url: str, headers: Mapping[str, str] = _ACCEPT) -> str | None:
    """Where the server at *url* keeps its protected resource metadata, when asked with *headers*
    and no sign-in's token it answers 401 and says so. None for a server that cannot be reached."""
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=ASK_SECONDS)) as session,
            session.post(url, json=_INITIALIZE, headers=headers) as answer,
        ):
            found = _RESOURCE_METADATA.search(answer.headers.get("WWW-Authenticate", ""))
            return found[1] if answer.status == 401 and found else None
    except aiohttp.ClientError, TimeoutError, ValueError:
        return None


async def _issuer(url: str) -> str | None:
    """The authorization server the MCP server at *url* names, as the SDK names it, or None when
    it names none the hub can read."""
    metadata = await _resource_metadata(url)
    if metadata is None:
        return None
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=ASK_SECONDS)) as session,
            session.get(metadata) as answer,
        ):
            named = ProtectedResourceMetadata.model_validate_json(await answer.read())
    except aiohttp.ClientError, TimeoutError, ValueError:
        return None
    return str(named.authorization_servers[0])


@dataclass(frozen=True)
class _SignedIn:
    #: The server's address, filled from the instance's secrets, the user signed in at.
    url: str
    #: The authorization server that registered the client and issued the tokens.
    issuer: str
    client: OAuthClientInformationFull
    tokens: OAuthToken


@dataclass(frozen=True)
class _Failed:
    reason: str


_KEPT = TypeAdapter(dict[McpServerName, _SignedIn | _Failed])


class _Refused(Exception):
    """The server's page sent the browser back with an error in place of a code."""


@dataclass
class _Flow:
    """One sign-in from its start until the server's page sends the browser back: the SDK's
    token storage while it runs, and the two ends of the redirect."""

    client: OAuthClientInformationFull | None
    tokens: OAuthToken | None = None
    authorization_url: asyncio.Future[str] = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )
    answer: asyncio.Future[AuthorizationCodeResult] = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client = client_info

    async def redirect(self, authorization_url: str) -> None:
        self.authorization_url.set_result(authorization_url)

    async def called_back(self) -> AuthorizationCodeResult:
        return await asyncio.wait_for(self.answer, SIGN_IN_TIME.total_seconds())


class _Provider(OAuthClientProvider):
    """The SDK's sign-in, asking for the entry's scopes in place of those the server names."""

    def __init__(
        self, url: str, metadata: OAuthClientMetadata, flow: _Flow, scopes: Sequence[str]
    ) -> None:
        super().__init__(url, metadata, flow, flow.redirect, flow.called_back)
        self.flow = flow
        self._scopes = scopes

    async def _perform_authorization(self) -> httpx2.Request:
        if self._scopes:
            self.context.client_metadata.scope = " ".join(self._scopes)
        return await super()._perform_authorization()

    def issuer(self) -> str:
        context = self.context
        return context.auth_server_url or context.get_authorization_base_url(context.server_url)


@dataclass(frozen=True)
class _Running:
    instance_id: UUID
    server: McpServerName
    deadline: datetime
    flow: _Flow
    task: asyncio.Task[None]


class McpSignIns:
    """Every instance's MCP sign-ins: those the hub keeps, in one file per instance encrypted
    with *key*, and those started and not yet sent back, by the state the hub issued."""

    def __init__(
        self, directory: Path, key: Callable[[], Fernet], clock: Callable[[], datetime]
    ) -> None:
        self._directory = directory
        self._key = key
        self._clock = clock
        self._running: dict[str, _Running] = {}

    def setup(self, instance_id: UUID, server: McpServerName) -> McpSignInSetup:
        match self._read(instance_id).get(server):
            case _SignedIn():
                return McpSignInSetup(server=server, state=LoginState.SIGNED_IN)
            case _Failed(reason=reason):
                return McpSignInSetup(server=server, state=LoginState.FAILED, detail=reason)
            case None:
                return McpSignInSetup(server=server, state=LoginState.PENDING)

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
        for state, running in list(self._running.items()):
            if (running.instance_id, running.server) == (instance_id, server):
                del self._running[state]
                running.task.cancel()
        redirect = f"{hub_url}{CALLBACK_PATH}"
        oauth = entry.oauth
        flow = _Flow(client=_preset(oauth, secrets, redirect))
        metadata = OAuthClientMetadata(
            redirect_uris=[AnyUrl(redirect)],
            client_name="kinby",
            grant_types=["authorization_code", "refresh_token"],
            application_type="web",
        )
        url = filled(entry.url, secrets)
        provider = _Provider(url, metadata, flow, oauth.scopes if oauth else ())
        task = asyncio.create_task(self._run(instance_id, server, url, provider))
        await asyncio.wait((flow.authorization_url, task), return_when=asyncio.FIRST_COMPLETED)
        if not flow.authorization_url.done():
            match self._read(instance_id).get(server):
                case _Failed(reason=reason):
                    raise McpSignInFailed(reason)
                case _:
                    raise McpSignInFailed("The server did not ask for a sign-in.")
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
        if "error" in query:
            running.flow.answer.set_exception(
                _Refused(query.get("error_description") or query["error"])
            )
        else:
            running.flow.answer.set_result(
                AuthorizationCodeResult(
                    code=query.get("code", ""), state=state, iss=query.get("iss")
                )
            )
        await asyncio.wait((running.task,))
        return f"/instances/{running.instance_id}"

    def sign_out(self, instance_id: UUID, server: McpServerName) -> None:
        kept = self._read(instance_id)
        if kept.pop(server, None) is not None:
            self._write(instance_id, kept)

    def keep(self, instance_id: UUID, servers: Collection[McpServerName]) -> None:
        """Delete each sign-in of a server outside *servers*, as a dropped pick's."""
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
        signed_in = {name: each for name, each in kept.items() if isinstance(each, _SignedIn)}
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
        """Delete every sign-in of the instance, as its deletion does."""
        self._path(instance_id).unlink(missing_ok=True)

    async def _run(
        self, instance_id: UUID, server: McpServerName, url: str, provider: _Provider
    ) -> None:
        """Send the server the initialize request through the SDK's sign-in, which the server
        answers once signed in, and keep how the sign-in ended."""
        try:
            async with httpx2.AsyncClient(auth=provider, timeout=ASK_SECONDS) as client:
                await client.post(url, json=_INITIALIZE, headers=_ACCEPT)
        except TimeoutError:
            # Nobody finished it in time, so it expired and leaves the sign-in as it was.
            return
        except Exception as exc:
            self._keep(instance_id, server, _Failed(_reason(exc, provider)))
            return
        flow = provider.flow
        if flow.tokens is not None and flow.client is not None:
            self._keep(
                instance_id, server, _SignedIn(url, provider.issuer(), flow.client, flow.tokens)
            )

    def _keep(self, instance_id: UUID, server: McpServerName, kept: _SignedIn | _Failed) -> None:
        self._write(instance_id, self._read(instance_id) | {server: kept})

    def _read(self, instance_id: UUID) -> dict[McpServerName, _SignedIn | _Failed]:
        path = self._path(instance_id)
        if not path.exists():
            return {}
        return _KEPT.validate_json(self._key().decrypt(path.read_bytes()))

    def _write(self, instance_id: UUID, kept: dict[McpServerName, _SignedIn | _Failed]) -> None:
        if not kept:
            self.forget(instance_id)
            return
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        replace_protected(
            self._path(instance_id), self._key().encrypt(_KEPT.dump_json(kept)).decode()
        )

    def _path(self, instance_id: UUID) -> Path:
        return self._directory / f"{instance_id}.mcp-sign-ins"


def _preset(
    oauth: McpOAuth | None, secrets: Mapping[str, str], redirect: str
) -> OAuthClientInformationFull | None:
    """The client the entry's *oauth* names, its secret filled from *secrets*, or None when it
    names none and the hub registers one."""
    if oauth is None or oauth.client_id is None:
        return None
    secret = filled(oauth.client_secret, secrets) if oauth.client_secret else None
    return OAuthClientInformationFull(
        client_id=oauth.client_id,
        client_secret=secret or None,
        redirect_uris=[AnyUrl(redirect)],
        token_endpoint_auth_method="client_secret_post" if secret else "none",
    )


def _reason(error: Exception, provider: _Provider) -> str:
    """Why the sign-in failed, as the user reads it."""
    match error:
        case _Refused():
            return f"The server refused the sign-in: {error}"
        case OAuthRegistrationError() if (
            provider.context.oauth_metadata is None
            or provider.context.oauth_metadata.registration_endpoint is None
        ):
            return _NO_REGISTRATION
        case OAuthRegistrationError():
            return f"The server refused to register kinby: {error}"
        case httpx2.HTTPError():
            return f"The hub could not reach the server: {error}"
        case _:
            return f"The sign-in failed: {error}"
