import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import aiohttp
import pytest

from kinby.contracts import (
    CONTRACT_VERSION,
    Capability,
    ControlToken,
    ErrorCode,
    Event,
    EventType,
    FrameType,
    MessageDelta,
    Payload,
    Scope,
    ThreadCreateResult,
    TurnCompleted,
    TurnStarted,
    is_turn_closing,
)
from kinby.core.contract_server import (
    CONTROL_TOKEN_VARIABLE,
    SUBSCRIPTION_QUEUE_LIMIT,
    ContractServer,
)
from kinby.core.dispatcher import ScheduledDispatcher
from kinby.core.receiver import Receiver
from kinby.core.turns import TurnOutcome
from kinby.instance import Instance, Serve
from tests.test_routines import instance_at, routine_file
from tests.test_scheduler import FakeClock, ScriptedRunner, runtime
from tests.test_serve import BlockingRoutineRunner

TOKEN = ControlToken("control-token")
STARTED = TurnStarted(message="Hello", model="openai:gpt-5")
COMPLETED = TurnCompleted(input_tokens=1, output_tokens=1)


def dispatcher_at(path: Path, runner: ScriptedRunner | None = None) -> ScheduledDispatcher:
    return runtime(instance_at(path), FakeClock(datetime(2026, 9, 18, tzinfo=UTC)), runner)


@asynccontextmanager
async def served(
    instance: Instance,
    dispatcher: ScheduledDispatcher,
    *,
    contract: bool = True,
) -> AsyncIterator[Serve]:
    receiver = Receiver(
        Serve("127.0.0.1", 0),
        dispatcher.scheduler,
        instance,
        ContractServer(dispatcher, TOKEN) if contract else None,
    )
    address = await receiver.start()
    try:
        yield address
    finally:
        await receiver.stop()


@asynccontextmanager
async def connected(
    address: Serve,
    *,
    path: str = "/ws",
    token: str | None = TOKEN,
) -> AsyncIterator[aiohttp.ClientWebSocketResponse]:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    async with (
        aiohttp.ClientSession(headers=headers) as session,
        session.ws_connect(f"http://{address.host}:{address.port}{path}") as socket,
    ):
        yield socket


async def frame(socket: aiohttp.ClientWebSocketResponse) -> dict[str, object]:
    message = await asyncio.wait_for(socket.receive(), timeout=5)
    assert message.type is aiohttp.WSMsgType.TEXT, message
    received: dict[str, object] = json.loads(message.data)
    return received


async def call(socket: aiohttp.ClientWebSocketResponse, method: str, **params: object) -> None:
    await socket.send_str(
        json.dumps({"type": "call", "id": "1", "method": method, "params": params})
    )


async def subscribe(socket: aiohttp.ClientWebSocketResponse, thread_id: UUID) -> None:
    await socket.send_str(
        json.dumps(
            {
                "type": "subscribe",
                "id": "1",
                "method": "thread.subscribe",
                "params": {"thread_id": str(thread_id)},
            }
        )
    )


async def health(address: Serve) -> dict[str, object]:
    async with (
        aiohttp.ClientSession() as session,
        session.get(f"http://{address.host}:{address.port}/health") as response,
    ):
        body: dict[str, object] = await response.json()
        return body


async def created_thread(dispatcher: ScheduledDispatcher) -> ThreadCreateResult:
    thread = await dispatcher.dispatch("thread.create", {}, set(Scope))
    assert isinstance(thread, ThreadCreateResult)
    return thread


def store_history(state_dir: Path, thread_id: UUID, payloads: list[Payload]) -> None:
    """Write a thread's history in one pass: appending event by event is quadratic."""
    turn_id = uuid4()
    records = [
        Event(
            sequence=sequence,
            thread_id=thread_id,
            turn_id=turn_id,
            payload=payload,
            timestamp=datetime(2026, 9, 18, tzinfo=UTC),
        ).model_dump_json()
        for sequence, payload in enumerate(payloads, start=1)
    ]
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "events.jsonl").write_text("".join(f"{record}\n" for record in records))


def test_a_call_returns_its_result_and_a_wrong_token_never_upgrades(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        async with served(instance, dispatcher_at(tmp_path)) as address:
            async with connected(address) as socket:
                await call(socket, "thread.create", title="Launch notes")
                result = await frame(socket)

            with pytest.raises(aiohttp.WSServerHandshakeError) as wrong:
                async with connected(address, token="wrong"):
                    pass
            with pytest.raises(aiohttp.WSServerHandshakeError) as missing:
                async with connected(address, token=None):
                    pass

        assert result["type"] == FrameType.RESULT.value
        assert result["id"] == "1"
        assert isinstance(result["result"], dict)
        assert UUID(str(result["result"]["id"]))
        assert wrong.value.status == 401
        assert missing.value.status == 401

    asyncio.run(scenario())


def test_the_control_route_alone_grants_the_lifecycle_scope(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        async with served(instance, dispatcher_at(tmp_path)) as address:
            async with connected(address) as socket:
                await call(socket, "instance.probe")
                denied = await frame(socket)
            async with connected(address, path="/control") as socket:
                await call(socket, "instance.probe")
                granted = await frame(socket)

        assert denied["type"] == FrameType.ERROR.value
        assert denied["error"] == {
            "code": ErrorCode.PERMISSION_DENIED.value,
            "message": 'Missing required scope "instance:lifecycle".',
            "retryable": False,
        }
        assert granted["result"] == {
            "contract_version": CONTRACT_VERSION,
            "capabilities": [Capability.WS.value],
        }

    asyncio.run(scenario())


def test_a_malformed_frame_is_answered_and_the_connection_stays_open(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        async with (
            served(instance, dispatcher_at(tmp_path)) as address,
            connected(address) as socket,
        ):
            await socket.send_str("not a frame")
            unreadable = await frame(socket)
            await socket.send_str(json.dumps({"type": "call", "id": "2"}))
            incomplete = await frame(socket)
            await call(socket, "thread.list")
            after = await frame(socket)

        assert unreadable["type"] == FrameType.ERROR.value
        assert unreadable["id"] is None
        assert isinstance(unreadable["error"], dict)
        assert unreadable["error"]["code"] == ErrorCode.INVALID_ARGUMENT.value
        assert isinstance(incomplete["error"], dict)
        assert incomplete["error"]["code"] == ErrorCode.INVALID_ARGUMENT.value
        assert after["type"] == FrameType.RESULT.value
        assert after["result"] == {"threads": []}

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
            connected(address) as socket,
        ):
            await subscribe(socket, thread.id)
            subscribed = await frame(socket)
            replayed = [await frame(socket) for _ in range(3)]
            await dispatcher.dispatch(
                "thread.turn.start",
                {"thread_id": str(thread.id), "message": "Hello"},
                set(Scope),
            )
            live = [await frame(socket) for _ in range(3)]
            await socket.send_str(json.dumps({"type": "cancel", "id": "1"}))
            ended = await frame(socket)

        assert subscribed == {"type": FrameType.SUBSCRIBED.value, "id": "1", "head_sequence": 3}
        assert [_event(item).sequence for item in replayed] == [1, 2, 3]
        assert [_event(item).sequence for item in live] == [4, 5, 6]
        assert [_event(item).type for item in live] == [
            EventType.TURN_STARTED,
            EventType.MESSAGE_DELTA,
            EventType.TURN_COMPLETED,
        ]
        assert ended == {"type": FrameType.END.value, "id": "1"}

    asyncio.run(scenario())


def test_a_subscription_past_its_queue_limit_ends_with_a_retryable_error(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        thread_id = uuid4()
        store_history(
            instance.manifest.state_dir,
            thread_id,
            [STARTED, *[MessageDelta(text="More") for _ in range(SUBSCRIPTION_QUEUE_LIMIT)]],
        )
        async with (
            served(instance, dispatcher_at(tmp_path)) as address,
            connected(address) as socket,
        ):
            await subscribe(socket, thread_id)
            subscribed = await frame(socket)
            received = await frame(socket)
            items = 0
            while received["type"] == FrameType.ITEM.value:
                items += 1
                received = await frame(socket)
            ended = await frame(socket)

        assert subscribed["head_sequence"] == SUBSCRIPTION_QUEUE_LIMIT + 1
        assert items == SUBSCRIPTION_QUEUE_LIMIT
        assert received["type"] == FrameType.ERROR.value
        assert isinstance(received["error"], dict)
        assert received["error"]["code"] == ErrorCode.RESOURCE_EXHAUSTED.value
        assert received["error"]["retryable"] is True
        assert ended == {"type": FrameType.END.value, "id": "1"}

    asyncio.run(scenario())


class BlockFirstRunner(BlockingRoutineRunner):
    """Hold only the first turn, so a later routine fire can finish."""

    async def run(self, turn: object, context: object) -> TurnOutcome:
        if self.started.is_set():
            self.runs += 1
            return TurnOutcome()
        return await super().run(turn, context)


def test_a_waiting_call_still_lets_the_socket_interrupt_the_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        runner = BlockFirstRunner()
        dispatcher = dispatcher_at(tmp_path, runner)
        thread = await created_thread(dispatcher)
        async with served(instance, dispatcher) as address, connected(address) as socket:
            await call(socket, "thread.turn.start", thread_id=str(thread.id), message="Hello")
            started = await frame(socket)
            await asyncio.to_thread(runner.started.wait, 5)
            await socket.send_str(
                json.dumps(
                    {
                        "type": "call",
                        "id": "2",
                        "method": "routine.run",
                        "params": {
                            "name": "news",
                            "payload": {"body": "later", "content_type": "text/plain"},
                        },
                    }
                )
            )
            await socket.send_str(
                json.dumps(
                    {
                        "type": "call",
                        "id": "3",
                        "method": "thread.turn.interrupt",
                        "params": {"thread_id": str(thread.id)},
                    }
                )
            )
            answers = {
                received["id"]: received for received in [await frame(socket) for _ in range(2)]
            }

        assert started["type"] == FrameType.RESULT.value
        assert answers["3"]["type"] == FrameType.RESULT.value
        assert answers["2"]["type"] == FrameType.RESULT.value
        assert runner.cancelled.is_set() is True

    asyncio.run(scenario())


def test_a_turn_survives_the_client_that_started_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        runner = BlockingRoutineRunner()
        dispatcher = dispatcher_at(tmp_path, runner)
        thread = await created_thread(dispatcher)
        async with served(instance, dispatcher) as address:
            async with connected(address) as socket:
                await call(socket, "thread.turn.start", thread_id=str(thread.id), message="Hello")
                accepted = await frame(socket)
                await asyncio.to_thread(runner.started.wait, 5)
            runner.release()
            async with connected(address) as socket:
                await subscribe(socket, thread.id)
                await frame(socket)
                closing = await _closing_event(socket)

        assert accepted["type"] == FrameType.RESULT.value
        assert runner.cancelled.is_set() is False
        assert isinstance(closing.payload, TurnCompleted)

    asyncio.run(scenario())


def test_health_reports_the_contract_version_and_what_the_instance_can_do(tmp_path: Path) -> None:
    async def scenario() -> tuple[dict[str, object], dict[str, object]]:
        instance = instance_at(tmp_path)
        async with served(instance, dispatcher_at(tmp_path)) as address:
            serving = await health(address)
        async with served(instance, dispatcher_at(tmp_path), contract=False) as address:
            off = await health(address)
        return serving, off

    serving, off = asyncio.run(scenario())

    assert serving == {
        "id": "test",
        "contract_version": CONTRACT_VERSION,
        "capabilities": [Capability.WS.value],
    }
    assert off == {"id": "test", "contract_version": CONTRACT_VERSION, "capabilities": []}


def test_an_instance_without_a_control_token_leaves_the_contract_server_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv(CONTROL_TOKEN_VARIABLE, raising=False)

    with caplog.at_level(logging.INFO):
        server = ContractServer.from_environment(dispatcher_at(tmp_path))

    assert server is None
    assert CONTROL_TOKEN_VARIABLE in caplog.text


def test_the_contract_server_reads_its_token_from_the_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(CONTROL_TOKEN_VARIABLE, "from-the-environment")

    async def scenario() -> tuple[bool, int]:
        instance = instance_at(tmp_path)
        dispatcher = dispatcher_at(tmp_path)
        server = ContractServer.from_environment(dispatcher)
        assert server is not None
        receiver = Receiver(Serve("127.0.0.1", 0), dispatcher.scheduler, instance, server)
        address = await receiver.start()
        try:
            async with connected(address, token="from-the-environment") as socket:
                await call(socket, "thread.list")
                answered = await frame(socket)
            with pytest.raises(aiohttp.WSServerHandshakeError) as rejected:
                async with connected(address, token=str(TOKEN)):
                    pass
        finally:
            await receiver.stop()
        return answered["type"] == FrameType.RESULT.value, rejected.value.status

    answered, status = asyncio.run(scenario())

    assert answered is True
    assert status == 401


def _event(received: dict[str, object]) -> Event:
    assert received["type"] == FrameType.ITEM.value, received
    return Event.model_validate(received["item"])


async def _closing_event(socket: aiohttp.ClientWebSocketResponse) -> Event:
    while True:
        event = _event(await frame(socket))
        if is_turn_closing(event.payload):
            return event
