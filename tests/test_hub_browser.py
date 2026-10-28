"""The hub's browser route: a token for one turn of one browser-on instance, a relay to the browser
service, a cap on browsers at once, and a service that runs only while it is needed (ADR 0113)."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import aiohttp
import pytest
from aiohttp import web

from kinby.contracts import ControlToken, ErrorCode, FrameType
from kinby.core.contract_server import CONTROL_TOKEN_VARIABLE
from kinby.hub import Hub
from kinby.hub.browser import BrowserLimits
from kinby.hub.browser_service import LAUNCH_OPTIONS
from kinby.instance import Serve
from kinby.plugins.hub import HUB_URL_VARIABLE
from kinby.plugins.playwright_browser import PlaywrightBrowser
from tests.test_hub import FakeRuntime, hub_at, hub_client, started_instance
from tests.test_hub_home_reads import answers
from tests.test_hub_routing import control_token
from tests.test_hub_server import served, url

_PRIVATE_URL = "http://hub:8080"


class FakeBrowserRuntime:
    """A browser service that answers at *address* while it runs."""

    def __init__(self, address: str) -> None:
        self.address = address
        self.starts = 0
        self.stops = 0
        self.stopped = asyncio.Event()
        self.up = False

    async def start(self) -> str:
        self.starts += 1
        self.up = True
        self.stopped.clear()
        return self.address

    async def running(self) -> bool:
        return self.up

    async def stop(self) -> None:
        self.stops += 1
        self.up = False
        self.stopped.set()


@asynccontextmanager
async def playwright_server(connected: list[dict[str, str]]) -> AsyncIterator[str]:
    """A stand-in for Playwright's browser server that echoes each frame and records the
    headers of each connection."""

    async def socket(request: web.Request) -> web.WebSocketResponse:
        connected.append(dict(request.headers))
        response = web.WebSocketResponse()
        await response.prepare(request)
        async for message in response:
            await response.send_str(f"echo {message.data}")
        return response

    application = web.Application()
    application.router.add_get("/", socket)
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"ws://127.0.0.1:{site.port}/"
    finally:
        await runner.cleanup()


@dataclass(frozen=True)
class BrowserHub:
    hub: Hub
    runtime: FakeBrowserRuntime
    address: Serve
    alice: UUID
    bob: UUID

    def route(self, instance_id: UUID) -> str:
        return url(self.address, f"/instances/{instance_id}/hub")

    def browser(self, instance_id: UUID) -> str:
        return url(self.address, f"/instances/{instance_id}/browser")

    def token(self, instance_id: UUID) -> ControlToken:
        return control_token(self.hub, str(instance_id))

    async def open(self, instance_id: UUID, turn: UUID) -> dict[str, object]:
        (answer,) = await answers(
            self.route(instance_id),
            self.token(instance_id),
            ("browser.open", {"turn_id": str(turn)}),
        )
        return answer


def browser_on(hub: Hub, instance_id: UUID) -> None:
    record = hub.registry.instance(instance_id)
    assert record is not None
    manifest = record.path / "kinby.toml"
    manifest.write_text("browser = true\n" + manifest.read_text(encoding="utf-8"), encoding="utf-8")


@asynccontextmanager
async def browser_hub(
    directory: Path, limits: BrowserLimits, connected: list[dict[str, str]] | None = None
) -> AsyncIterator[BrowserHub]:
    """A served hub whose alice has the browser on, beside bob who does not."""
    async with playwright_server([] if connected is None else connected) as service:
        runtime = FakeBrowserRuntime(service)
        hub = hub_at(
            directory,
            runtime=FakeRuntime(),
            private_url=_PRIVATE_URL,
            browser=runtime,
            browser_limits=limits,
        )
        client = hub_client(hub)
        alice = (await started_instance(client, hub, name="alice")).instance_id
        bob = (await started_instance(client, hub, name="bob")).instance_id
        browser_on(hub, alice)
        async with served(hub) as address:
            yield BrowserHub(hub, runtime, address, alice, bob)
        hub.close()


def _result(answer: dict[str, object]) -> dict[str, object]:
    assert answer["type"] == FrameType.RESULT.value, answer
    result = answer["result"]
    assert isinstance(result, dict)
    return result


def _error(answer: dict[str, object]) -> dict[str, object]:
    assert answer["type"] == FrameType.ERROR.value, answer
    error = answer["error"]
    assert isinstance(error, dict)
    return error


async def _connects(route: str, token: object) -> int | str:
    """The relayed echo of one frame, or the status the route refused the connection with."""
    async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {token}"}) as session:
        try:
            async with session.ws_connect(
                route, headers={"x-playwright-browser": "chromium"}
            ) as socket:
                await socket.send_str("hello")
                return (await socket.receive(timeout=5)).data
        except aiohttp.WSServerHandshakeError as error:
            return error.status


_LIMITS = BrowserLimits(cap=3, wait_seconds=5, idle_seconds=300, connect_seconds=60)


def test_the_route_issues_a_token_for_one_turn_that_connects_only_its_own_instance(tmp_path):
    async def scenario() -> None:
        connected: list[dict[str, str]] = []
        async with browser_hub(tmp_path / "hub", _LIMITS, connected) as hub:
            opened = _result(await hub.open(hub.alice, uuid4()))
            token = opened["token"]
            as_bob = await _connects(hub.browser(hub.bob), token)
            as_alice = await _connects(hub.browser(hub.alice), token)
            again = await _connects(hub.browser(hub.alice), token)

        assert opened["address"] == f"ws://hub:8080/instances/{hub.alice}/browser"
        assert as_bob == 401
        assert as_alice == "echo hello"
        assert again == 401
        (headers,) = connected
        assert headers["x-playwright-launch-options"] == LAUNCH_OPTIONS
        assert headers["x-playwright-browser"] == "chromium"
        assert "Authorization" not in headers

    asyncio.run(scenario())


def test_a_browser_off_instance_is_refused_the_route(tmp_path):
    async def scenario() -> None:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            refused = _error(await hub.open(hub.bob, uuid4()))

        assert refused["code"] == ErrorCode.NOT_FOUND.value
        assert hub.runtime.starts == 0

    asyncio.run(scenario())


def test_with_the_cap_full_a_request_that_waits_past_its_limit_fails_with_a_clear_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    limits = BrowserLimits(cap=1, wait_seconds=0.05, idle_seconds=300, connect_seconds=60)

    async def scenario() -> None:
        async with browser_hub(tmp_path / "hub", limits) as hub:
            _result(await hub.open(hub.alice, uuid4()))
            refused = _error(await hub.open(hub.alice, uuid4()))
            monkeypatch.setenv(HUB_URL_VARIABLE, hub.route(hub.alice).replace("http", "ws", 1))
            monkeypatch.setenv(CONTROL_TOKEN_VARIABLE, hub.token(hub.alice))
            with pytest.raises(ValueError, match="cap of 1") as raised:
                await PlaywrightBrowser().open(uuid4())

        assert refused["code"] == ErrorCode.RESOURCE_EXHAUSTED.value
        assert refused["retryable"] is True
        assert refused["message"] == (
            "The hub's browser cap of 1 is reached, and no browser came free within 0.05 "
            "seconds. Try again later."
        )
        assert str(raised.value) == f"The hub refused the browser: {refused['message']}"

    asyncio.run(scenario())


def test_a_token_never_connected_gives_its_browser_back(tmp_path):
    limits = BrowserLimits(cap=1, wait_seconds=5, idle_seconds=300, connect_seconds=0.05)

    async def scenario() -> None:
        async with browser_hub(tmp_path / "hub", limits) as hub:
            first = _result(await hub.open(hub.alice, uuid4()))
            second = _result(await hub.open(hub.alice, uuid4()))
            expired = await _connects(hub.browser(hub.alice), first["token"])

        assert second["token"] != first["token"]
        assert expired == 401

    asyncio.run(scenario())


def test_the_hub_starts_the_service_on_the_first_request_and_stops_it_once_idle(tmp_path):
    limits = BrowserLimits(cap=3, wait_seconds=5, idle_seconds=0.05, connect_seconds=60)

    async def scenario() -> None:
        async with browser_hub(tmp_path / "hub", limits) as hub:
            runtime = hub.runtime
            assert runtime.starts == 0
            first = _result(await hub.open(hub.alice, uuid4()))
            second = _result(await hub.open(hub.alice, uuid4()))
            starts = runtime.starts
            await _connects(hub.browser(hub.alice), first["token"])
            await _connects(hub.browser(hub.alice), second["token"])
            await asyncio.wait_for(runtime.stopped.wait(), timeout=5)
            _result(await hub.open(hub.alice, uuid4()))

        assert starts == 1
        assert runtime.stops == 1
        assert runtime.starts == 2

    asyncio.run(scenario())


def test_a_service_that_died_starts_again_on_the_next_request(tmp_path):
    async def scenario() -> tuple[int, int | str]:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            _result(await hub.open(hub.alice, uuid4()))
            hub.runtime.up = False
            opened = _result(await hub.open(hub.alice, uuid4()))
            return hub.runtime.starts, await _connects(hub.browser(hub.alice), opened["token"])

    starts, connected = asyncio.run(scenario())

    assert starts == 2
    assert connected == "echo hello"
