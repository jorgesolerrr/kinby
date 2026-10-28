"""The browser service's container: Playwright's browser server, and the proxy every browser it
launches reaches the web through (ADR 0113).

The proxy resolves each host itself and refuses one that resolves to an address that is not
public. Every request a page makes passes it, a redirect's next hop included, which Playwright's
own request interception never sees.

    python -m kinby.hub.browser_service
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import suppress
from functools import partial
from urllib.parse import urlsplit

from kinby.plugins.defaults.web import REFUSED_HEADER, RefusedUrl, public_address

#: Where Playwright's browser server listens for the hub's Playwright client.
PORT = 3000
_PROXY_PORT = 3128
#: The launch options of every browser the hub connects to.
LAUNCH_OPTIONS = json.dumps({"proxy": {"server": f"http://127.0.0.1:{_PROXY_PORT}"}})
_HOP_HEADERS = (b"connection:", b"proxy-connection:", b"keep-alive:", b"proxy-authorization:")

#: The address to connect to for a host and port, or RefusedUrl.
Resolve = Callable[[str, int], Awaitable[str]]


async def serve_proxy(host: str, port: int, *, resolve: Resolve = public_address) -> asyncio.Server:
    """Serve the proxy on *host* and *port*. It reaches only the addresses *resolve* gives."""
    return await asyncio.start_server(partial(_proxy, resolve), host, port)


async def _proxy(
    resolve: Resolve, client: asyncio.StreamReader, answer: asyncio.StreamWriter
) -> None:
    """Carry one connection: a tunnel for CONNECT, or else one plain HTTP request."""
    try:
        head = await client.readuntil(b"\r\n\r\n")
        request_line, *headers = head.removesuffix(b"\r\n\r\n").split(b"\r\n")
        method, target, version = request_line.decode("latin-1").split(" ", 2)
        address = urlsplit(f"//{target}" if method == "CONNECT" else target)
        port = address.port or (443 if method == "CONNECT" else 80)
        try:
            upstream_reader, upstream = await asyncio.open_connection(
                await resolve(address.hostname or "", port), port
            )
        except (RefusedUrl, OSError) as exc:
            answer.write(_refusal(exc))
            return
        if method == "CONNECT":
            answer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        else:
            path = address.path or "/"
            if address.query:
                path = f"{path}?{address.query}"
            kept = [header for header in headers if not header.lower().startswith(_HOP_HEADERS)]
            upstream.write(
                b"\r\n".join(
                    [f"{method} {path} {version}".encode("latin-1"), *kept, b"Connection: close"]
                )
                + b"\r\n\r\n"
            )
        await asyncio.gather(_pipe(client, upstream), _pipe(upstream_reader, answer))
    except asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError, OSError:
        pass
    finally:
        answer.close()


async def _pipe(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
    try:
        while chunk := await source.read(65_536):
            sink.write(chunk)
            await sink.drain()
    except OSError:
        pass
    finally:
        with suppress(OSError):
            sink.write_eof()


def _refusal(reason: Exception) -> bytes:
    message = str(reason).replace("\r", " ").replace("\n", " ")
    status = "403 Forbidden" if isinstance(reason, RefusedUrl) else "502 Bad Gateway"
    body = message.encode()
    return (
        f"HTTP/1.1 {status}\r\n{REFUSED_HEADER}: {message}\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\nContent-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode() + body


async def main() -> int:
    """Run the proxy, and the browser server for as long as it runs."""
    proxy = await serve_proxy("127.0.0.1", _PROXY_PORT)
    async with proxy:
        server = await asyncio.create_subprocess_exec(
            "playwright", "run-server", "--host", "0.0.0.0", "--port", str(PORT)
        )
        return await server.wait()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
