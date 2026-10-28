"""The hub's browser methods on an instance's hub route, as far as they reach without a browser:
only a browser-on instance has them, the hub keeps the profiles, and no route on the hub takes a
Playwright client (ADR 0114). tests/test_browser_service.py drives them on a real browser."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import aiohttp

from kinby.contracts import (
    BROWSER_PROFILE_DELETE,
    BROWSER_PROFILE_RENAME,
    BROWSER_TAKEOVER_START,
    HUB_SCOPES,
    INSTANCE_STATUS,
    THREAD_TAKEOVER_INPUT,
    THREAD_TAKEOVER_START,
    BrowserProfile,
    BrowserProfileDeleteCommand,
    BrowserProfileListResult,
    BrowserProfileRenameCommand,
    ContractModel,
    ControlToken,
    ErrorCode,
    ErrorEnvelope,
    FrameType,
    InstanceStatusCommand,
    InstanceStatusResult,
    Stream,
    TakeoverKey,
    ThreadTakeoverInputCommand,
)
from kinby.hub import Hub
from kinby.hub.browser import BrowserLimits
from kinby.instance import Serve
from tests.test_hub import FakeRuntime, hub_at, hub_client, started_instance
from tests.test_hub_home_reads import answers
from tests.test_hub_routing import control_token
from tests.test_hub_server import served, url

_PRIVATE_URL = "http://hub:8080"
#: A browser service no browser in these tests reaches.
_NOWHERE = "ws://127.0.0.1:9/"


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


@dataclass(frozen=True)
class BrowserHub:
    hub: Hub
    runtime: FakeBrowserRuntime
    #: Where the hub reaches its instances.
    instances: FakeRuntime
    address: Serve
    alice: UUID
    bob: UUID

    def route(self, instance_id: UUID) -> str:
        return url(self.address, f"/instances/{instance_id}/hub")

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
async def browser_hub(
    directory: Path, limits: BrowserLimits, address: str = _NOWHERE
) -> AsyncIterator[BrowserHub]:
    """A served hub whose browser service answers at *address*, and whose alice has the browser
    on, beside bob who does not."""
    runtime = FakeBrowserRuntime(address)
    instances = FakeRuntime()
    hub = hub_at(
        directory,
        runtime=instances,
        private_url=_PRIVATE_URL,
        browser=runtime,
        browser_limits=limits,
    )
    client = hub_client(hub)
    alice = (await started_instance(client, hub, name="alice")).instance_id
    bob = (await started_instance(client, hub, name="bob")).instance_id
    browser_on(hub, alice)
    async with served(hub) as served_at:
        yield BrowserHub(hub, runtime, instances, served_at, alice, bob)
    hub.close()


def result(answer: dict[str, object]) -> dict[str, object]:
    assert answer["type"] == FrameType.RESULT.value, answer
    answered = answer["result"]
    assert isinstance(answered, dict)
    return answered


def error(answer: dict[str, object]) -> dict[str, object]:
    assert answer["type"] == FrameType.ERROR.value, answer
    refused = answer["error"]
    assert isinstance(refused, dict)
    return refused


_LIMITS = BrowserLimits(cap=3, wait_seconds=5, idle_seconds=300)


def test_no_route_on_the_hub_takes_a_playwright_client(tmp_path):
    async def scenario() -> int:
        async with (
            browser_hub(tmp_path / "hub", _LIMITS) as hub,
            aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {hub.token(hub.alice)}"}
            ) as session,
        ):
            try:
                async with session.ws_connect(
                    url(hub.address, f"/instances/{hub.alice}/browser"),
                    headers={"x-playwright-browser": "chromium"},
                ):
                    return 101
            except aiohttp.WSServerHandshakeError as refused:
                return refused.status

    assert asyncio.run(scenario()) == 404


def test_a_browser_off_instance_is_refused_the_browser(tmp_path):
    async def scenario() -> None:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            refused = error(await hub.open(hub.bob, uuid4()))

        assert refused["code"] == ErrorCode.NOT_FOUND.value
        assert hub.runtime.starts == 0

    asyncio.run(scenario())


def test_opening_a_profile_the_instance_lacks_fails_with_a_clear_error(tmp_path):
    async def scenario() -> tuple[dict[str, object], int]:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            return error(await hub.open(hub.alice, uuid4(), "LinkedIn")), hub.runtime.starts

    refused, starts = asyncio.run(scenario())

    assert refused["code"] == ErrorCode.NOT_FOUND.value
    assert refused["message"] == 'The instance has no browser profile named "LinkedIn".'
    assert starts == 0


def test_a_turn_with_no_browser_open_has_nothing_to_act_on_or_save(tmp_path):
    async def scenario() -> list[dict[str, object]]:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            turn: dict[str, object] = {"turn_id": str(uuid4())}
            return [
                error(await hub.call(hub.alice, method, params))
                for method, params in [
                    ("browser.profile.save", {**turn, "name": "LinkedIn"}),
                    ("browser.text", turn),
                    ("browser.screenshot", turn),
                ]
            ]

    refused = asyncio.run(scenario())

    assert [answer["code"] for answer in refused] == [ErrorCode.NOT_FOUND.value] * 3


def test_a_turn_with_no_browser_open_has_no_page_to_drive(tmp_path):
    async def scenario() -> object:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            return await hub_client(hub.hub).call(
                THREAD_TAKEOVER_INPUT,
                ThreadTakeoverInputCommand(
                    instance_id=hub.alice, turn_id=uuid4(), input=TakeoverKey(key="a")
                ),
            )

    driven = asyncio.run(scenario())

    assert isinstance(driven, ErrorEnvelope)
    assert driven.code is ErrorCode.NOT_FOUND


def test_a_browser_off_instance_has_no_takeover_to_open_a_page_for(tmp_path):
    async def scenario() -> tuple[list[Stream[ContractModel] | ErrorEnvelope], int]:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            started = [
                await hub.hub.dispatcher.subscribe(subscription, params, HUB_SCOPES)
                for subscription, params in [
                    (
                        BROWSER_TAKEOVER_START.name,
                        {
                            "instance_id": hub.bob,
                            "takeover_id": uuid4(),
                            "url": "https://example.com/",
                        },
                    ),
                    (THREAD_TAKEOVER_START.name, {"instance_id": hub.bob, "turn_id": uuid4()}),
                ]
            ]
            return started, hub.runtime.starts

    started, starts = asyncio.run(scenario())

    assert [(type(answer), getattr(answer, "code", None)) for answer in started] == [
        (ErrorEnvelope, ErrorCode.NOT_FOUND)
    ] * 2
    assert starts == 0


def test_the_user_renames_and_deletes_an_instances_profiles_and_no_other_instances(tmp_path):
    async def scenario() -> list[object]:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            hub.hub.browser_profiles.save(
                hub.alice, "LinkedIn", "www.linkedin.com", {"cookies": [], "origins": []}
            )
            listed = result(await hub.call(hub.alice, "browser.profiles", {}))
            client = hub_client(hub.hub)
            return [
                listed,
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

    listed, bobs_rename, bobs_delete, renamed, deleted = asyncio.run(scenario())

    assert listed == {"profiles": [{"name": "LinkedIn", "site": "www.linkedin.com"}]}
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
