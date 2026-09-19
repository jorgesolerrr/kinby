"""Drive a contract server over one WebSocket that reconnects under the client."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncGenerator, AsyncIterator, Collection, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from http import HTTPStatus
from itertools import count

import aiohttp

from kinby.cli.client import ContractClient
from kinby.contracts import (
    INSTANCE_SCOPES,
    RESULT_MODELS,
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
    Stream,
    SubscribedFrame,
    SubscribeFrame,
    parse_server_frame,
)

TOKEN_VARIABLE = "KINBY_TOKEN"
#: Full-jitter backoff between reconnects, in seconds.
FIRST_BACKOFF = 0.5
LAST_BACKOFF = 30.0
#: The item field carrying a subscription's position, and the command field that resumes from it.
_SEQUENCE = "sequence"
_AFTER_SEQUENCE = "after_sequence"

_logger = logging.getLogger(__name__)

CONNECTION_LOST = ErrorEnvelope(
    code=ErrorCode.CONNECTION_LOST,
    message="The connection dropped before the call returned. Read the events to see its outcome.",
    retryable=False,
)
AUTHENTICATION_FAILED = ErrorEnvelope(
    code=ErrorCode.PERMISSION_DENIED,
    message="The contract server rejected the token.",
    retryable=False,
)
_UNREADABLE = ErrorEnvelope(
    code=ErrorCode.INTERNAL,
    message="The contract server answered with something this client could not read.",
    retryable=False,
)


@dataclass(frozen=True)
class _Call:
    """One unary call in flight: the model its result arrives as, and who waits for it."""

    result: type[ContractModel]
    answer: asyncio.Future[ContractModel]


@dataclass
class _Subscription:
    """One open subscription: what it asked for, and how far its items have come."""

    id: FrameId
    method: str
    params: Mapping[str, object]
    item: type[ContractModel]
    opened: asyncio.Future[int | ErrorEnvelope]
    items: asyncio.Queue[ContractModel | None]
    last_sequence: int = 0

    def frame(self) -> SubscribeFrame:
        """Ask from the last sequence seen, so asking again neither repeats nor skips an item."""
        params = (
            {**self.params, _AFTER_SEQUENCE: self.last_sequence}
            if self.last_sequence
            else self.params
        )
        return SubscribeFrame.model_validate(
            {"id": self.id, "method": self.method, "params": params}
        )

    def receive(self, payload: Mapping[str, object]) -> None:
        sequence = payload.get(_SEQUENCE)
        if isinstance(sequence, int):
            self.last_sequence = sequence
        self.items.put_nowait(_validated(self.item, payload))

    def fail(self, error: ErrorEnvelope) -> bool:
        """Answer a subscription that never opened; once open, an error is one more item."""
        if self.opened.done():
            self.items.put_nowait(error)
            return False
        self.opened.set_result(error)
        return True

    def close(self) -> None:
        self.items.put_nowait(None)


class ContractSocket:
    """The two callables of a contract client, answered over a socket that comes and goes."""

    def __init__(self, session: aiohttp.ClientSession, url: str) -> None:
        self._session = session
        self._url = url
        self._socket: aiohttp.ClientWebSocketResponse | None = None
        self._live = asyncio.Event()
        self._stopped: ErrorEnvelope | None = None
        self._calls: dict[FrameId, _Call] = {}
        self._subscriptions: dict[FrameId, _Subscription] = {}
        self._ids = count(1)

    @asynccontextmanager
    async def connected(self) -> AsyncIterator[None]:
        """Hold the connection open for the body, reconnecting whenever it drops."""
        connecting = asyncio.create_task(self._connect())
        try:
            yield
        finally:
            connecting.cancel()
            with suppress(asyncio.CancelledError):
                await connecting

    async def dispatch(
        self,
        method: str,
        params: Mapping[str, object],
        scopes: Collection[Scope],
    ) -> ContractModel:
        """Scopes ride on the token: the contract server decides what this connection holds."""
        del scopes
        await self._live.wait()
        if self._stopped is not None:
            return self._stopped
        frame_id = self._next_id()
        answer: asyncio.Future[ContractModel] = asyncio.get_running_loop().create_future()
        self._calls[frame_id] = _Call(RESULT_MODELS[method], answer)
        try:
            sent = await self._send(
                CallFrame.model_validate({"id": frame_id, "method": method, "params": params})
            )
            return await answer if sent else CONNECTION_LOST
        finally:
            del self._calls[frame_id]

    async def subscribe(
        self,
        method: str,
        params: Mapping[str, object],
        scopes: Collection[Scope],
    ) -> Stream[ContractModel] | ErrorEnvelope:
        del scopes
        await self._live.wait()
        if self._stopped is not None:
            return self._stopped
        frame_id = self._next_id()
        subscription = _Subscription(
            frame_id,
            method,
            params,
            RESULT_MODELS[method],
            asyncio.get_running_loop().create_future(),
            asyncio.Queue(),
        )
        self._subscriptions[frame_id] = subscription
        await self._send(subscription.frame())
        head_sequence = await subscription.opened
        if isinstance(head_sequence, ErrorEnvelope):
            return head_sequence
        return Stream(head_sequence, self._items(subscription))

    async def _connect(self) -> None:
        """Reconnect with full-jitter backoff until the body is done, or the token is rejected."""
        attempt = 0
        while self._stopped is None:
            try:
                async with self._session.ws_connect(self._url) as socket:
                    attempt = 0
                    await self._opened(socket)
                    async for message in socket:
                        if message.type is aiohttp.WSMsgType.TEXT:
                            self._answer(parse_server_frame(message.data))
            except aiohttp.WSServerHandshakeError as refusal:
                if refusal.status == HTTPStatus.UNAUTHORIZED:
                    self._stopped = AUTHENTICATION_FAILED
                _logger.info("The contract server refused the connection: %s", refusal)
            except (aiohttp.ClientError, OSError) as failure:
                _logger.info("The contract server connection failed: %s", failure)
            self._dropped()
            if self._stopped is None:
                await asyncio.sleep(_backoff(attempt))
                attempt += 1

    async def _opened(self, socket: aiohttp.ClientWebSocketResponse) -> None:
        self._socket = socket
        for subscription in list(self._subscriptions.values()):
            await self._send(subscription.frame())
        self._live.set()

    def _dropped(self) -> None:
        """ADR 0048: a call in flight when the socket drops fails, and is never retried."""
        self._socket = None
        self._live.clear()
        for call in self._calls.values():
            if not call.answer.done():
                call.answer.set_result(self._stopped or CONNECTION_LOST)
        if self._stopped is None:
            return
        for subscription in self._subscriptions.values():
            subscription.fail(self._stopped)
            subscription.close()
        self._subscriptions.clear()
        self._live.set()

    async def _items(self, subscription: _Subscription) -> AsyncGenerator[ContractModel]:
        try:
            while (item := await subscription.items.get()) is not None:
                yield item
        finally:
            await self._cancel(subscription)

    async def _cancel(self, subscription: _Subscription) -> None:
        if self._subscriptions.pop(subscription.id, None) is not None:
            await self._send(CancelFrame(id=subscription.id))

    def _answer(self, frame: ContractModel) -> None:
        match frame:
            case ResultFrame():
                self._resolve(frame.id, frame.result)
            case SubscribedFrame():
                self._open(frame)
            case ItemFrame():
                self._item(frame)
            case EndFrame():
                self._end(frame)
            case ErrorFrame() if frame.id is not None:
                self._error(frame.id, frame.error)

    def _resolve(self, frame_id: FrameId, payload: Mapping[str, object]) -> None:
        call = self._calls.get(frame_id)
        if call is not None and not call.answer.done():
            call.answer.set_result(_validated(call.result, payload))

    def _open(self, frame: SubscribedFrame) -> None:
        subscription = self._subscriptions.get(frame.id)
        if subscription is not None and not subscription.opened.done():
            subscription.opened.set_result(frame.head_sequence)

    def _item(self, frame: ItemFrame) -> None:
        subscription = self._subscriptions.get(frame.id)
        if subscription is not None:
            subscription.receive(frame.item)

    def _end(self, frame: EndFrame) -> None:
        subscription = self._subscriptions.pop(frame.id, None)
        if subscription is not None:
            subscription.close()

    def _error(self, frame_id: FrameId, error: ErrorEnvelope) -> None:
        call = self._calls.get(frame_id)
        if call is not None and not call.answer.done():
            call.answer.set_result(error)
            return
        subscription = self._subscriptions.get(frame_id)
        if subscription is not None and subscription.fail(error):
            del self._subscriptions[frame_id]

    def _next_id(self) -> FrameId:
        return FrameId(str(next(self._ids)))

    async def _send(self, frame: ClientFrame) -> bool:
        socket = self._socket
        if socket is None or socket.closed:
            return False
        try:
            await socket.send_str(frame.model_dump_json())
        except aiohttp.ClientError, ConnectionError:
            return False
        return True


@asynccontextmanager
async def contract_client(url: str, token: ControlToken) -> AsyncIterator[ContractClient]:
    """Open a client on a contract server, authenticated with a bearer token."""
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession(headers=headers) as session:
        contract = ContractSocket(session, url)
        async with contract.connected():
            yield ContractClient(contract.dispatch, contract.subscribe, INSTANCE_SCOPES)


def _backoff(attempt: int) -> float:
    """Full jitter under a doubling ceiling, so many clients never retry in step."""
    return random.uniform(0, min(LAST_BACKOFF, FIRST_BACKOFF * 2**attempt))


def _validated(model: type[ContractModel], payload: Mapping[str, object]) -> ContractModel:
    try:
        return model.model_validate(payload)
    except ValueError:
        return _UNREADABLE
