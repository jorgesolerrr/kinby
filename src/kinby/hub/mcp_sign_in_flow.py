"""The MCP SDK's side of an MCP sign-in, which the hub loads only while it runs a sign-in or keeps
one (ADR 0128). The SDK's OAuth client runs discovery, registration, PKCE, the ``resource``
parameter and the token exchange. kinby gives it a token store and the two ends of the redirect.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

import aiohttp
import httpx2
from mcp.client.auth import OAuthClientProvider, OAuthRegistrationError
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)
from pydantic import AnyUrl, TypeAdapter

from kinby.contracts import McpHttpEntry, McpOAuth, McpServerName
from kinby.core.errors import McpSignInUnreachable
from kinby.hub.mcp_sign_ins import ACCEPT, ASK_SECONDS, INITIALIZE, SIGN_IN_TIME
from kinby.plugins.mcp import filled

_NO_REGISTRATION = (
    "The server offers no way to register kinby. "
    "Give its entry the oauth clientId the server knows kinby as."
)


@dataclass(frozen=True)
class SignedIn:
    #: The server's address, filled from the instance's secrets, the user signed in at.
    url: str
    #: The authorization server that registered the client and issued the tokens.
    issuer: str
    client: OAuthClientInformationFull
    tokens: OAuthToken
    #: When the access token runs out, by the hub's clock, or None when the server said not.
    expires_at: datetime | None = None
    #: Where a renewal sends the refresh token, and the resource it names, as the sign-in found
    #: them.
    metadata: OAuthMetadata | None = None
    resource: ProtectedResourceMetadata | None = None


@dataclass(frozen=True)
class Failed:
    reason: str


#: What the hub keeps of an instance's sign-ins, by server.
KEPT = TypeAdapter(dict[McpServerName, SignedIn | Failed])


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


class SignInFlow(OAuthClientProvider):
    """The SDK's sign-in to the server one entry defines, asking for the entry's scopes in place
    of those the server names."""

    def __init__(self, entry: McpHttpEntry, secrets: Mapping[str, str], redirect: str) -> None:
        self.url = filled(entry.url, secrets)
        self._flow = _Flow(client=_preset(entry.oauth, secrets, redirect))
        self._scopes = entry.oauth.scopes if entry.oauth else []
        metadata = OAuthClientMetadata(
            redirect_uris=[AnyUrl(redirect)],
            client_name="kinby",
            grant_types=["authorization_code", "refresh_token"],
            application_type="web",
        )
        super().__init__(
            self.url, metadata, self._flow, self._flow.redirect, self._flow.called_back
        )

    @property
    def authorization_url(self) -> asyncio.Future[str]:
        """The server's page, once the SDK found it."""
        return self._flow.authorization_url

    def called_back(self, state: str, query: Mapping[str, str]) -> None:
        """Hand the SDK what the server's page sent the browser back with."""
        if "error" in query:
            self._flow.answer.set_exception(
                _Refused(query.get("error_description") or query["error"])
            )
        else:
            self._flow.answer.set_result(
                AuthorizationCodeResult(
                    code=query.get("code", ""), state=state, iss=query.get("iss")
                )
            )

    async def run(self, clock: Callable[[], datetime]) -> SignedIn | Failed | None:
        """Send the server the initialize request through the sign-in, which the server answers
        once signed in, and say how the sign-in ended: None when nobody finished it in time."""
        try:
            async with httpx2.AsyncClient(auth=self, timeout=ASK_SECONDS) as client:
                await client.post(self.url, json=INITIALIZE, headers=ACCEPT)
        except TimeoutError:
            return None
        except Exception as exc:
            return Failed(self._reason(exc))
        flow = self._flow
        if flow.tokens is None or flow.client is None:
            return Failed("The server did not ask for a sign-in.")
        context = self.context
        issuer = context.auth_server_url or context.get_authorization_base_url(context.server_url)
        return SignedIn(
            self.url,
            issuer,
            flow.client,
            flow.tokens,
            _expiry(flow.tokens, clock()),
            context.oauth_metadata,
            context.protected_resource_metadata,
        )

    async def _perform_authorization(self) -> httpx2.Request:
        if self._scopes:
            self.context.client_metadata.scope = " ".join(self._scopes)
        return await super()._perform_authorization()

    def _reason(self, error: Exception) -> str:
        """Why the sign-in failed, as the user reads it."""
        metadata = self.context.oauth_metadata
        match error:
            case _Refused():
                return f"The server refused the sign-in: {error}"
            case OAuthRegistrationError() if metadata is None or not metadata.registration_endpoint:
                return _NO_REGISTRATION
            case OAuthRegistrationError():
                return f"The server refused to register kinby: {error}"
            case httpx2.HTTPError():
                return f"The hub could not reach the server: {error}"
            case _:
                return f"The sign-in failed: {error}"


class _Renewal(OAuthClientProvider):
    """The SDK's refresh of the tokens one sign-in keeps."""

    def __init__(self, signed_in: SignedIn) -> None:
        # The metadata only registers a client, which a renewal never does.
        metadata = OAuthClientMetadata(redirect_uris=signed_in.client.redirect_uris)
        super().__init__(signed_in.url, metadata, _Flow(signed_in.client, signed_in.tokens))
        context = self.context
        context.client_info, context.current_tokens = signed_in.client, signed_in.tokens
        context.oauth_metadata = signed_in.metadata
        context.protected_resource_metadata = signed_in.resource

    async def tokens(self) -> OAuthToken | None:
        """The tokens the authorization server gives for the refresh token, or None when it
        refuses it."""
        try:
            async with httpx2.AsyncClient(timeout=ASK_SECONDS) as client:
                answer = await client.send(await self._refresh_token())
            if answer.is_server_error:
                answer.raise_for_status()
        except httpx2.HTTPError as exc:
            raise McpSignInUnreachable(
                f"The hub could not reach the server to renew its sign-in: {exc}"
            ) from exc
        return self.context.current_tokens if await self._handle_refresh_response(answer) else None


async def renewed(signed_in: SignedIn, clock: Callable[[], datetime]) -> SignedIn | None:
    """*signed_in* with a fresh access token, or None when the server refuses its refresh
    token or it holds none."""
    if signed_in.tokens.refresh_token is None:
        return None
    tokens = await _Renewal(signed_in).tokens()
    if tokens is None:
        return None
    return replace(signed_in, tokens=tokens, expires_at=_expiry(tokens, clock()))


def _expiry(tokens: OAuthToken, now: datetime) -> datetime | None:
    if tokens.expires_in is None:
        return None
    return now + timedelta(seconds=tokens.expires_in)


async def issuer(resource_metadata: str) -> str | None:
    """The authorization server the protected resource metadata at *resource_metadata* names, as
    the SDK names it, or None when the hub cannot read one."""
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=ASK_SECONDS)) as session,
            session.get(resource_metadata) as answer,
        ):
            named = ProtectedResourceMetadata.model_validate_json(await answer.read())
    except aiohttp.ClientError, TimeoutError, ValueError:
        return None
    return str(named.authorization_servers[0])


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
