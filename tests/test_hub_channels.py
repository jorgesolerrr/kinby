"""The hub's channels: set up, paired, and sent every notification an instance raises, once."""

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import aiohttp
from aiohttp import web

from kinby.cli.client import ContractClient
from kinby.contracts import (
    CHANNEL_CREATE,
    CHANNEL_DELETE,
    CHANNEL_KINDS,
    CHANNEL_LIST,
    CHANNEL_MUTE,
    CHANNEL_TEST,
    CHANNEL_UNMUTE,
    CHANNEL_UPDATE,
    HUB_SCOPES,
    NOTIFICATION_RAISE,
    Channel,
    ChannelCreateCommand,
    ChannelDeleteCommand,
    ChannelKindDescription,
    ChannelKindsCommand,
    ChannelKindsResult,
    ChannelListCommand,
    ChannelListResult,
    ChannelMuteCommand,
    ChannelTestCommand,
    ChannelUpdateCommand,
    ErrorCode,
    ErrorEnvelope,
    NotificationRaiseCommand,
    NotificationRaiseResult,
    Scope,
    SetupField,
    SetupFieldKind,
    SetupFieldType,
    SetupValue,
)
from kinby.hub import Hub
from kinby.hub.channels import ChannelNotification, ChannelSetup, ChannelUser, Inbound, SentRef
from kinby.instance import Serve
from tests.test_hub import (
    FakeControl,
    FakeRuntime,
    created_instance,
    hub_at,
    hub_client,
    started_instance,
)
from tests.test_hub_home_reads import answers
from tests.test_hub_routing import control_token
from tests.test_hub_server import served, url

_TOKEN = "sentinel-fake-token"
_NOW = datetime(2026, 10, 23, 9, tzinfo=UTC)
_PUBLIC_URL = "https://kinby.example"
_LIMIT = 40
_SETTINGS = (
    SetupField(
        name="token",
        label="Token",
        description="What the fake service knows the bot by.",
        kind=SetupFieldKind.SECRET,
        type=SetupFieldType.TEXT,
        required=True,
    ),
    SetupField(
        name="room",
        label="Room",
        description="Where the fake service posts.",
        kind=SetupFieldKind.CONFIG,
        type=SetupFieldType.TEXT,
        required=False,
        default="general",
    ),
)


@dataclass(frozen=True)
class Sent:
    """One notification the fake service took: to whom, with which token and settings."""

    to: ChannelUser | None
    settings: Mapping[str, SetupValue]
    notification: ChannelNotification


class FakeService:
    """The service a fake channel talks to: what it took, whether it is down, and the users whose
    calls to the hub it holds until their event is set."""

    def __init__(self, *, down: bool = False) -> None:
        self.down = down
        self.attempts: list[ChannelNotification] = []
        self.sent: asyncio.Queue[Sent] = asyncio.Queue()
        self.held: dict[str, asyncio.Event] = {}
        self.receiving: asyncio.Queue[str] = asyncio.Queue()

    def kind(self) -> type:
        service = self

        class FakeChannel:
            setup = ChannelSetup(settings=_SETTINGS, pairs=True, message_limit=_LIMIT)

            def __init__(
                self, settings: Mapping[str, SetupValue], paired_user: ChannelUser | None
            ) -> None:
                self._settings = settings
                self._paired_user = paired_user

            async def send(self, notification: ChannelNotification) -> SentRef:
                service.attempts.append(notification)
                if service.down:
                    raise ConnectionError("the fake service is down")
                service.sent.put_nowait(Sent(self._paired_user, self._settings, notification))
                return SentRef(f"message-{len(service.attempts)}")

            async def receive(self, request: web.Request) -> Inbound | None:
                body = await request.json()
                service.receiving.put_nowait(body["user"])
                if body["user"] in service.held:
                    await service.held[body["user"]].wait()
                return Inbound(sender=ChannelUser(body["user"]), text=body["text"])

        return FakeChannel


class FakeTime:
    """A clock that a sleep moves forward once its gate opens, at once without one."""

    def __init__(self, *, gate: asyncio.Event | None = None) -> None:
        self.now = _NOW
        self.gate = gate
        self.slept: list[float] = []
        self.woken = 0

    def clock(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        if self.gate is not None:
            await self.gate.wait()
        self.now += timedelta(seconds=seconds)
        await asyncio.sleep(0)
        self.woken += 1


def channel_hub(directory: Path, service: FakeService, time: FakeTime) -> Hub:
    return hub_at(
        directory,
        runtime=FakeRuntime(),
        control=FakeControl(),
        channel_kinds={"fake": service.kind()},
        clock=time.clock,
        sleep=time.sleep,
        public_url=_PUBLIC_URL,
    )


async def created_channel(client: ContractClient) -> Channel:
    created = await client.call(
        CHANNEL_CREATE,
        ChannelCreateCommand(
            name="phone", kind="fake", settings={"room": "ops"}, secrets={"token": _TOKEN}
        ),
    )
    assert isinstance(created, Channel), created
    return created


async def inbound(address: Serve, channel: Channel, user: str, text: str) -> None:
    async with (
        aiohttp.ClientSession() as session,
        session.post(
            url(address, f"/channels/{channel.channel_id}/fake"),
            json={"user": user, "text": text},
        ) as answer,
    ):
        assert answer.status == 200, await answer.text()


async def paired_channel(client: ContractClient, address: Serve) -> Channel:
    channel = await created_channel(client)
    assert channel.pairing_code is not None
    await inbound(address, channel, "jorge", channel.pairing_code)
    return await listed_channel(client, channel.channel_id)


async def listed_channel(client: ContractClient, channel_id: UUID) -> Channel:
    listed = await client.call(CHANNEL_LIST, ChannelListCommand())
    assert isinstance(listed, ChannelListResult), listed
    return next(channel for channel in listed.channels if channel.channel_id == channel_id)


def raising(hub: Hub, instance_id: UUID) -> ContractClient:
    """A client holding what the instance holds on its hub route."""
    scopes, dispatcher = hub.hub_route(instance_id)
    return ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(scopes))


def notification(key: str, *, text: str = "It ran.") -> NotificationRaiseCommand:
    return NotificationRaiseCommand(
        key=key,
        thread_id=UUID(int=1),
        source="Daily news",
        title=key,
        text=text,
        options=["Rerun"],
    )


async def raised(client: ContractClient, key: str) -> NotificationRaiseResult:
    result = await client.call(NOTIFICATION_RAISE, notification(key))
    assert isinstance(result, NotificationRaiseResult), result
    return result


async def next_sent(service: FakeService) -> Sent:
    return await asyncio.wait_for(service.sent.get(), timeout=5)


async def until(condition: Callable[[], bool]) -> None:
    for _ in range(500):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the condition never held")


def test_the_hub_lists_each_channel_kind_with_its_settings(tmp_path):
    async def scenario() -> None:
        hub = channel_hub(tmp_path / "hub", FakeService(), FakeTime())

        kinds = await hub_client(hub).call(CHANNEL_KINDS, ChannelKindsCommand())

        assert kinds == ChannelKindsResult(
            kinds=[ChannelKindDescription(kind="fake", settings=list(_SETTINGS), pairs=True)]
        )

    asyncio.run(scenario())


def test_a_channel_keeps_its_secrets_as_hub_secrets_and_pairs_with_the_first_sender_of_its_code(
    tmp_path,
):
    async def scenario() -> None:
        hub = channel_hub(tmp_path / "hub", FakeService(), FakeTime())
        client = hub_client(hub)
        channel = await created_channel(client)
        assert channel.pairing_code is not None
        async with served(hub) as address:
            await inbound(address, channel, "mallory", "000000")
            await inbound(address, channel, "mallory", "¿código?")
            unpaired = await listed_channel(client, channel.channel_id)
            await inbound(address, channel, "jorge", f" {channel.pairing_code}\n")
            await inbound(address, channel, "mallory", channel.pairing_code)
            paired = await listed_channel(client, channel.channel_id)

        secret = channel.settings["token"]
        assert isinstance(secret, str)
        assert channel.settings == {"room": "ops", "token": secret}
        assert hub.secrets.values()[secret] == _TOKEN
        assert _TOKEN not in channel.model_dump_json()
        assert len(channel.pairing_code) == 6
        assert channel.pairing_code.isdigit()
        assert channel.pairing_expires_at == _NOW + timedelta(minutes=15)
        assert not unpaired.paired
        assert paired.paired
        assert paired.pairing_code is None

    asyncio.run(scenario())


def test_a_second_sender_of_the_code_overlapping_the_first_pairs_nothing(tmp_path):
    async def scenario() -> None:
        service = FakeService()
        hub = channel_hub(tmp_path / "hub", service, FakeTime())
        client = hub_client(hub)
        channel = await created_channel(client)
        assert channel.pairing_code is not None
        service.held = {"jorge": asyncio.Event(), "mallory": asyncio.Event()}
        async with served(hub) as address:
            first = asyncio.create_task(inbound(address, channel, "jorge", channel.pairing_code))
            second = asyncio.create_task(inbound(address, channel, "mallory", channel.pairing_code))
            assert {await service.receiving.get(), await service.receiving.get()} == {
                "jorge",
                "mallory",
            }
            service.held["jorge"].set()
            await first
            service.held["mallory"].set()
            await second
        await client.call(CHANNEL_TEST, ChannelTestCommand(channel_id=channel.channel_id))

        assert (await next_sent(service)).to == "jorge"

    asyncio.run(scenario())


def test_a_code_pairs_nothing_once_it_expired(tmp_path):
    async def scenario() -> None:
        time = FakeTime()
        hub = channel_hub(tmp_path / "hub", FakeService(), time)
        client = hub_client(hub)
        channel = await created_channel(client)
        assert channel.pairing_code is not None
        time.now += timedelta(minutes=15)
        async with served(hub) as address:
            await inbound(address, channel, "jorge", channel.pairing_code)

        assert not (await listed_channel(client, channel.channel_id)).paired

    asyncio.run(scenario())


def test_an_instance_raises_a_notification_on_its_hub_route_and_a_paired_channel_sends_it_once(
    tmp_path,
):
    async def scenario() -> None:
        service = FakeService()
        hub = channel_hub(tmp_path / "hub", service, FakeTime())
        client = hub_client(hub)
        alice = (await started_instance(client, hub)).instance_id
        long_text = "The news routine found nothing new today. " * 3
        params = notification("approval-1", text=long_text).model_dump(mode="json")
        async with served(hub) as address:
            await paired_channel(client, address)
            route = url(address, f"/instances/{alice}/hub")
            first, again = await answers(
                route,
                control_token(hub, str(alice)),
                ("notification.raise", params),
                ("notification.raise", params),
            )
            sent = await next_sent(service)
            await raised(raising(hub, alice), "approval-2")
            later = await next_sent(service)

        assert isinstance(first["result"], dict), first
        assert again["result"] == first["result"]
        assert sent.to == "jorge"
        assert sent.settings == {"room": "ops", "token": _TOKEN}
        assert sent.notification.header == "alice · Daily news"
        assert sent.notification.title == "approval-1"
        assert sent.notification.text == long_text[: _LIMIT - 1] + "…"
        assert sent.notification.link == f"{_PUBLIC_URL}/instances/{alice}/threads/{UUID(int=1)}"
        assert sent.notification.options == ("Rerun",)
        assert later.notification.title == "approval-2"
        assert [attempt.title for attempt in service.attempts] == ["approval-1", "approval-2"]

    asyncio.run(scenario())


def test_an_instance_past_its_daily_cap_is_refused_and_the_hub_says_so_once(tmp_path):
    async def scenario() -> None:
        service = FakeService()
        time = FakeTime()
        hub = channel_hub(tmp_path / "hub", service, time)
        client = hub_client(hub)
        alice_id = (await started_instance(client, hub)).instance_id
        alice = raising(hub, alice_id)
        async with served(hub) as address:
            await paired_channel(client, address)
        for index in range(20):
            await raised(alice, f"news {index}")
        refused = await alice.call(NOTIFICATION_RAISE, notification("news 20"))
        again = await alice.call(NOTIFICATION_RAISE, notification("news 21"))
        repeated = await raised(alice, "news 0")
        await until(lambda: len(service.attempts) == 21)
        time.now += timedelta(days=1)
        await raised(alice, "tomorrow")
        await until(lambda: len(service.attempts) == 22)

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.RESOURCE_EXHAUSTED
        assert "20 notifications today" in refused.message
        assert isinstance(again, ErrorEnvelope)
        assert again.code is ErrorCode.RESOURCE_EXHAUSTED
        assert isinstance(repeated, NotificationRaiseResult)
        cap = service.attempts[20]
        assert cap.header == "alice · Daily news"
        assert cap.title == "Daily notification cap reached"
        assert cap.link == f"{_PUBLIC_URL}/instances/{alice_id}/threads/{UUID(int=1)}"
        assert cap.options == ()
        assert service.attempts[21].title == "tomorrow"

    asyncio.run(scenario())


def test_every_instance_holds_the_scope_to_raise_a_notification_and_the_hubs_clients_do_not(
    tmp_path,
):
    async def scenario() -> None:
        hub = channel_hub(tmp_path / "hub", FakeService(), FakeTime())
        alice = (await created_instance(hub_client(hub))).instance_id

        assert Scope.NOTIFICATION_RAISE in hub.instance_scopes(alice)
        assert Scope.NOTIFICATION_RAISE not in HUB_SCOPES

    asyncio.run(scenario())


def test_an_unpaired_channel_sends_nothing(tmp_path):
    async def scenario() -> None:
        service = FakeService()
        hub = channel_hub(tmp_path / "hub", service, FakeTime())
        client = hub_client(hub)
        alice = raising(hub, (await created_instance(client)).instance_id)
        channel = await created_channel(client)
        await raised(alice, "before pairing")
        assert channel.pairing_code is not None
        async with served(hub) as address:
            await inbound(address, channel, "jorge", channel.pairing_code)
        await raised(alice, "after pairing")
        await next_sent(service)

        assert [attempt.title for attempt in service.attempts] == ["after pairing"]

    asyncio.run(scenario())


def test_a_channel_that_mutes_an_instance_sends_nothing_from_it(tmp_path):
    async def scenario() -> None:
        service = FakeService()
        hub = channel_hub(tmp_path / "hub", service, FakeTime())
        client = hub_client(hub)
        alice_id = (await created_instance(client, name="alice")).instance_id
        bob = raising(hub, (await created_instance(client, name="bob")).instance_id)
        alice = raising(hub, alice_id)
        async with served(hub) as address:
            channel = await paired_channel(client, address)
        mute = ChannelMuteCommand(channel_id=channel.channel_id, instance_id=alice_id)
        muting = await client.call(CHANNEL_MUTE, mute)
        await raised(alice, "muted")
        await raised(bob, "from bob")
        await next_sent(service)
        unmuting = await client.call(CHANNEL_UNMUTE, mute)
        await raised(alice, "unmuted")
        await next_sent(service)

        assert isinstance(muting, Channel), muting
        assert muting.muted == [alice_id]
        assert isinstance(unmuting, Channel), unmuting
        assert unmuting.muted == []
        assert [attempt.title for attempt in service.attempts] == ["from bob", "unmuted"]

    asyncio.run(scenario())


def test_a_failing_send_is_retried_with_backoff_for_a_day_and_then_fails_for_good(tmp_path):
    directory = tmp_path / "hub"
    service = FakeService(down=True)
    time = FakeTime()
    instances: list[UUID] = []

    async def failing() -> None:
        hub = channel_hub(directory, service, time)
        client = hub_client(hub)
        instances.append((await created_instance(client)).instance_id)
        alice = raising(hub, instances[0])
        async with served(hub) as address:
            channel = await paired_channel(client, address)
        await raised(alice, "news")
        # 5 seconds doubling to 5 minutes, then every 5 minutes, until the try past 24 hours.
        await until(lambda: len(service.attempts) == 294)
        failed = await listed_channel(client, channel.channel_id)
        hub.close()

        assert failed.last_failure is not None
        assert failed.last_failure.error == "the fake service is down"
        assert failed.last_failure.at == time.now
        assert failed.last_sent_at is None

    async def rebuilt() -> None:
        working = FakeService()
        hub = channel_hub(directory, working, FakeTime())
        await hub.recover()
        alice = raising(hub, instances[0])
        await raised(alice, "fresh")
        await next_sent(working)

        assert [attempt.title for attempt in working.attempts] == ["fresh"]

    asyncio.run(failing())
    asyncio.run(rebuilt())

    assert time.slept[:7] == [5, 10, 20, 40, 80, 160, 300]
    assert set(time.slept[7:]) == {300}
    assert sum(time.slept[:-1]) < timedelta(days=1).total_seconds() <= sum(time.slept)


def test_a_channel_disabled_while_a_delivery_waits_to_retry_does_not_send_it(tmp_path):
    async def scenario() -> None:
        service = FakeService(down=True)
        time = FakeTime(gate=asyncio.Event())
        hub = channel_hub(tmp_path / "hub", service, time)
        client = hub_client(hub)
        alice = raising(hub, (await created_instance(client)).instance_id)
        async with served(hub) as address:
            channel = await paired_channel(client, address)
        await raised(alice, "news")
        await until(lambda: len(service.attempts) == 1)
        await client.call(
            CHANNEL_UPDATE, ChannelUpdateCommand(channel_id=channel.channel_id, enabled=False)
        )
        service.down = False
        assert time.gate is not None
        time.gate.set()
        await until(lambda: time.woken == 1)

        assert [attempt.title for attempt in service.attempts] == ["news"]

    asyncio.run(scenario())


def test_pending_deliveries_resume_after_the_hub_is_rebuilt_on_the_same_directory(tmp_path):
    directory = tmp_path / "hub"
    later = FakeTime()

    async def interrupted() -> None:
        service = FakeService(down=True)
        hub = channel_hub(directory, service, FakeTime(gate=asyncio.Event()))
        client = hub_client(hub)
        alice = raising(hub, (await created_instance(client)).instance_id)
        async with served(hub) as address:
            await paired_channel(client, address)
        await raised(alice, "news")
        await until(lambda: len(service.attempts) == 1)
        hub.close()

    async def resumed() -> Sent:
        working = FakeService()
        hub = channel_hub(directory, working, later)
        await hub.recover()
        return await next_sent(working)

    asyncio.run(interrupted())
    sent = asyncio.run(resumed())

    assert sent.notification.title == "news"
    assert sent.to == "jorge"
    assert later.slept[0] == 5


def test_a_test_send_goes_through_the_channel_and_records_its_failure(tmp_path):
    async def scenario() -> None:
        service = FakeService()
        hub = channel_hub(tmp_path / "hub", service, FakeTime())
        client = hub_client(hub)
        unpaired = await created_channel(client)
        refused = await client.call(
            CHANNEL_TEST, ChannelTestCommand(channel_id=unpaired.channel_id)
        )
        async with served(hub) as address:
            channel = await paired_channel(client, address)
        tested = await client.call(CHANNEL_TEST, ChannelTestCommand(channel_id=channel.channel_id))
        sent = await next_sent(service)
        service.down = True
        failed = await client.call(CHANNEL_TEST, ChannelTestCommand(channel_id=channel.channel_id))

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert isinstance(tested, Channel), tested
        assert tested.last_sent_at == _NOW
        assert sent.to == "jorge"
        assert isinstance(failed, Channel), failed
        assert failed.last_sent_at == _NOW
        assert failed.last_failure is not None
        assert failed.last_failure.error == "the fake service is down"

    asyncio.run(scenario())


def test_a_channel_is_renamed_disabled_and_deleted_with_its_secrets(tmp_path):
    async def scenario() -> None:
        service = FakeService()
        hub = channel_hub(tmp_path / "hub", service, FakeTime())
        client = hub_client(hub)
        alice = raising(hub, (await created_instance(client)).instance_id)
        async with served(hub) as address:
            channel = await paired_channel(client, address)
        updated = await client.call(
            CHANNEL_UPDATE,
            ChannelUpdateCommand(
                channel_id=channel.channel_id,
                name="pocket",
                enabled=False,
                secrets={"token": "rotated"},
            ),
        )
        await raised(alice, "while disabled")
        await client.call(
            CHANNEL_UPDATE, ChannelUpdateCommand(channel_id=channel.channel_id, enabled=True)
        )
        await raised(alice, "enabled again")
        sent = await next_sent(service)
        deleted = await client.call(
            CHANNEL_DELETE, ChannelDeleteCommand(channel_id=channel.channel_id)
        )
        missing = await client.call(CHANNEL_TEST, ChannelTestCommand(channel_id=channel.channel_id))

        assert isinstance(updated, Channel), updated
        assert (updated.name, updated.enabled, updated.paired) == ("pocket", False, True)
        assert updated.settings == channel.settings
        assert [attempt.title for attempt in service.attempts] == ["enabled again"]
        assert sent.settings["token"] == "rotated"
        assert deleted == ChannelListResult(channels=[])
        assert hub.secrets.values() == {}
        assert isinstance(missing, ErrorEnvelope)
        assert missing.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_a_channel_is_refused_an_unknown_kind_and_missing_or_unknown_settings(tmp_path):
    async def scenario() -> None:
        hub = channel_hub(tmp_path / "hub", FakeService(), FakeTime())
        client = hub_client(hub)

        unknown = await client.call(
            CHANNEL_CREATE, ChannelCreateCommand(name="phone", kind="pigeon")
        )
        invalid = await client.call(
            CHANNEL_CREATE,
            ChannelCreateCommand(name="phone", kind="fake", settings={"room": 3, "colour": "red"}),
        )
        listed = await client.call(CHANNEL_LIST, ChannelListCommand())

        assert isinstance(unknown, ErrorEnvelope)
        assert unknown.code is ErrorCode.INVALID_SETUP
        assert set(unknown.fields) == {"kind"}
        assert isinstance(invalid, ErrorEnvelope)
        assert invalid.code is ErrorCode.INVALID_SETUP
        assert set(invalid.fields) == {"token", "room", "colour"}
        assert listed == ChannelListResult(channels=[])
        assert hub.secrets.values() == {}

    asyncio.run(scenario())


def test_an_inbound_call_for_a_channel_the_hub_does_not_keep_is_not_found(tmp_path):
    async def scenario() -> None:
        hub = channel_hub(tmp_path / "hub", FakeService(), FakeTime())
        async with (
            served(hub) as address,
            aiohttp.ClientSession() as session,
            session.post(url(address, f"/channels/{uuid4()}/fake"), json={}) as answer,
        ):
            assert answer.status == 404

    asyncio.run(scenario())
