"""Receive authenticated signals over HTTP."""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import Awaitable, Callable, Mapping

from aiohttp import web

from kinby.contracts import Delivery, DeliveryId, RoutineName, RoutineTrigger
from kinby.core.clock import utc_now
from kinby.core.errors import RoutineNotFound
from kinby.core.scheduler import Scheduler
from kinby.instance import Instance, Serve
from kinby.plugins.routines import (
    Routine,
    SignalConfig,
    TokenSignalConfig,
    load_routine,
    strip_signal_credentials,
)

_REJECTED_STATUSES = frozenset({401, 404, 405, 410, 413})
_CLIENT_MAX_SIZE = 1024**2
# AppRunner otherwise keeps idle connections open for 3,630 seconds.
_KEEPALIVE_TIMEOUT_SECONDS = 75


def _log_rejection(request: web.Request, response: web.StreamResponse) -> None:
    if response.status in _REJECTED_STATUSES:
        logging.getLogger(__name__).info(
            "Signal request rejected at %s: %s %s",
            request.path,
            response.status,
            response.reason,
        )


@web.middleware
async def _rejection_log(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    try:
        response = await handler(request)
    except web.HTTPException as rejection:
        _log_rejection(request, rejection)
        raise
    _log_rejection(request, response)
    return response


def verify(signal: SignalConfig, headers: Mapping[str, str], body: bytes) -> bool:
    secret = os.environ[signal.secret_name]
    if isinstance(signal, TokenSignalConfig):
        return hmac.compare_digest(
            headers.get("Authorization", ""),
            f"Bearer {secret}",
        )
    signature = headers.get(signal.signature_header, "")
    if signature.startswith("sha256="):
        signature = signature.removeprefix("sha256=")
    expected = hmac.digest(secret.encode(), body, "sha256").hex()
    return hmac.compare_digest(signature, expected)


class Receiver:
    def __init__(
        self,
        listen: Serve,
        scheduler: Scheduler,
        instance: Instance,
    ) -> None:
        self._listen = listen
        self._scheduler = scheduler
        self._instance = instance
        self._runner: web.AppRunner | None = None

    async def start(self) -> Serve:
        application = web.Application(
            client_max_size=_CLIENT_MAX_SIZE,
            middlewares=(_rejection_log,),
        )
        application.router.add_get("/health", self._health, allow_head=False)
        application.router.add_route("*", "/health", self._not_found)
        application.router.add_post("/signals/{routine}", self._receive)
        runner = web.AppRunner(
            application,
            keepalive_timeout=_KEEPALIVE_TIMEOUT_SECONDS,
        )
        await runner.setup()
        site = web.TCPSite(
            runner,
            self._listen.host,
            self._listen.port,
        )
        await site.start()
        self._runner = runner
        return Serve(self._listen.host, site.port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _health(self, _request: web.Request) -> web.Response:
        return web.json_response({"id": self._instance.manifest.id})

    async def _not_found(self, _request: web.Request) -> web.Response:
        raise web.HTTPNotFound()

    async def _receive(self, request: web.Request) -> web.Response:
        try:
            routine = self._routine(RoutineName(request.match_info["routine"]))
        except ValueError as exc:
            raise web.HTTPNotFound(reason="routine could not be loaded") from exc
        if routine is None or routine.signal is None:
            raise web.HTTPNotFound(reason="unknown routine or no signal")
        signal = routine.signal
        body = await request.read()
        if not verify(signal, request.headers, body):
            return web.Response(status=401, reason="authentication failed")
        if not routine.enabled:
            raise web.HTTPGone(reason="routine disabled")

        headers = strip_signal_credentials(signal, request.headers)
        delivery_header = signal.delivery_header
        delivery_id = request.headers.get(delivery_header) if delivery_header is not None else None
        try:
            receipt = await self._scheduler.receive(
                routine.name,
                Delivery(
                    headers=headers,
                    content_type=request.headers.get("Content-Type", request.content_type),
                    body=body.decode(),
                    delivery_id=DeliveryId(delivery_id) if delivery_id is not None else None,
                    received_at=utc_now(),
                ),
                RoutineTrigger.SIGNAL,
            )
        except RoutineNotFound as exc:
            raise web.HTTPNotFound(reason="unknown routine") from exc
        accepted = receipt.accepted
        return web.json_response(
            {"thread_id": str(accepted.thread_id), "sequence": accepted.sequence},
            status=200 if receipt.repeated else 202,
        )

    def _routine(self, name: RoutineName) -> Routine | None:
        return load_routine(self._instance, name)
