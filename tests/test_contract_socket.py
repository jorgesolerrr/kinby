import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from io import StringIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from aiohttp import web
from aiohttp.typedefs import Handler, Middleware

from kinby.cli import main
from kinby.cli.contract_socket import (
    AUTHENTICATION_FAILED,
    CONNECTION_LOST,
    FIRST_BACKOFF,
    TOKEN_VARIABLE,
    UNREADABLE,
    InsecureContractUrl,
    contract_client,
    protected_contract_url,
)
from kinby.contracts import (
    THREAD_CREATE,
    THREAD_LIST,
    THREAD_SUBSCRIBE,
    THREAD_TURN_INTERRUPT,
    THREAD_TURN_START,
    AcceptedResult,
    ControlToken,
    ErrorEnvelope,
    Event,
    EventType,
    MessageDelta,
    Scope,
    ThreadCreateCommand,
    ThreadCreateResult,
    ThreadListCommand,
    ThreadListResult,
    ThreadSubscribeCommand,
    ThreadTurnInterruptCommand,
    ThreadTurnStartCommand,
)
from kinby.core.contract_server import ContractServer
from kinby.core.dispatcher import Dispatcher
from kinby.instance import Serve
from tests.test_contract_server import (
    COMPLETED,
    STARTED,
    TOKEN,
    created_thread,
    dispatcher_at,
    served,
    store_history,
)
from tests.test_routines import instance_at


def url(address: Serve, path: str = "/ws") -> str:
    return f"http://{address.host}:{address.port}{path}"


def test_a_call_crosses_the_socket_and_returns_its_result(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        async with (
            served(instance, dispatcher_at(tmp_path)) as address,
            contract_client(url(address), TOKEN) as client,
        ):
            created = await client.call(THREAD_CREATE, ThreadCreateCommand(title="Launch notes"))
            listed = await client.call(THREAD_LIST, ThreadListCommand())

        assert isinstance(created, ThreadCreateResult)
        assert isinstance(listed, ThreadListResult)
        assert [(thread.id, thread.title) for thread in listed.threads] == [
            (created.id, "Launch notes")
        ]

    asyncio.run(scenario())


def test_a_subscription_replays_to_its_head_sequence_then_streams_live(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        dispatcher = dispatcher_at(tmp_path)
        thread = await created_thread(dispatcher)
        store_history(
            instance.manifest.state_dir,
            thread.id,
            [STARTED, MessageDelta(text="Yesterday"), COMPLETED],
        )
        async with (
            served(instance, dispatcher) as address,
            contract_client(url(address), TOKEN) as client,
        ):
            stream = await client.subscribe(
                THREAD_SUBSCRIBE, ThreadSubscribeCommand(thread_id=thread.id)
            )
            assert not isinstance(stream, ErrorEnvelope)
            head = stream.head_sequence
            replayed = [await anext(stream.items) for _ in range(3)]
            await client.call(
                THREAD_TURN_START,
                ThreadTurnStartCommand(thread_id=thread.id, message="Hello"),
            )
            live = [await anext(stream.items) for _ in range(3)]
            await stream.aclose()

        assert head == 3
        assert [_sequence(item) for item in replayed] == [1, 2, 3]
        assert [_sequence(item) for item in live] == [4, 5, 6]
        assert [_type(item) for item in live] == [
            EventType.TURN_STARTED,
            EventType.MESSAGE_DELTA,
            EventType.TURN_COMPLETED,
        ]

    asyncio.run(scenario())


def _sequence(item: Event | ErrorEnvelope) -> int:
    assert isinstance(item, Event)
    return item.sequence


def _type(item: Event | ErrorEnvelope) -> EventType:
    assert isinstance(item, Event)
    return item.type


class RestartableServer:
    """A contract server that can drop every socket and come back on the same port."""

    def __init__(self, dispatcher: Dispatcher, *middlewares: Middleware) -> None:
        self._dispatcher = dispatcher
        self._middlewares = middlewares
        self._runner: web.AppRunner | None = None
        self.address = Serve("127.0.0.1", 0)

    async def start(self) -> None:
        application = web.Application(middlewares=self._middlewares)
        ContractServer(self._dispatcher, TOKEN).add_routes(application)
        runner = web.AppRunner(application, shutdown_timeout=0.1)
        await runner.setup()
        site = web.TCPSite(runner, self.address.host, self.address.port)
        await site.start()
        self._runner = runner
        self.address = Serve(self.address.host, site.port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None


async def head_sequence(dispatcher: Dispatcher, thread_id: UUID) -> int:
    stream = await dispatcher.subscribe(
        "thread.subscribe", {"thread_id": str(thread_id)}, set(Scope)
    )
    assert not isinstance(stream, ErrorEnvelope)
    try:
        return stream.head_sequence
    finally:
        await stream.aclose()


async def wait_for_events(dispatcher: Dispatcher, thread_id: UUID, sequence: int) -> None:
    async with asyncio.timeout(5):
        while await head_sequence(dispatcher, thread_id) < sequence:
            await asyncio.sleep(0.01)


def test_a_subscription_resumes_after_a_dropped_socket(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        dispatcher = dispatcher_at(tmp_path)
        thread = await created_thread(dispatcher)
        store_history(
            instance.manifest.state_dir,
            thread.id,
            [STARTED, MessageDelta(text="Yesterday"), COMPLETED],
        )
        server = RestartableServer(dispatcher)
        await server.start()
        try:
            async with contract_client(url(server.address), TOKEN) as client:
                stream = await client.subscribe(
                    THREAD_SUBSCRIBE, ThreadSubscribeCommand(thread_id=thread.id)
                )
                assert not isinstance(stream, ErrorEnvelope)
                replayed = [await anext(stream.items) for _ in range(3)]
                await server.stop()
                await dispatcher.dispatch(
                    "thread.turn.start",
                    {"thread_id": str(thread.id), "message": "Hello"},
                    set(Scope),
                )
                await wait_for_events(dispatcher, thread.id, 6)
                await server.start()
                resumed = [
                    await asyncio.wait_for(anext(stream.items), timeout=30) for _ in range(3)
                ]
                await stream.aclose()
        finally:
            await server.stop()

        assert [_sequence(item) for item in replayed] == [1, 2, 3]
        assert [_sequence(item) for item in resumed] == [4, 5, 6]

    asyncio.run(scenario())


def test_a_call_in_flight_when_the_socket_drops_fails_as_connection_lost() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        held = asyncio.Event()
        dispatcher = Dispatcher()

        async def hold(command: ThreadTurnInterruptCommand) -> AcceptedResult:
            started.set()
            await held.wait()
            return AcceptedResult(thread_id=command.thread_id, turn_id=uuid4(), sequence=1)

        dispatcher.register(THREAD_TURN_INTERRUPT, hold)
        server = RestartableServer(dispatcher)
        await server.start()
        try:
            async with contract_client(url(server.address), TOKEN) as client:
                waiting = asyncio.create_task(
                    client.call(
                        THREAD_TURN_INTERRUPT, ThreadTurnInterruptCommand(thread_id=uuid4())
                    )
                )
                await asyncio.wait_for(started.wait(), timeout=5)
                await server.stop()
                answer = await asyncio.wait_for(waiting, timeout=5)
        finally:
            held.set()
            await server.stop()

        assert answer == CONNECTION_LOST

    asyncio.run(scenario())


def test_a_rejected_token_stops_the_reconnect_loop(tmp_path: Path) -> None:
    async def scenario() -> None:
        upgrades: list[str] = []

        @web.middleware
        async def count(request: web.Request, handler: Handler) -> web.StreamResponse:
            upgrades.append(request.path)
            return await handler(request)

        server = RestartableServer(dispatcher_at(tmp_path), count)
        await server.start()
        try:
            async with contract_client(url(server.address), ControlToken("wrong")) as client:
                refused = await asyncio.wait_for(
                    client.call(THREAD_LIST, ThreadListCommand()), timeout=5
                )
                await asyncio.sleep(FIRST_BACKOFF * 2)
                again = await asyncio.wait_for(
                    client.call(THREAD_LIST, ThreadListCommand()), timeout=5
                )
        finally:
            await server.stop()

        assert refused == AUTHENTICATION_FAILED
        assert again == AUTHENTICATION_FAILED
        assert upgrades == ["/ws"]

    asyncio.run(scenario())


def test_the_repl_connects_to_a_contract_server_with_the_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        dispatcher = dispatcher_at(tmp_path)
        monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
        monkeypatch.setattr("sys.stdin", StringIO(""))
        async with served(instance, dispatcher) as address:
            exit_code = await asyncio.to_thread(main, ["repl", "--connect", url(address)])
            listed = await dispatcher.dispatch("thread.list", {}, set(Scope))

        assert exit_code == 0
        assert isinstance(listed, ThreadListResult)
        assert len(listed.threads) == 1

    asyncio.run(scenario())


def test_the_repl_refuses_an_instance_directory_beside_connect(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["repl", str(tmp_path), "--connect", "http://127.0.0.1:1/ws"])

    assert exit_code == 1
    assert "--connect" in capsys.readouterr().err


@asynccontextmanager
async def answering(body: str) -> AsyncIterator[Serve]:
    """A socket that answers every client frame with one text payload and stays open."""

    async def handler(request: web.Request) -> web.WebSocketResponse:
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        async for message in socket:
            if message.type is web.WSMsgType.TEXT:
                await socket.send_str(body)
        return socket

    application = web.Application()
    application.router.add_get("/ws", handler)
    runner = web.AppRunner(application, shutdown_timeout=0.1)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield Serve("127.0.0.1", site.port)
    finally:
        await runner.cleanup()


def test_an_unreadable_frame_fails_the_call_instead_of_waiting() -> None:
    async def scenario() -> None:
        async with (
            answering("not a frame") as address,
            contract_client(url(address), TOKEN) as client,
        ):
            listed = client.call(THREAD_LIST, ThreadListCommand())
            answer = await asyncio.wait_for(listed, timeout=5)

        assert answer == UNREADABLE

    asyncio.run(scenario())


def test_an_unreadable_frame_fails_a_subscription_that_never_opened() -> None:
    async def scenario() -> None:
        async with (
            answering("not a frame") as address,
            contract_client(url(address), TOKEN) as client,
        ):
            command = ThreadSubscribeCommand(thread_id=uuid4())
            answer = await asyncio.wait_for(client.subscribe(THREAD_SUBSCRIBE, command), timeout=5)

        assert answer == UNREADABLE

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "remote",
    [
        "http://example.com/ws",
        "ws://8.8.8.8/ws",
        "http://192.168.1.10:8787/ws",
    ],
)
def test_a_plaintext_remote_url_does_not_open_a_client(remote: str) -> None:
    async def scenario() -> None:
        with pytest.raises(InsecureContractUrl, match="plaintext"):
            async with contract_client(remote, TOKEN):
                pass

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "allowed",
    [
        "http://127.0.0.1:8787/ws",
        "http://localhost/ws",
        "ws://[::1]/ws",
        "https://example.com/ws",
        "wss://hub.example.com/instances/1/ws",
    ],
)
def test_loopback_and_tls_urls_keep_the_control_token_on_a_safe_path(allowed: str) -> None:
    assert protected_contract_url(allowed) == allowed


def test_the_repl_refuses_a_plaintext_remote_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
    exit_code = main(["repl", "--connect", "http://example.com/ws"])

    assert exit_code == 1
    assert "plaintext" in capsys.readouterr().err
