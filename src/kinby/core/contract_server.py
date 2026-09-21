"""Carry the contract to network clients, one socket per connection."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from collections.abc import AsyncGenerator, Coroutine
from contextlib import aclosing, suppress
from enum import Enum, auto
from functools import partial

from aiohttp import WSMsgType, web

from kinby.contracts import (
    CONTROL_SCOPES,
    INSTANCE_SCOPES,
    THREAD_APPROVAL_RESPOND,
    THREAD_TURN_INTERRUPT,
    CallFrame,
    CancelFrame,
    ClientFrame,
    ContractModel,
    ControlToken,
    EndFrame,
    ErrorCode,
    ErrorEnvelope,
    ErrorFrame,
    FrameId,
    ItemFrame,
    ResultFrame,
    Scope,
    ServerFrame,
    SubscribedFrame,
    SubscribeFrame,
    parse_client_frame,
)
from kinby.core.dispatcher import Dispatcher

CONTROL_TOKEN_VARIABLE = "KINBY_CONTROL_TOKEN"
#: Items one subscription may hold for a client that is not reading fast enough.
SUBSCRIPTION_QUEUE_LIMIT = 1024
#: Unary calls one connection may hold in each bucket: ordinary, or interrupt and approval.
CALL_LIMIT = 8
_HEARTBEAT_SECONDS = 30
_UNBLOCKING = frozenset({THREAD_TURN_INTERRUPT.name, THREAD_APPROVAL_RESPOND.name})

_logger = logging.getLogger(__name__)

_OVERFLOWED = ErrorEnvelope(
    code=ErrorCode.RESOURCE_EXHAUSTED,
    message=(
        f"The subscription fell more than {SUBSCRIPTION_QUEUE_LIMIT} items behind. "
        "Subscribe again after the last sequence you received."
    ),
    retryable=True,
)
_TOO_MANY_CALLS = ErrorEnvelope(
    code=ErrorCode.RESOURCE_EXHAUSTED,
    message=(f"This connection already has {CALL_LIMIT} calls in flight. Wait for one to finish."),
    retryable=True,
)


class _StreamEnd(Enum):
    """Why a subscription stopped producing, queued behind the items it already read."""

    COMPLETE = auto()
    OVERFLOWED = auto()


async def serve_contract(
    request: web.Request,
    dispatcher: Dispatcher,
    scopes: frozenset[Scope],
) -> web.WebSocketResponse:
    """Upgrade an authenticated request and carry the contract over it until it closes."""
    socket = web.WebSocketResponse(heartbeat=_HEARTBEAT_SECONDS)
    await socket.prepare(request)
    await _Connection(socket, dispatcher, scopes).serve()
    return socket


class ContractServer:
    """Serve the contract at ``/ws``, and the routes a hub drives at ``/control``."""

    def __init__(self, dispatcher: Dispatcher, token: ControlToken) -> None:
        self._dispatcher = dispatcher
        self._token = token

    @classmethod
    def from_environment(cls, dispatcher: Dispatcher) -> ContractServer | None:
        """Build the server from the instance's control token, or stay off without one."""
        token = os.environ.get(CONTROL_TOKEN_VARIABLE)
        if not token:
            _logger.info(
                "Contract server off: %s is not set in the environment.",
                CONTROL_TOKEN_VARIABLE,
            )
            return None
        return cls(dispatcher, ControlToken(token))

    def add_routes(self, application: web.Application) -> None:
        application.router.add_get("/ws", self._instance_socket, allow_head=False)
        application.router.add_get("/control", self._control_socket, allow_head=False)

    async def _instance_socket(self, request: web.Request) -> web.WebSocketResponse:
        return await self._serve(request, INSTANCE_SCOPES)

    async def _control_socket(self, request: web.Request) -> web.WebSocketResponse:
        return await self._serve(request, CONTROL_SCOPES)

    async def _serve(self, request: web.Request, scopes: frozenset[Scope]) -> web.WebSocketResponse:
        if not self._authenticated(request):
            raise web.HTTPUnauthorized(reason="authentication failed")
        return await serve_contract(request, self._dispatcher, scopes)

    def _authenticated(self, request: web.Request) -> bool:
        return hmac.compare_digest(
            request.headers.get("Authorization", ""),
            f"Bearer {self._token}",
        )


class _Connection:
    """One socket: the frames it carries, and the subscriptions that end when it closes."""

    def __init__(
        self,
        socket: web.WebSocketResponse,
        dispatcher: Dispatcher,
        scopes: frozenset[Scope],
    ) -> None:
        self._socket = socket
        self._dispatcher = dispatcher
        self._scopes = scopes
        self._subscriptions: dict[FrameId, asyncio.Task[None]] = {}
        self._calls: set[asyncio.Task[None]] = set()
        self._unblocking: set[asyncio.Task[None]] = set()
        self._sending = asyncio.Lock()

    async def serve(self) -> None:
        try:
            async for message in self._socket:
                if message.type is WSMsgType.TEXT:
                    await self._receive(message.data)
        finally:
            open_subscriptions = list(self._subscriptions.values())
            for subscription in open_subscriptions:
                subscription.cancel()
            await asyncio.gather(*open_subscriptions, return_exceptions=True)

    async def _receive(self, message: str) -> None:
        frame = parse_client_frame(message)
        if isinstance(frame, ErrorEnvelope):
            _logger.info("Frame rejected: it could not be read.")
            await self._send(ErrorFrame(error=frame))
            return
        _log(frame)
        match frame:
            case CallFrame():
                await self._begin(frame)
            case SubscribeFrame():
                await self._open(frame)
            case CancelFrame():
                await self._cancel(frame)

    async def _begin(self, frame: CallFrame) -> None:
        bucket = self._unblocking if frame.method in _UNBLOCKING else self._calls
        if len(bucket) >= CALL_LIMIT:
            await self._send(ErrorFrame(id=frame.id, error=_TOO_MANY_CALLS))
            return
        self._spawn(self._call(frame), bucket)

    def _spawn(
        self,
        work: Coroutine[object, object, None],
        bucket: set[asyncio.Task[None]],
    ) -> None:
        task = asyncio.create_task(work)
        bucket.add(task)
        task.add_done_callback(lambda done: self._forget_call(done, bucket))

    def _forget_call(self, task: asyncio.Task[None], bucket: set[asyncio.Task[None]]) -> None:
        bucket.discard(task)
        _log_task_error(task, "A call failed.")

    async def _call(self, frame: CallFrame) -> None:
        result = await self._dispatcher.dispatch(frame.method, frame.params, self._scopes)
        if isinstance(result, ErrorEnvelope):
            await self._send(ErrorFrame(id=frame.id, error=result))
            return
        await self._send(ResultFrame(id=frame.id, result=result.model_dump(mode="json")))

    async def _open(self, frame: SubscribeFrame) -> None:
        if frame.id in self._subscriptions:
            await self._send(ErrorFrame(id=frame.id, error=_already_open(frame.id)))
            return
        subscription = asyncio.create_task(self._stream(frame))
        self._subscriptions[frame.id] = subscription
        subscription.add_done_callback(partial(self._forget, frame.id))

    async def _cancel(self, frame: CancelFrame) -> None:
        subscription = self._subscriptions.pop(frame.id, None)
        if subscription is None:
            await self._send(ErrorFrame(id=frame.id, error=_not_open(frame.id)))
            return
        subscription.cancel()
        with suppress(asyncio.CancelledError):
            await subscription
        await self._send(EndFrame(id=frame.id))

    def _forget(self, frame_id: FrameId, subscription: asyncio.Task[None]) -> None:
        if self._subscriptions.get(frame_id) is subscription:
            del self._subscriptions[frame_id]
        _log_task_error(subscription, f"Subscription {frame_id} failed.")

    async def _stream(self, frame: SubscribeFrame) -> None:
        stream = await self._dispatcher.subscribe(frame.method, frame.params, self._scopes)
        if isinstance(stream, ErrorEnvelope):
            await self._send(ErrorFrame(id=frame.id, error=stream))
            return
        try:
            await self._send(SubscribedFrame(id=frame.id, head_sequence=stream.head_sequence))
            queue: asyncio.Queue[ContractModel | _StreamEnd] = asyncio.Queue(
                SUBSCRIPTION_QUEUE_LIMIT + 1
            )
            filling = asyncio.create_task(_fill(stream.items, queue))
            try:
                await self._drain(frame.id, queue)
            finally:
                filling.cancel()
                with suppress(asyncio.CancelledError):
                    await filling
        finally:
            await stream.aclose()

    async def _drain(
        self,
        frame_id: FrameId,
        queue: asyncio.Queue[ContractModel | _StreamEnd],
    ) -> None:
        while True:
            item = await queue.get()
            if isinstance(item, _StreamEnd):
                if item is _StreamEnd.OVERFLOWED:
                    await self._send(ErrorFrame(id=frame_id, error=_OVERFLOWED))
                await self._send(EndFrame(id=frame_id))
                return
            await self._send(ItemFrame(id=frame_id, item=item.model_dump(mode="json")))

    async def _send(self, frame: ServerFrame) -> None:
        async with self._sending:
            if self._socket.closed:
                return
            await self._socket.send_str(frame.model_dump_json())


def _log_task_error(task: asyncio.Task[None], message: str) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        _logger.error(message, exc_info=error)


async def _fill(
    items: AsyncGenerator[ContractModel],
    queue: asyncio.Queue[ContractModel | _StreamEnd],
) -> None:
    """Read the subscription ahead of the socket, up to the items one client may hold."""
    async with aclosing(items):
        async for item in items:
            if queue.qsize() >= SUBSCRIPTION_QUEUE_LIMIT:
                queue.put_nowait(_StreamEnd.OVERFLOWED)
                return
            queue.put_nowait(item)
    queue.put_nowait(_StreamEnd.COMPLETE)


def _log(frame: ClientFrame) -> None:
    """Log what a frame asks for, never its params: those carry the user's own content."""
    method = frame.method if isinstance(frame, CallFrame | SubscribeFrame) else None
    _logger.info("Frame %s id=%s method=%s", frame.type.value, frame.id, method)


def _already_open(frame_id: FrameId) -> ErrorEnvelope:
    return ErrorEnvelope(
        code=ErrorCode.INVALID_ARGUMENT,
        message=f'Subscription "{frame_id}" is already open on this connection.',
        retryable=False,
    )


def _not_open(frame_id: FrameId) -> ErrorEnvelope:
    return ErrorEnvelope(
        code=ErrorCode.INVALID_ARGUMENT,
        message=f'Subscription "{frame_id}" is not open on this connection.',
        retryable=False,
    )
