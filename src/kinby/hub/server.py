"""Carry the hub's contract to the browser and to network clients."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

from kinby.contracts import CONTRACT_VERSION, HUB_SCOPES, AccessToken
from kinby.core.contract_server import serve_contract
from kinby.core.dispatcher import Dispatcher
from kinby.hub.access import SESSION_COOKIE, HubAccess, SessionId
from kinby.instance import Serve

_UNAUTHORIZED = "authentication failed"


class HubContractServer:
    """Serve the hub's contract at ``/ws``, the login that opens a session, and the web app."""

    def __init__(
        self,
        dispatcher: Dispatcher,
        access: HubAccess,
        web_app: Path | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._access = access
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
        application.router.add_get("/ws", self._socket, allow_head=False)
        if self._web_app is not None:
            _add_web_app(application, self._web_app)

    async def _login(self, request: web.Request) -> web.Response:
        if not self._access.accepts(await _submitted_token(request)):
            raise web.HTTPUnauthorized(reason=_UNAUTHORIZED)
        response = web.json_response({"contract_version": CONTRACT_VERSION})
        response.set_cookie(
            SESSION_COOKIE,
            self._access.open_session(),
            httponly=True,
            secure=True,
            samesite="Strict",
            path="/",
        )
        return response

    async def _socket(self, request: web.Request) -> web.WebSocketResponse:
        if not self._authenticated(request):
            raise web.HTTPUnauthorized(reason=_UNAUTHORIZED)
        return await serve_contract(request, self._dispatcher, HUB_SCOPES)

    def _authenticated(self, request: web.Request) -> bool:
        """A bearer token authenticates any client; a cookie only from the hub's own page."""
        header = request.headers.get("Authorization")
        if header is not None:
            return header.startswith("Bearer ") and self._access.accepts(
                AccessToken(header.removeprefix("Bearer "))
            )
        session = request.cookies.get(SESSION_COOKIE)
        if session is None:
            return False
        return _same_origin(request) and self._access.session_open(SessionId(session))


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
