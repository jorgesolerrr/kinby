"""Carry public traffic to one instance's private routes, without reading what it carries."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from urllib.parse import quote

import aiohttp
from aiohttp import WSMsgType, web

from kinby.core.contract_server import HEARTBEAT_SECONDS
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
#: A webhook sender waits on its own clock, so a hung instance must fail rather than hold it.
_SIGNAL_TIMEOUT_SECONDS = 30


def unreachable(reason: InstanceUnreachable) -> web.HTTPException:
    """An instance the hub does not manage is missing; one it cannot reach is unavailable."""
    if reason is InstanceUnreachable.MISSING:
        return web.HTTPNotFound(reason="unknown instance")
    return web.HTTPServiceUnavailable(reason="the instance is not running")


async def relay_socket(request: web.Request, endpoint: InstanceEndpoint) -> web.WebSocketResponse:
    """Carry frames both ways unparsed, so the hub knows no instance method."""
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
        await _carry(downstream, upstream)
    finally:
        await upstream.close()
        await session.close()
    return downstream


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
                headers=_forwarded(request.headers),
            ) as answered,
        ):
            return web.Response(
                status=answered.status,
                body=await answered.read(),
                content_type=answered.content_type,
            )
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise unreachable(InstanceUnreachable.UNAVAILABLE) from exc


def _forwarded(headers: Mapping[str, str]) -> dict[str, str]:
    """Keep every header the instance authenticates with, drop the ones this hop owns."""
    return {name: value for name, value in headers.items() if name.lower() not in _NOT_FORWARDED}


async def _carry(
    downstream: web.WebSocketResponse,
    upstream: aiohttp.ClientWebSocketResponse,
) -> None:
    both = (
        asyncio.create_task(_pump(downstream, upstream)),
        asyncio.create_task(_pump(upstream, downstream)),
    )
    _, pending = await asyncio.wait(both, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


async def _pump(
    source: web.WebSocketResponse | aiohttp.ClientWebSocketResponse,
    sink: web.WebSocketResponse | aiohttp.ClientWebSocketResponse,
) -> None:
    async for message in source:
        if message.type is WSMsgType.TEXT:
            await sink.send_str(message.data)
        elif message.type is WSMsgType.BINARY:
            await sink.send_bytes(message.data)
    await sink.close()
