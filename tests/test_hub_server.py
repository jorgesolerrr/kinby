import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http.cookies import SimpleCookie
from pathlib import Path
from uuid import uuid4

import aiohttp
import pytest

from kinby.contracts import (
    CONTRACT_VERSION,
    INSTANCE_ADOPT,
    INSTANCE_ADOPT_PREVIEW,
    INSTANCE_CREATE,
    INSTANCE_DELETE,
    INSTANCE_DELETE_PREVIEW,
    INSTANCE_LIST,
    INSTANCE_LOGS,
    INSTANCE_RECREATE,
    INSTANCE_REMOVE,
    INSTANCE_RESTORE,
    INSTANCE_SECRETS_SET,
    INSTANCE_START,
    INSTANCE_STATUS,
    INSTANCE_STOP,
    AccessToken,
    FrameType,
)
from kinby.hub import Hub, HubAccess, HubContractServer, HubRegistry
from kinby.hub.access import SessionId
from kinby.instance import Serve
from tests.test_hub import FakeImages, FakeRuntime, HeldRuntime


def hub_at(directory: Path, runtime: FakeRuntime | None = None) -> Hub:
    return Hub(directory, runtime=runtime or FakeRuntime(), images=FakeImages())


@asynccontextmanager
async def served(hub: Hub, *, web_app: Path | None = None) -> AsyncIterator[Serve]:
    server = HubContractServer(hub.dispatcher, hub.access, hub, web_app)
    address = await server.start(Serve("127.0.0.1", 0))
    try:
        yield address
    finally:
        await server.stop()


def url(address: Serve, path: str) -> str:
    return f"http://{address.host}:{address.port}{path}"


async def login(
    session: aiohttp.ClientSession,
    address: Serve,
    token: AccessToken,
) -> aiohttp.ClientResponse:
    return await session.post(url(address, "/auth/login"), json={"token": token})


async def session_cookie(
    session: aiohttp.ClientSession,
    address: Serve,
    token: AccessToken,
) -> SimpleCookie:
    """Read the session cookie from its Set-Cookie header: the jar drops cookies from IP hosts."""
    response = await login(session, address, token)
    assert response.status == 200, await response.text()
    cookie = SimpleCookie()
    cookie.load(response.headers["Set-Cookie"])
    return cookie


async def frame(socket: aiohttp.ClientWebSocketResponse) -> dict[str, object]:
    message = await asyncio.wait_for(socket.receive(), timeout=5)
    assert message.type is aiohttp.WSMsgType.TEXT, message
    received: dict[str, object] = json.loads(message.data)
    return received


async def call(socket: aiohttp.ClientWebSocketResponse, method: str, **params: object) -> None:
    await socket.send_str(
        json.dumps({"type": "call", "id": "1", "method": method, "params": params})
    )


def test_a_session_cookie_upgrades_the_socket_and_carries_a_hub_method(tmp_path: Path) -> None:
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub")
        token = hub.access.issue()
        assert token is not None
        async with served(hub) as address, aiohttp.ClientSession() as session:
            response = await login(session, address, token)
            body = await response.json()
            cookie = SimpleCookie()
            cookie.load(response.headers["Set-Cookie"])
            morsel = cookie["kinby_session"]
            async with session.ws_connect(
                url(address, "/ws"),
                headers={
                    "Cookie": f"kinby_session={morsel.value}",
                    "Origin": f"http://{address.host}:{address.port}",
                },
            ) as socket:
                await call(socket, "instance.list")
                listed = await frame(socket)

        assert response.status == 200
        assert body == {"contract_version": CONTRACT_VERSION}
        assert morsel["httponly"] is True
        assert not morsel["secure"]
        assert morsel["samesite"] == "Strict"
        assert morsel["path"] == "/"
        assert listed["type"] == FrameType.RESULT.value
        assert listed["result"] == {"instances": []}

    asyncio.run(scenario())


def test_the_session_cookie_is_secure_when_the_request_is_https(tmp_path: Path) -> None:
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub")
        token = hub.access.issue()
        assert token is not None
        async with served(hub) as address, aiohttp.ClientSession() as session:
            response = await session.post(
                url(address, "/auth/login"),
                json={"token": token},
                headers={"X-Forwarded-Proto": "https"},
            )
            cookie = SimpleCookie()
            cookie.load(response.headers["Set-Cookie"])

        assert response.status == 200
        assert cookie["kinby_session"]["secure"] is True

    asyncio.run(scenario())


def test_the_hub_issues_one_access_token_and_stores_only_its_hash(tmp_path: Path) -> None:
    hub = hub_at(tmp_path / "hub")
    token = hub.access.issue()
    hub.close()

    assert token is not None
    assert hub_at(tmp_path / "hub").access.issue() is None
    assert hub.access.accepts(token) is True
    stored = (tmp_path / "hub" / "registry.sqlite").read_bytes().decode("utf-8", errors="ignore")
    assert token not in stored


def test_a_cookie_from_another_origin_never_upgrades(tmp_path: Path) -> None:
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub")
        token = hub.access.issue()
        assert token is not None
        async with served(hub) as address, aiohttp.ClientSession() as session:
            cookie = await session_cookie(session, address, token)
            value = cookie["kinby_session"].value
            with pytest.raises(aiohttp.WSServerHandshakeError) as elsewhere:
                async with session.ws_connect(
                    url(address, "/ws"),
                    headers={
                        "Cookie": f"kinby_session={value}",
                        "Origin": "https://attacker.example",
                    },
                ):
                    pass
            with pytest.raises(aiohttp.WSServerHandshakeError) as without_origin:
                async with session.ws_connect(
                    url(address, "/ws"),
                    headers={"Cookie": f"kinby_session={value}"},
                ):
                    pass
            with pytest.raises(aiohttp.WSServerHandshakeError) as unauthenticated:
                async with session.ws_connect(url(address, "/ws")):
                    pass

        assert elsewhere.value.status == 401
        assert without_origin.value.status == 401
        assert unauthenticated.value.status == 401

    asyncio.run(scenario())


def test_a_bearer_token_upgrades_the_socket_without_a_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub")
        token = hub.access.issue()
        assert token is not None
        async with served(hub) as address, aiohttp.ClientSession() as session:
            async with session.ws_connect(
                url(address, "/ws"),
                headers={"Authorization": f"Bearer {token}"},
            ) as socket:
                await call(socket, "instance.list")
                listed = await frame(socket)
            with pytest.raises(aiohttp.WSServerHandshakeError) as wrong:
                async with session.ws_connect(
                    url(address, "/ws"),
                    headers={"Authorization": "Bearer wrong-token"},
                ):
                    pass

        assert listed["result"] == {"instances": []}
        assert wrong.value.status == 401

    asyncio.run(scenario())


def test_rotating_the_access_token_ends_open_sessions(tmp_path: Path) -> None:
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub")
        token = hub.access.issue()
        assert token is not None
        async with served(hub) as address, aiohttp.ClientSession() as session:
            cookie = await session_cookie(session, address, token)
            rotated = hub.access.rotate()
            with pytest.raises(aiohttp.WSServerHandshakeError) as ended:
                async with session.ws_connect(
                    url(address, "/ws"),
                    headers={
                        "Cookie": f"kinby_session={cookie['kinby_session'].value}",
                        "Origin": f"http://{address.host}:{address.port}",
                    },
                ):
                    pass
            replaced = await login(session, address, token)
            async with session.ws_connect(
                url(address, "/ws"),
                headers={"Authorization": f"Bearer {rotated}"},
            ) as socket:
                await call(socket, "instance.list")
                listed = await frame(socket)

        assert ended.value.status == 401
        assert replaced.status == 401
        assert listed["type"] == FrameType.RESULT.value

    asyncio.run(scenario())


def test_a_session_survives_a_hub_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub")
        token = hub.access.issue()
        assert token is not None
        async with served(hub) as address, aiohttp.ClientSession() as session:
            cookie = await session_cookie(session, address, token)
        hub.close()
        restarted = hub_at(tmp_path / "hub")
        async with served(restarted) as address, aiohttp.ClientSession() as session:
            headers = {
                "Cookie": f"kinby_session={cookie['kinby_session'].value}",
                "Origin": f"http://{address.host}:{address.port}",
            }
            async with session.ws_connect(url(address, "/ws"), headers=headers) as socket:
                await call(socket, "instance.list")
                listed = await frame(socket)

        assert restarted.access.issue() is None
        assert listed["type"] == FrameType.RESULT.value

    asyncio.run(scenario())


async def created_instance(socket: aiohttp.ClientWebSocketResponse) -> str:
    await call(
        socket,
        "instance.create",
        manifest_id="alice",
        model="openai:gpt-5",
        revision="main",
    )
    accepted = await frame(socket)
    assert isinstance(accepted["result"], dict)
    instance_id = str(accepted["result"]["instance_id"])
    await finished(socket, str(accepted["result"]["operation_id"]))
    return instance_id


async def finished(socket: aiohttp.ClientWebSocketResponse, operation_id: str) -> str:
    for _ in range(200):
        await call(socket, "operation.get", operation_id=operation_id)
        reported = await frame(socket)
        assert isinstance(reported["result"], dict), reported
        state = str(reported["result"]["state"])
        if state in {"succeeded", "failed"}:
            assert state == "succeeded", reported["result"]["detail"]
            return state
        await asyncio.sleep(0.01)
    raise AssertionError("lifecycle operation did not finish")


def test_an_operation_keeps_running_after_its_client_disconnects(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = HeldRuntime()
        hub = hub_at(tmp_path / "hub", runtime)
        token = hub.access.issue()
        assert token is not None
        headers = {"Authorization": f"Bearer {token}"}
        async with served(hub) as address, aiohttp.ClientSession(headers=headers) as session:
            async with session.ws_connect(url(address, "/ws")) as socket:
                instance_id = await created_instance(socket)
                await call(socket, "instance.start", instance_id=instance_id)
                accepted = await frame(socket)
                assert isinstance(accepted["result"], dict)
                operation_id = str(accepted["result"]["operation_id"])
                await asyncio.wait_for(runtime.holding.wait(), timeout=5)
            runtime.released.set()
            async with session.ws_connect(url(address, "/ws")) as socket:
                state = await finished(socket, operation_id)

        assert state == "succeeded"
        assert runtime.started == [instance_id]

    asyncio.run(scenario())


def test_a_frame_is_logged_by_method_and_never_by_its_params(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub")
        token = hub.access.issue()
        assert token is not None
        headers = {"Authorization": f"Bearer {token}"}
        async with (
            served(hub) as address,
            aiohttp.ClientSession(headers=headers) as session,
            session.ws_connect(url(address, "/ws")) as socket,
        ):
            await call(
                socket,
                "instance.create",
                manifest_id="alice",
                model="openai:gpt-5",
                revision="main",
                secrets={"PROVIDER_TOKEN": "private-value"},
            )
            await frame(socket)

    with caplog.at_level(logging.INFO):
        asyncio.run(scenario())

    assert "Frame call id=1 method=instance.create" in caplog.text
    assert "private-value" not in caplog.text


def built_web_app(directory: Path) -> Path:
    (directory / "assets").mkdir(parents=True)
    (directory / "assets" / "app-1a2b.js").write_text("console.log('kinby')\n", encoding="utf-8")
    (directory / "index.html").write_text("<title>kinby</title>\n", encoding="utf-8")
    return directory


def test_the_web_app_serves_assets_and_falls_back_to_index(tmp_path: Path) -> None:
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub")
        token = hub.access.issue()
        assert token is not None
        web_app = built_web_app(tmp_path / "app")
        async with (
            served(hub, web_app=web_app) as address,
            aiohttp.ClientSession() as session,
        ):
            asset = await session.get(url(address, "/assets/app-1a2b.js"))
            asset_body = await asset.text()
            fallback = await session.get(url(address, "/instances/42"))
            fallback_body = await fallback.text()
            socket = await session.get(url(address, "/ws"))
            unknown_asset = await session.get(url(address, "/assets/missing.js"))
            login_response = await login(session, address, token)

        assert asset.status == 200
        assert asset_body == "console.log('kinby')\n"
        assert fallback.status == 200
        assert fallback_body == "<title>kinby</title>\n"
        assert fallback.headers["Cache-Control"] == "no-cache"
        assert fallback.content_type == "text/html"
        assert socket.status == 401
        assert unknown_asset.status == 404
        assert login_response.status == 200

    asyncio.run(scenario())


def test_a_hub_without_a_built_app_still_serves_the_contract(tmp_path: Path) -> None:
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub")
        token = hub.access.issue()
        assert token is not None
        async with served(hub) as address, aiohttp.ClientSession() as session:
            page = await session.get(url(address, "/instances/42"))
            async with session.ws_connect(
                url(address, "/ws"),
                headers={"Authorization": f"Bearer {token}"},
            ) as socket:
                await call(socket, "instance.list")
                listed = await frame(socket)

        assert page.status == 404
        assert listed["result"] == {"instances": []}

    asyncio.run(scenario())


@pytest.mark.parametrize("token", ["wrong-token", ""])
def test_a_failed_login_opens_no_session(tmp_path: Path, token: str) -> None:
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub")
        assert hub.access.issue() is not None
        async with served(hub) as address, aiohttp.ClientSession() as session:
            response = await login(session, address, AccessToken(token))
            body = await response.text()

        assert response.status == 401
        assert response.headers.get("Set-Cookie") is None
        assert "token" not in body.lower()

    asyncio.run(scenario())


def test_login_opens_a_session_only_for_the_current_token(tmp_path: Path) -> None:
    access = HubAccess(HubRegistry(tmp_path / "hub"))
    token = access.issue()
    assert token is not None

    session = access.login(token)

    assert session is not None
    assert access.session_open(session) is True
    assert access.login(AccessToken("wrong-token")) is None


def _login_versus_rotate(directory: Path) -> None:
    access = HubAccess(HubRegistry(directory))
    token = access.issue()
    assert token is not None
    barrier = threading.Barrier(2)
    opened: list[SessionId | None] = []

    def login_old() -> None:
        barrier.wait()
        opened.append(access.login(token))

    def rotate() -> None:
        barrier.wait()
        access.rotate()

    threads = [threading.Thread(target=login_old), threading.Thread(target=rotate)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    session = opened[0]
    assert access.accepts(token) is False
    if session is not None:
        assert access.session_open(session) is False


def test_rotation_closes_a_login_that_raced_it(tmp_path: Path) -> None:
    for index in range(40):
        _login_versus_rotate(tmp_path / "hub" / str(index))


def test_concurrent_first_starts_keep_one_access_token(tmp_path: Path) -> None:
    directory = tmp_path / "hub"
    HubRegistry(directory)
    issued: list[AccessToken | None] = []
    lock = threading.Lock()

    def issue() -> None:
        token = HubAccess(HubRegistry(directory)).issue()
        with lock:
            issued.append(token)

    threads = [threading.Thread(target=issue) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    kept = [token for token in issued if token is not None]
    assert len(kept) == 1
    assert HubAccess(HubRegistry(directory)).accepts(kept[0]) is True


def test_a_reconnecting_client_finds_the_operation_whose_answer_it_lost(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = HeldRuntime()
        hub = hub_at(tmp_path / "hub", runtime)
        token = hub.access.issue()
        assert token is not None
        headers = {"Authorization": f"Bearer {token}"}
        async with served(hub) as address, aiohttp.ClientSession(headers=headers) as session:
            async with session.ws_connect(url(address, "/ws")) as socket:
                instance_id = await created_instance(socket)
                await call(socket, "instance.start", instance_id=instance_id)
                accepted = await frame(socket)
                assert isinstance(accepted["result"], dict)
                lost = str(accepted["result"]["operation_id"])
                await asyncio.wait_for(runtime.holding.wait(), timeout=5)
            async with session.ws_connect(url(address, "/ws")) as reconnected:
                await call(reconnected, "instance.status", instance_id=instance_id)
                status = await frame(reconnected)
                assert isinstance(status["result"], dict)
                await call(
                    reconnected,
                    "operation.get",
                    operation_id=status["result"]["active_operation_id"],
                )
                running = await frame(reconnected)
                runtime.released.set()
                state = await finished(reconnected, lost)

        assert status["result"]["active_operation_id"] == lost
        assert isinstance(running["result"], dict)
        assert running["result"]["state"] == "running"
        assert [step["name"] for step in running["result"]["steps"]] == ["start"]
        assert state == "succeeded"
        assert runtime.started == [instance_id]

    asyncio.run(scenario())


#: Every hub method the update token must not reach.
NOT_AN_UPDATE = (
    INSTANCE_CREATE,
    INSTANCE_START,
    INSTANCE_STOP,
    INSTANCE_RECREATE,
    INSTANCE_REMOVE,
    INSTANCE_RESTORE,
    INSTANCE_DELETE_PREVIEW,
    INSTANCE_DELETE,
    INSTANCE_SECRETS_SET,
    INSTANCE_ADOPT_PREVIEW,
    INSTANCE_ADOPT,
    INSTANCE_LIST,
    INSTANCE_STATUS,
    INSTANCE_LOGS,
)


def test_an_update_token_runs_an_update_and_reads_its_operation_and_nothing_else(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub")
        assert hub.access.issue() is not None
        token = hub.access.rotate_update_token()
        headers = {"Authorization": f"Bearer {token}"}
        async with served(hub) as address, aiohttp.ClientSession() as session:
            async with session.ws_connect(
                url(address, "/ws"),
                headers={"Authorization": f"Bearer {hub.access.rotate()}"},
            ) as admin:
                instance_id = await created_instance(admin)
            async with session.ws_connect(url(address, "/ws"), headers=headers) as socket:
                await call(socket, "instance.update", instance_id=instance_id, revision="v0.2.0")
                accepted = await frame(socket)
                assert isinstance(accepted["result"], dict), accepted
                state = await finished(socket, str(accepted["result"]["operation_id"]))
                refused: dict[str, object] = {}
                for method in NOT_AN_UPDATE:
                    await call(socket, method.name, instance_id=instance_id)
                    answered = await frame(socket)
                    assert isinstance(answered["error"], dict), answered
                    refused[method.name] = answered["error"]["code"]
            signed_in = await login(session, address, AccessToken(token))
            with pytest.raises(aiohttp.WSServerHandshakeError) as relayed:
                async with session.ws_connect(
                    url(address, f"/instances/{instance_id}/ws"),
                    headers=headers,
                ):
                    pass

        assert state == "succeeded"
        assert refused == {method.name: "PERMISSION_DENIED" for method in NOT_AN_UPDATE}
        assert signed_in.status == 401
        assert relayed.value.status == 401

    asyncio.run(scenario())


def test_rotating_the_update_token_refuses_the_previous_one_and_keeps_the_access_token(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub")
        access = hub.access.issue()
        assert access is not None
        previous = hub.access.rotate_update_token()
        current = hub.access.rotate_update_token()
        async with served(hub) as address, aiohttp.ClientSession() as session:
            with pytest.raises(aiohttp.WSServerHandshakeError) as rotated:
                async with session.ws_connect(
                    url(address, "/ws"),
                    headers={"Authorization": f"Bearer {previous}"},
                ):
                    pass
            for token in (current, access):
                async with session.ws_connect(
                    url(address, "/ws"),
                    headers={"Authorization": f"Bearer {token}"},
                ) as socket:
                    await call(socket, "operation.get", operation_id=str(uuid4()))
                    answered = await frame(socket)
                    assert isinstance(answered["error"], dict), answered
                    assert answered["error"]["code"] == "NOT_FOUND"

        stored = (tmp_path / "hub" / "registry.sqlite").read_bytes().decode(errors="ignore")
        assert rotated.value.status == 401
        assert current not in stored

    asyncio.run(scenario())
