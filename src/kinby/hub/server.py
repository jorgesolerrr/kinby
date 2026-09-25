"""Carry the hub's contract to the browser and to network clients."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from aiohttp import web

from kinby.contracts import CONTRACT_VERSION, HUB_SCOPES, AccessToken, Scope
from kinby.core.contract_server import serve_contract
from kinby.core.dispatcher import Dispatcher
from kinby.hub.access import SESSION_COOKIE, HubAccess, SessionId
from kinby.hub.models import InstanceEndpoint, InstanceRouting, InstanceUnreachable
from kinby.hub.relay import forward_signal, relay_socket, unreachable
from kinby.instance import Serve

_UNAUTHORIZED = "authentication failed"


class HubContractServer:
    """Serve the hub's contract at ``/ws``, the browser session's routes, and the web app."""

    def __init__(
        self,
        dispatcher: Dispatcher,
        access: HubAccess,
        routing: InstanceRouting,
        web_app: Path | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._access = access
        self._routing = routing
        self._web_app = web_app
        self._runner: web.AppRunner | None = None

    async def start(self, listen: Serve) -> Serve:
        application = web.Application()
        self.add_routes(application)
        runner = web.AppRunner(application)
        await runner.setup()
        site = web.TCPSite(runner, listen.host, listen.port)
        await site.start()
        self._runner = runner
        return Serve(listen.host, site.port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    def add_routes(self, application: web.Application) -> None:
        """Register the contract first: aiohttp resolves routes in order, and the app is last."""
        application.router.add_post("/auth/login", self._login)
        application.router.add_get("/auth/session", self._session, allow_head=False)
        application.router.add_post("/auth/logout", self._logout)
        application.router.add_get("/ws", self._socket, allow_head=False)
        application.router.add_get(
            "/instances/{instance_id}/ws",
            self._instance_socket,
            allow_head=False,
        )
        application.router.add_post(
            "/instances/{instance_id}/signals/{routine}",
            self._instance_signal,
        )
        application.router.add_post("/signals/{routine}", self._aliased_signal)
        if self._web_app is not None:
            _add_web_app(application, self._web_app)

    async def _login(self, request: web.Request) -> web.Response:
        session = self._access.login(await _submitted_token(request))
        if session is None:
            raise web.HTTPUnauthorized(reason=_UNAUTHORIZED)
        response = web.json_response({"contract_version": CONTRACT_VERSION})
        response.set_cookie(
            SESSION_COOKIE,
            session,
            httponly=True,
            secure=_cookie_secure(request),
            samesite="Strict",
            path="/",
        )
        return response

    async def _session(self, request: web.Request) -> web.Response:
        """A browser cannot read why an upgrade failed, so it asks whether its session is open."""
        session = request.cookies.get(SESSION_COOKIE)
        if session is None or not self._access.session_open(SessionId(session)):
            raise web.HTTPUnauthorized(reason=_UNAUTHORIZED)
        return web.Response(status=204)

    async def _logout(self, request: web.Request) -> web.Response:
        session = request.cookies.get(SESSION_COOKIE)
        if session is not None:
            self._access.logout(SessionId(session))
        response = web.Response(status=204)
        response.del_cookie(SESSION_COOKIE, path="/")
        return response

    async def _socket(self, request: web.Request) -> web.WebSocketResponse:
        scopes = self._scopes(request)
        if scopes is None:
            raise web.HTTPUnauthorized(reason=_UNAUTHORIZED)
        return await serve_contract(request, self._dispatcher, scopes)

    async def _instance_socket(self, request: web.Request) -> web.WebSocketResponse:
        """Relay to the instance's own ``/ws``, which grants no lifecycle authority."""
        if self._scopes(request) != HUB_SCOPES:
            raise web.HTTPUnauthorized(reason=_UNAUTHORIZED)
        return await relay_socket(request, await self._reach(request))

    async def _instance_signal(self, request: web.Request) -> web.Response:
        """Webhooks carry their own signature, so the hub authenticates none of them."""
        return await forward_signal(
            request,
            await self._reach(request),
            request.match_info["routine"],
        )

    async def _aliased_signal(self, request: web.Request) -> web.Response:
        """The path a webhook was registered with before the hub existed."""
        return await forward_signal(
            request,
            _reached(await self._routing.signal_endpoint()),
            request.match_info["routine"],
        )

    async def _reach(self, request: web.Request) -> InstanceEndpoint:
        try:
            instance_id = UUID(request.match_info["instance_id"])
        except ValueError as exc:
            raise unreachable(InstanceUnreachable.MISSING) from exc
        return _reached(await self._routing.endpoint(instance_id))

    def _scopes(self, request: web.Request) -> frozenset[Scope] | None:
        """A bearer token authenticates any client; a cookie only from the hub's own page."""
        header = request.headers.get("Authorization")
        if header is not None:
            if not header.startswith("Bearer "):
                return None
            return self._access.bearer_scopes(header.removeprefix("Bearer "))
        session = request.cookies.get(SESSION_COOKIE)
        if session is None or not _same_origin(request):
            return None
        return HUB_SCOPES if self._access.session_open(SessionId(session)) else None


def _reached(endpoint: InstanceEndpoint | InstanceUnreachable) -> InstanceEndpoint:
    if isinstance(endpoint, InstanceUnreachable):
        raise unreachable(endpoint)
    return endpoint


def _add_web_app(application: web.Application, web_app: Path) -> None:
    """Serve the built app: assets by name, every other path the app's own index."""

    async def page(request: web.Request) -> web.FileResponse:
        index = web_app / "index.html"
        if not index.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(index, headers={"Cache-Control": "no-cache"})

    assets = web_app / "assets"
    if assets.is_dir():
        application.router.add_static("/assets", assets)
    application.router.add_get("/{tail:.*}", page)


async def _submitted_token(request: web.Request) -> AccessToken:
    """Read the one field the login body carries, and nothing else from it."""
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(reason="the login body is not JSON") from exc
    token = body.get("token") if isinstance(body, dict) else None
    if not isinstance(token, str):
        raise web.HTTPBadRequest(reason="the login body carries no token")
    return AccessToken(token)


def _same_origin(request: web.Request) -> bool:
    origin = request.headers.get("Origin")
    host = request.headers.get("Host")
    return origin is not None and host is not None and urlsplit(origin).netloc == host


def _cookie_secure(request: web.Request) -> bool:
    """Mark Secure on HTTPS, including a request Caddy forwarded as https."""
    if request.secure:
        return True
    forwarded = request.headers.get("X-Forwarded-Proto", "")
    return forwarded.split(",", 1)[0].strip().lower() == "https"
