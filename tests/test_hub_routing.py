import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import aiohttp
import pytest
from aiohttp import web
from dotenv import dotenv_values

from kinby.cli import main
from kinby.contracts import ControlToken, ErrorCode, FrameType, SignalReceived
from kinby.core.contract_server import CONTROL_TOKEN_VARIABLE, ContractServer
from kinby.core.dispatcher import Dispatcher, build_dispatcher
from kinby.core.events import EventLog
from kinby.core.receiver import Receiver
from kinby.core.scheduler import Scheduler
from kinby.hub import Hub
from kinby.instance import Instance, Serve
from tests.test_hub import FakeRuntime
from tests.test_hub_server import call, created_instance, finished, frame, hub_at, served, url
from tests.test_routines import instance_at, routine_file
from tests.test_scheduler import FakeClock, runtime


@asynccontextmanager
async def instance_served(dispatcher: Dispatcher, token: ControlToken) -> AsyncIterator[Serve]:
    """Run one instance's own contract server, as private as it is behind a hub."""
    application = web.Application()
    ContractServer(dispatcher, token).add_routes(application)
    runner = web.AppRunner(application)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        yield Serve("127.0.0.1", site.port)
    finally:
        await runner.cleanup()


def control_token(hub: Hub, instance_id: str) -> ControlToken:
    values = dotenv_values(hub.instances_directory / instance_id / ".env", interpolate=False)
    token = values[CONTROL_TOKEN_VARIABLE]
    assert token is not None
    return ControlToken(token)


async def running_instance(
    hub: Hub,
    session: aiohttp.ClientSession,
    address: Serve,
) -> str:
    """Create an instance through the hub's contract and bring it to running."""
    async with session.ws_connect(url(address, "/ws")) as socket:
        instance_id = await created_instance(socket)
        await call(socket, "instance.start", instance_id=instance_id)
        accepted = await frame(socket)
        assert isinstance(accepted["result"], dict), accepted
        await finished(socket, str(accepted["result"]["operation_id"]))
    return instance_id


def test_the_hub_relays_a_client_to_the_instances_private_contract(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime)
        token = hub.access.issue()
        assert token is not None
        headers = {"Authorization": f"Bearer {token}"}
        async with served(hub) as address, aiohttp.ClientSession(headers=headers) as session:
            instance_id = await running_instance(hub, session, address)
            async with instance_served(
                build_dispatcher(tmp_path / "state"),
                control_token(hub, instance_id),
            ) as private:
                runtime.addresses[instance_id] = f"http://{private.host}:{private.port}"
                async with session.ws_connect(
                    url(address, f"/instances/{instance_id}/ws")
                ) as relayed:
                    await call(relayed, "thread.create", title="Relayed")
                    created = await frame(relayed)
                    await call(relayed, "thread.list")
                    listed = await frame(relayed)

        assert created["type"] == FrameType.RESULT.value
        assert isinstance(created["result"], dict)
        assert isinstance(listed["result"], dict)
        assert [thread["title"] for thread in listed["result"]["threads"]] == ["Relayed"]

    asyncio.run(scenario())


def test_the_relay_never_carries_lifecycle_authority_into_an_instance(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime)
        token = hub.access.issue()
        assert token is not None
        headers = {"Authorization": f"Bearer {token}"}
        async with served(hub) as address, aiohttp.ClientSession(headers=headers) as session:
            instance_id = await running_instance(hub, session, address)
            secret = control_token(hub, instance_id)
            async with instance_served(build_dispatcher(tmp_path / "state"), secret) as private:
                runtime.addresses[instance_id] = f"http://{private.host}:{private.port}"
                async with session.ws_connect(
                    url(address, f"/instances/{instance_id}/ws")
                ) as relayed:
                    await call(relayed, "instance.probe")
                    refused = await frame(relayed)
                control = await session.get(url(address, f"/instances/{instance_id}/control"))
                async with session.ws_connect(
                    f"http://{private.host}:{private.port}/control",
                    headers={"Authorization": f"Bearer {secret}"},
                ) as private_socket:
                    await call(private_socket, "instance.probe")
                    probed = await frame(private_socket)

        assert refused["type"] == FrameType.ERROR.value
        assert isinstance(refused["error"], dict)
        assert refused["error"]["code"] == ErrorCode.PERMISSION_DENIED.value
        assert control.status == 404
        assert probed["type"] == FrameType.RESULT.value

    asyncio.run(scenario())


def test_a_stopped_instance_is_unavailable_and_an_unknown_one_is_missing(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime)
        token = hub.access.issue()
        assert token is not None
        headers = {"Authorization": f"Bearer {token}"}
        async with served(hub) as address, aiohttp.ClientSession(headers=headers) as session:
            async with session.ws_connect(url(address, "/ws")) as socket:
                instance_id = await created_instance(socket)
            with pytest.raises(aiohttp.WSServerHandshakeError) as stopped:
                async with session.ws_connect(url(address, f"/instances/{instance_id}/ws")):
                    pass
            with pytest.raises(aiohttp.WSServerHandshakeError) as unknown:
                async with session.ws_connect(url(address, f"/instances/{uuid4()}/ws")):
                    pass
            stopped_signal = await session.post(
                url(address, f"/instances/{instance_id}/signals/news"),
                data=b"{}",
            )
            unknown_signal = await session.post(
                url(address, f"/instances/{uuid4()}/signals/news"),
                data=b"{}",
            )

        assert stopped.value.status == 503
        assert unknown.value.status == 404
        assert stopped_signal.status == 503
        assert unknown_signal.status == 404

    asyncio.run(scenario())


SIGNED_BODY = b'{"action":"opened"}'
SIGNATURE = "sha256=d42142b53efbc7cf5cd20b6e074eb33707e0de3b368f698e6d6f6c824ffb8d37"


@asynccontextmanager
async def instance_receiving(instance: Instance, scheduler: Scheduler) -> AsyncIterator[Serve]:
    receiver = Receiver(Serve("127.0.0.1", 0), scheduler, instance)
    address = await receiver.start()
    try:
        yield address
    finally:
        await receiver.stop()


def test_a_forwarded_webhook_keeps_its_signature_and_only_the_instance_answers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("SIGNAL_SECRET", "secret")
        coder = tmp_path / "coder"
        coder.mkdir()
        instance = instance_at(coder)
        routine_file(
            instance,
            """description: Issues
signal:
  auth: hmac-sha256
  secret: SIGNAL_SECRET
  signature_header: X-Hub-Signature-256
  delivery_header: X-GitHub-Delivery""",
        )
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 18, tzinfo=UTC)))
        hub_runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", hub_runtime)
        token = hub.access.issue()
        assert token is not None
        headers = {"Authorization": f"Bearer {token}"}
        async with served(hub) as address, aiohttp.ClientSession(headers=headers) as session:
            instance_id = await running_instance(hub, session, address)
            async with instance_receiving(instance, dispatcher.scheduler) as private:
                hub_runtime.addresses[instance_id] = f"http://{private.host}:{private.port}"
                async with aiohttp.ClientSession() as sender:
                    signed = await sender.post(
                        url(address, f"/instances/{instance_id}/signals/news"),
                        data=SIGNED_BODY,
                        headers={
                            "Content-Type": "application/json; charset=utf-8",
                            "X-GitHub-Delivery": "delivery-1",
                            "X-Hub-Signature-256": SIGNATURE,
                        },
                    )
                    accepted = await signed.json()
                    tampered = await sender.post(
                        url(address, f"/instances/{instance_id}/signals/news"),
                        data=SIGNED_BODY + b" ",
                        headers={"X-Hub-Signature-256": SIGNATURE},
                    )
        await dispatcher.scheduler.tick()
        await dispatcher.scheduler.drain()

        assert signed.status == 202
        assert UUID(accepted["thread_id"])
        assert tampered.status == 401
        events = list(EventLog(instance.manifest.state_dir).all_events())
        received = [event.payload for event in events if isinstance(event.payload, SignalReceived)]
        assert len(received) == 1
        assert received[0].delivery.body == SIGNED_BODY.decode()
        assert received[0].delivery.delivery_id == "delivery-1"

    asyncio.run(scenario())


@asynccontextmanager
async def recording_instance(received: list[web.Request]) -> AsyncIterator[Serve]:
    """Stand in for an instance's receiver, so a test can read exactly what arrived."""

    async def receive(request: web.Request) -> web.Response:
        await request.read()
        received.append(request)
        return web.json_response({"thread_id": "recorded", "sequence": 7}, status=202)

    application = web.Application()
    # A catch-all, so a test can see any path the hub forwards to, not only the right one.
    application.router.add_route("*", "/{tail:.*}", receive)
    runner = web.AppRunner(application)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        yield Serve("127.0.0.1", site.port)
    finally:
        await runner.cleanup()


def test_forwarding_keeps_the_request_bytes_and_leaves_the_hub_session_behind(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        hub_runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", hub_runtime)
        token = hub.access.issue()
        assert token is not None
        headers = {"Authorization": f"Bearer {token}"}
        received: list[web.Request] = []
        async with served(hub) as address, aiohttp.ClientSession(headers=headers) as session:
            instance_id = await running_instance(hub, session, address)
            async with recording_instance(received) as private:
                hub_runtime.addresses[instance_id] = f"http://{private.host}:{private.port}"
                async with aiohttp.ClientSession() as sender:
                    answer = await sender.post(
                        url(address, f"/instances/{instance_id}/signals/news"),
                        data=b"\x00binary\xffbody",
                        headers={
                            "Authorization": "Bearer signal-secret",
                            "Cookie": "kinby_session=stolen",
                            "X-Hub-Signature-256": SIGNATURE,
                        },
                    )
                    body = await answer.read()

        assert len(received) == 1
        forwarded = received[0]
        assert forwarded.path == "/signals/news"
        assert await forwarded.read() == b"\x00binary\xffbody"
        assert forwarded.headers["Authorization"] == "Bearer signal-secret"
        assert forwarded.headers["X-Hub-Signature-256"] == SIGNATURE
        assert "Cookie" not in forwarded.headers
        assert forwarded.headers["Host"] == f"{private.host}:{private.port}"
        assert answer.status == 202
        assert json.loads(body) == {"thread_id": "recorded", "sequence": 7}

    asyncio.run(scenario())


def test_the_public_signal_path_reaches_the_instance_adoption_pointed_it_at(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        hub_runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", hub_runtime)
        token = hub.access.issue()
        assert token is not None
        headers = {"Authorization": f"Bearer {token}"}
        received: list[web.Request] = []
        async with served(hub) as address, aiohttp.ClientSession(headers=headers) as session:
            instance_id = await running_instance(hub, session, address)
            async with recording_instance(received) as private:
                hub_runtime.addresses[instance_id] = f"http://{private.host}:{private.port}"
                async with aiohttp.ClientSession() as sender:
                    unclaimed = await sender.post(url(address, "/signals/news"), data=b"{}")
                    claimed = main(["hub", str(tmp_path / "hub"), "signals", instance_id])
                    adopted = await sender.post(url(address, "/signals/news"), data=b"{}")

        assert unclaimed.status == 404
        assert claimed == 0
        assert adopted.status == 202
        assert [request.path for request in received] == ["/signals/news"]

    asyncio.run(scenario())


def test_the_signal_alias_refuses_an_instance_the_hub_does_not_manage(tmp_path: Path) -> None:
    hub_at(tmp_path / "hub")

    exit_code = main(["hub", str(tmp_path / "hub"), "signals", str(uuid4())])

    assert exit_code == 1


def test_an_instance_keeps_serving_while_its_hub_is_stopped(tmp_path: Path) -> None:
    async def scenario() -> None:
        hub_runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", hub_runtime)
        token = hub.access.issue()
        assert token is not None
        headers = {"Authorization": f"Bearer {token}"}
        dispatcher = build_dispatcher(tmp_path / "state")
        async with aiohttp.ClientSession(headers=headers) as session:
            async with served(hub) as address:
                instance_id = await running_instance(hub, session, address)
                secret = control_token(hub, instance_id)
            async with instance_served(dispatcher, secret) as private:
                hub_runtime.addresses[instance_id] = f"http://{private.host}:{private.port}"
                with pytest.raises(aiohttp.ClientConnectorError):
                    await session.ws_connect(url(address, f"/instances/{instance_id}/ws"))
                async with session.ws_connect(
                    f"http://{private.host}:{private.port}/ws",
                    headers={"Authorization": f"Bearer {secret}"},
                ) as direct:
                    await call(direct, "thread.create", title="During the outage")
                    await frame(direct)
                async with (
                    served(hub) as restarted,
                    session.ws_connect(url(restarted, f"/instances/{instance_id}/ws")) as relayed,
                ):
                    await call(relayed, "thread.list")
                    after = await frame(relayed)

        assert isinstance(after["result"], dict)
        assert [thread["title"] for thread in after["result"]["threads"]] == ["During the outage"]

    asyncio.run(scenario())


def test_a_routine_name_cannot_walk_out_of_the_signal_path(tmp_path: Path) -> None:
    async def scenario() -> None:
        hub_runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", hub_runtime)
        token = hub.access.issue()
        assert token is not None
        headers = {"Authorization": f"Bearer {token}"}
        received: list[web.Request] = []
        async with served(hub) as address, aiohttp.ClientSession(headers=headers) as session:
            instance_id = await running_instance(hub, session, address)
            async with recording_instance(received) as private:
                hub_runtime.addresses[instance_id] = f"http://{private.host}:{private.port}"
                async with aiohttp.ClientSession() as sender:
                    dotted = await sender.post(
                        url(address, f"/instances/{instance_id}/signals/%2E%2E"),
                        data=b"{}",
                    )
                    slashed = await sender.post(
                        url(address, f"/instances/{instance_id}/signals/..%2Fcontrol"),
                        data=b"{}",
                    )

        # A name the hub's own router resolves away never becomes a forward at all; one it
        # keeps stays a single segment under /signals, so it reaches no other instance route.
        assert dotted.status == 404
        assert [request.rel_url.raw_path for request in received] == ["/signals/..%2Fcontrol"]
        assert slashed.status == 202

    asyncio.run(scenario())
