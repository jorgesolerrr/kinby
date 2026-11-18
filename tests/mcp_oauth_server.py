"""A fake remote MCP server that requires an OAuth sign-in, which the MCP sign-in tests share.

It is its own authorization server: it serves protected resource metadata, authorization server
metadata and dynamic client registration, its authorize endpoint redirects back at once with a
code, and its token endpoint issues tokens a test can expire or revoke. Its MCP endpoint offers
the echo server's ``look`` to a caller with a live access token. A test can turn registration off
and register a client itself, and make its protected resource metadata name another issuer.
"""

import secrets
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route


class _Provider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """The authorization server's clients, codes and tokens, in memory."""

    def __init__(self) -> None:
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.access: dict[str, AccessToken] = {}
        self.refresh: dict[str, RefreshToken] = {}

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        assert client_info.client_id is not None
        self.clients[client_info.client_id] = client_info

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        assert client.client_id is not None
        code = AuthorizationCode(
            code=secrets.token_urlsafe(),
            scopes=params.scopes or [],
            expires_at=time.time() + 600,
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        self.codes[code.code] = code
        return construct_redirect_uri(str(params.redirect_uri), code=code.code, state=params.state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return self.codes.get(authorization_code)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        del self.codes[authorization_code.code]
        return self._issued(authorization_code.client_id, authorization_code.scopes)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        return self.refresh.get(refresh_token)

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        del self.refresh[refresh_token.token]
        return self._issued(refresh_token.client_id, scopes or refresh_token.scopes)

    async def load_access_token(self, token: str) -> AccessToken | None:
        access = self.access.get(token)
        if access is None or (access.expires_at is not None and access.expires_at < time.time()):
            return None
        return access

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self.access.pop(token.token, None)
        self.refresh.pop(token.token, None)

    def _issued(self, client_id: str, scopes: list[str]) -> OAuthToken:
        access = AccessToken(
            token=secrets.token_urlsafe(),
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time()) + 3600,
        )
        refresh = RefreshToken(token=secrets.token_urlsafe(), client_id=client_id, scopes=scopes)
        self.access[access.token] = access
        self.refresh[refresh.token] = refresh
        return OAuthToken(
            access_token=access.token,
            expires_in=3600,
            scope=" ".join(scopes) or None,
            refresh_token=refresh.token,
        )


class OAuthMcpServer:
    """The running server, at ``url``, its clients, and what a test does to the tokens it
    issued."""

    def __init__(self, url: str, provider: _Provider, issuer: str) -> None:
        self.url = url
        self.issuer = issuer
        self._provider = provider
        #: The authorization server its protected resource metadata names.
        self.named_issuer = issuer

    @property
    def clients(self) -> list[OAuthClientInformationFull]:
        return list(self._provider.clients.values())

    def add_client(self, client: OAuthClientInformationFull) -> None:
        """Know *client* without its registering, as a server whose clients are set up by hand."""
        assert client.client_id is not None
        self._provider.clients[client.client_id] = client

    def tokens(self) -> list[str]:
        """Every access and refresh token still live."""
        return [*self._provider.access, *self._provider.refresh]

    def expire(self) -> None:
        """Expire every access token issued so far. Their refresh tokens still work."""
        for token, access in self._provider.access.items():
            self._provider.access[token] = access.model_copy(update={"expires_at": 0})

    def revoke(self) -> None:
        """Revoke every access and refresh token issued so far, as a user removing the client."""
        self._provider.access.clear()
        self._provider.refresh.clear()


@contextmanager
def oauth_mcp_server(*, registration: bool = True) -> Iterator[OAuthMcpServer]:
    listening = socket.socket()
    listening.bind(("127.0.0.1", 0))
    # Listening before the server runs queues the first requests rather than refusing them.
    listening.listen()
    base = f"http://127.0.0.1:{listening.getsockname()[1]}"
    provider = _Provider()
    server = MCPServer(
        "oauth-echo",
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=base,
            resource_server_url=f"{base}/mcp",
            client_registration_options=ClientRegistrationOptions(enabled=registration),
            revocation_options=RevocationOptions(enabled=True),
            validate_token_resource=False,
        ),
    )

    @server.tool()
    def look(text: str) -> str:
        """Repeat a line of text."""
        return f"looked: {text}"

    served = OAuthMcpServer(f"{base}/mcp", provider, base)

    async def resource_metadata(request: Request) -> Response:
        return JSONResponse(
            {"resource": served.url, "authorization_servers": [served.named_issuer]}
        )

    application = server.streamable_http_app()
    # Ahead of the SDK's own route, so a test can change the issuer it names.
    application.router.routes.insert(
        0, Route("/.well-known/oauth-protected-resource/mcp", resource_metadata)
    )
    running = uvicorn.Server(uvicorn.Config(application, log_level="warning"))
    thread = threading.Thread(target=running.run, kwargs={"sockets": [listening]})
    thread.start()
    try:
        yield served
    finally:
        running.should_exit = True
        thread.join()
        listening.close()
