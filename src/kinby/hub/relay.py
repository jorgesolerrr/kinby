"""Carry traffic to one instance's private routes: a client's socket and a webhook travel unread,
but for the approvals on the home instance's socket."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from urllib.parse import quote

import aiohttp
from aiohttp import WSMsgType, web
from multidict import CIMultiDict
from pydantic import JsonValue

from kinby.contracts import (
    CallFrame,
    ErrorEnvelope,
    ErrorFrame,
    FrameId,
    ResultFrame,
    parse_server_frame,
)
from kinby.core.contract_server import HEARTBEAT_SECONDS
from kinby.core.errors import InstanceAnswerLost, InstanceRefused, InstanceUnavailable
from kinby.hub.approvals import HomeApprovals
from kinby.hub.browser_profiles import BrowserSession
from kinby.hub.models import InstanceEndpoint, InstanceUnreachable

#: Headers that describe the connection ending at the hub, plus the ones the next hop rewrites.
_NOT_FORWARDED = frozenset(
    {
        "connection",
        "content-length",
        # The hub's session cookie is its own authority. It never travels to an instance.
        "cookie",
        "host",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_PLAYWRIGHT_HEADERS = "x-playwright-"
_LAUNCH_OPTIONS_HEADER = "x-playwright-launch-options"
#: A webhook sender waits on its own clock, so a hung instance must fail rather than hold it.
_SIGNAL_TIMEOUT_SECONDS = 30
#: A turn waits on a relayed read, so a hung instance must fail rather than hold the turn open.
RELAYED_CALL_SECONDS = 30


def unreachable(reason: InstanceUnreachable) -> web.HTTPException:
    """An instance the hub does not manage is missing; one it cannot reach is unavailable."""
    if reason is InstanceUnreachable.MISSING:
        return web.HTTPNotFound(reason="unknown instance")
    return web.HTTPServiceUnavailable(reason="the instance is not running")


async def relay_socket(
    request: web.Request, endpoint: InstanceEndpoint, approvals: HomeApprovals | None = None
) -> web.WebSocketResponse:
    """Carry frames both ways unparsed, so the hub knows no instance method. On the home
    instance's socket, *approvals* sees each frame first."""
    session = aiohttp.ClientSession()
    try:
        upstream = await session.ws_connect(
            f"{endpoint.url}/ws",
            headers={"Authorization": f"Bearer {endpoint.control_token}"},
            heartbeat=HEARTBEAT_SECONDS,
        )
    except (aiohttp.ClientError, TimeoutError) as exc:
        await session.close()
        raise unreachable(InstanceUnreachable.UNAVAILABLE) from exc
    downstream = web.WebSocketResponse(heartbeat=HEARTBEAT_SECONDS)
    await downstream.prepare(request)
    try:
        await _carry(downstream, upstream, approvals)
    finally:
        await upstream.close()
        await session.close()
    return downstream


async def relay_browser(
    request: web.Request, address: str, launch_options: str, browser: BrowserSession
) -> web.WebSocketResponse:
    """Carry a Playwright client to the browser service at *address* through *browser*. The
    service launches its browser with *launch_options*, whatever the client asked for."""
    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower().startswith(_PLAYWRIGHT_HEADERS)
    }
    headers[_LAUNCH_OPTIONS_HEADER] = launch_options
    session = aiohttp.ClientSession()
    try:
        # A screenshot or a page's tree can outgrow the default size cap on a frame.
        upstream = await session.ws_connect(
            address, headers=headers, max_msg_size=0, heartbeat=HEARTBEAT_SECONDS
        )
    except (aiohttp.ClientError, TimeoutError) as exc:
        await session.close()
        raise web.HTTPServiceUnavailable(reason="the browser service is not running") from exc
    # A peer that went away without closing would otherwise hold its browser for good.
    downstream = web.WebSocketResponse(max_msg_size=0, heartbeat=HEARTBEAT_SECONDS)
    await downstream.prepare(request)
    try:
        await browser.carry(downstream, upstream)
    finally:
        await upstream.close()
        await session.close()
    return downstream


async def relayed_call(
    endpoint: InstanceEndpoint,
    method: str,
    params: dict[str, JsonValue],
    *,
    socket_path: str = "/ws",
) -> dict[str, JsonValue]:
    """Make one call on the instance's socket, the one the relay carries a client to unless
    *socket_path* names another, and return what the instance answered.

    Once the socket is open, a call that gets no answer raises InstanceAnswerLost: the instance
    may have acted on it.
    """
    call = CallFrame(id=FrameId("1"), method=method, params=params)
    connected = False
    try:
        async with (
            asyncio.timeout(RELAYED_CALL_SECONDS),
            aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {endpoint.control_token}"}
            ) as session,
            session.ws_connect(
                f"{endpoint.url}{socket_path}", heartbeat=HEARTBEAT_SECONDS
            ) as socket,
        ):
            connected = True
            await socket.send_str(call.model_dump_json())
            answer = await socket.receive_str()
    except (aiohttp.ClientError, aiohttp.WSMessageTypeError, TimeoutError) as exc:
        raise (InstanceAnswerLost if connected else InstanceUnavailable)(
            f'Instance "{endpoint.instance_id}" did not answer "{method}": '
            f"{exc or type(exc).__name__}"
        ) from exc
    match parse_server_frame(answer):
        case ResultFrame(result=result):
            return result
        case ErrorFrame(error=error) | (ErrorEnvelope() as error):
            raise InstanceRefused(error)
        case other:
            raise InstanceAnswerLost(
                f'Instance "{endpoint.instance_id}" answered "{method}" with "{other.type.value}".'
            )


async def forward_signal(
    request: web.Request,
    endpoint: InstanceEndpoint,
    routine: str,
) -> web.Response:
    """Forward a webhook byte for byte. Only the instance that recorded it may acknowledge it."""
    body = await request.read()
    try:
        async with (
            aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=_SIGNAL_TIMEOUT_SECONDS)
            ) as session,
            session.post(
                # Quoted, so a routine name can never walk out of the instance's signal path.
                f"{endpoint.url}/signals/{quote(routine, safe='')}",
                data=body,
                headers=forwarded_headers(request.headers),
            ) as answered,
        ):
            return web.Response(
                status=answered.status,
                body=await answered.read(),
                content_type=answered.content_type,
            )
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise unreachable(InstanceUnreachable.UNAVAILABLE) from exc


def forwarded_headers(headers: Mapping[str, str]) -> CIMultiDict[str]:
    """Keep every header the instance authenticates with, drop the ones this hop owns."""
    return CIMultiDict(
        (name, value) for name, value in headers.items() if name.lower() not in _NOT_FORWARDED
    )


async def _carry(
    downstream: web.WebSocketResponse,
    upstream: aiohttp.ClientWebSocketResponse,
    approvals: HomeApprovals | None,
) -> None:
    both = (
        asyncio.create_task(
            _pump(downstream, upstream, approvals.client_sent if approvals else _unseen)
        ),
        asyncio.create_task(
            _pump(upstream, downstream, approvals.instance_sent if approvals else _unseen)
        ),
    )
    _, pending = await asyncio.wait(both, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


async def _pump(
    source: web.WebSocketResponse | aiohttp.ClientWebSocketResponse,
    sink: web.WebSocketResponse | aiohttp.ClientWebSocketResponse,
    seen: Callable[[str], None],
) -> None:
    async for message in source:
        if message.type is WSMsgType.TEXT:
            seen(message.data)
            await sink.send_str(message.data)
        elif message.type is WSMsgType.BINARY:
            await sink.send_bytes(message.data)
    await sink.close()


def _unseen(message: str) -> None:
    pass
