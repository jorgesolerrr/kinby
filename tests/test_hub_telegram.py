"""The Telegram channel kind, against a local app standing in for Telegram's Bot API."""

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import aiohttp
import pytest
from aiohttp import web

from kinby.cli.client import ContractClient
from kinby.contracts import (
    CHANNEL_CREATE,
    CHANNEL_LIST,
    CHANNEL_TEST,
    CHANNEL_UPDATE,
    NOTIFICATION_LIST,
    Channel,
    ChannelCreateCommand,
    ChannelListCommand,
    ChannelListResult,
    ChannelTestCommand,
    ChannelUpdateCommand,
    ErrorCode,
    ErrorEnvelope,
    NotificationListCommand,
    NotificationListResult,
    SetupFieldKind,
    SetupValue,
)
from kinby.hub import Hub
from kinby.hub.channels import ChannelNotification, ChannelUser, installed_kinds
from kinby.hub.telegram import TelegramBot, TelegramRefused
from kinby.instance import Serve
from tests.test_hub import FakeControl, created_instance, hub_at, hub_client
from tests.test_hub_channels import listed_channel, raised, raising, until
from tests.test_hub_server import served, url

_BOT_TOKEN = "123456:sentinel-fake-bot-token"
_PUBLIC_URL = "https://kinby.example"
_LINK = "https://kinby.example/instances/1/threads/2"
_JORGE = 42
_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


@dataclass
class FakeTelegram:
    """What the stand-in took, by bot token and method, and whether it refuses every call."""

    refusing: bool = False
    calls: list[tuple[str, str, dict[str, object]]] = field(default_factory=list)

    async def method(self, request: web.Request) -> web.Response:
        token, method = request.match_info["token"], request.match_info["method"]
        self.calls.append((token, method, await request.json()))
        if self.refusing:
            return web.json_response(
                {"ok": False, "error_code": 401, "description": "Unauthorized"}, status=401
            )
        result: object = True
        if method == "sendMessage":
            result = {"message_id": len(self.sent())}
        return web.json_response({"ok": True, "result": result})

    def sent(self, method: str = "sendMessage") -> list[dict[str, object]]:
        return [body for _, called, body in self.calls if called == method]


@asynccontextmanager
async def telegram(service: FakeTelegram) -> AsyncIterator[str]:
    application = web.Application()
    application.router.add_post("/bot{token}/{method}", service.method)
    runner = web.AppRunner(application)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        yield f"http://127.0.0.1:{site.port}"
    finally:
        await runner.cleanup()


def telegram_hub(directory: Path, *, public_url: str | None = _PUBLIC_URL) -> Hub:
    """A hub with the kinds kinby installs."""
    return hub_at(directory, control=FakeControl(), public_url=public_url)


async def created_channel(client: ContractClient, base_url: str) -> Channel:
    created = await client.call(CHANNEL_CREATE, creation(base_url))
    assert isinstance(created, Channel), created
    return created


def creation(base_url: str) -> ChannelCreateCommand:
    return ChannelCreateCommand(
        name="phone",
        kind="telegram",
        settings={"base_url": base_url},
        secrets={"bot_token": _BOT_TOKEN},
    )


async def updated(
    address: Serve, channel: Channel, update: Mapping[str, object], secret_token: object
) -> None:
    """Call the channel's route as Telegram does with the secret token its webhook was set with."""
    async with (
        aiohttp.ClientSession() as session,
        session.post(
            url(address, f"/channels/{channel.channel_id}/telegram"),
            json=update,
            headers={_SECRET_HEADER: str(secret_token)},
        ) as answer,
    ):
        assert answer.status == 200, await answer.text()


def message(sender: int, text: str) -> dict[str, object]:
    return {"update_id": 1, "message": {"message_id": 9, "from": {"id": sender}, "text": text}}


def tap(sender: int, message_id: object, option: int) -> dict[str, object]:
    return {
        "update_id": 2,
        "callback_query": {
            "id": "query",
            "from": {"id": sender},
            "message": {"message_id": message_id, "chat": {"id": sender}},
            "data": str(option),
        },
    }


async def paired(
    client: ContractClient, address: Serve, service: FakeTelegram, base_url: str
) -> tuple[Channel, object]:
    """A channel Jorge paired by messaging the bot its code, and its webhook's secret token."""
    channel = await created_channel(client, base_url)
    [webhook] = service.sent("setWebhook")
    await updated(
        address, channel, message(_JORGE, str(channel.pairing_code)), webhook["secret_token"]
    )
    return await listed_channel(client, channel.channel_id), webhook["secret_token"]


def bot(base_url: str) -> TelegramBot:
    settings: dict[str, SetupValue] = {"bot_token": _BOT_TOKEN, "base_url": base_url}
    return TelegramBot(settings, ChannelUser(str(_JORGE)))


def notification(*, options: tuple[str, ...] = ()) -> ChannelNotification:
    return ChannelNotification(
        notification_id=UUID(int=7),
        header="Kinby · Daily news",
        title="The news failed",
        text="The feed answered 500 <twice>.",
        link=_LINK,
        options=options,
    )


def test_kinby_registers_the_telegram_kind_through_its_entry_point_group():
    kind = installed_kinds()["telegram"]

    assert kind is TelegramBot
    assert kind.setup.pairs
    assert [(setting.name, setting.kind) for setting in kind.setup.settings] == [
        ("bot_token", SetupFieldKind.SECRET),
        ("base_url", SetupFieldKind.CONFIG),
    ]


def test_creating_a_channel_sets_the_bots_webhook_to_its_route_with_a_secret_token(tmp_path):
    async def scenario() -> None:
        service = FakeTelegram()
        async with telegram(service) as base_url:
            hub = telegram_hub(tmp_path / "hub")
            channel = await created_channel(hub_client(hub), base_url)

        [(token, method, body)] = service.calls
        assert (token, method) == (_BOT_TOKEN, "setWebhook")
        assert body["url"] == f"{_PUBLIC_URL}/channels/{channel.channel_id}/telegram"
        assert body["allowed_updates"] == ["message", "callback_query"]
        secret_token = body["secret_token"]
        assert isinstance(secret_token, str)
        assert len(secret_token) >= 32
        assert _BOT_TOKEN not in secret_token

    asyncio.run(scenario())


def test_a_new_bot_token_sets_the_webhook_again_and_a_rename_does_not(tmp_path):
    async def scenario() -> None:
        service = FakeTelegram()
        async with telegram(service) as base_url:
            client = hub_client(telegram_hub(tmp_path / "hub"))
            channel = await created_channel(client, base_url)
            await client.call(
                CHANNEL_UPDATE, ChannelUpdateCommand(channel_id=channel.channel_id, name="mobile")
            )
            await client.call(
                CHANNEL_UPDATE,
                ChannelUpdateCommand(
                    channel_id=channel.channel_id, secrets={"bot_token": "654321:another-token"}
                ),
            )

        first, second = service.sent("setWebhook")
        assert [token for token, _, _ in service.calls] == [_BOT_TOKEN, "654321:another-token"]
        assert second["url"] == first["url"]
        assert second["secret_token"] != first["secret_token"]

    asyncio.run(scenario())


def test_no_channel_is_created_when_telegram_refuses_the_webhook_or_the_hub_has_no_public_url(
    tmp_path,
):
    async def scenario() -> None:
        service = FakeTelegram(refusing=True)
        async with telegram(service) as base_url:
            refused = telegram_hub(tmp_path / "refused")
            refusal = await hub_client(refused).call(CHANNEL_CREATE, creation(base_url))
            unreachable = telegram_hub(tmp_path / "unreachable", public_url=None)
            no_route = await hub_client(unreachable).call(CHANNEL_CREATE, creation(base_url))

        assert isinstance(refusal, ErrorEnvelope), refusal
        assert refusal.code is ErrorCode.INVALID_SETUP
        assert "Unauthorized" in str(refusal.fields)
        assert isinstance(no_route, ErrorEnvelope), no_route
        assert no_route.code is ErrorCode.INVALID_SETUP
        assert "public URL" in str(no_route.fields)
        assert [method for _, method, _ in service.calls] == ["setWebhook"]
        for hub in (refused, unreachable):
            listed = await hub_client(hub).call(CHANNEL_LIST, ChannelListCommand())
            assert listed == ChannelListResult(channels=[])
            assert hub.secrets.values() == {}

    asyncio.run(scenario())


def test_messaging_the_bot_its_code_pairs_the_channel_with_that_chat(tmp_path):
    async def scenario() -> None:
        service = FakeTelegram()
        async with telegram(service) as base_url:
            hub = telegram_hub(tmp_path / "hub")
            client = hub_client(hub)
            channel = await created_channel(client, base_url)
            [webhook] = service.sent("setWebhook")
            code = str(channel.pairing_code)
            async with served(hub) as address:
                await updated(address, channel, message(7, code), "forged")
                await updated(address, channel, {"update_id": 3}, webhook["secret_token"])
                unpaired = await listed_channel(client, channel.channel_id)
                await updated(address, channel, message(_JORGE, code), webhook["secret_token"])
            await client.call(CHANNEL_TEST, ChannelTestCommand(channel_id=channel.channel_id))

        assert not unpaired.paired
        [test] = service.sent()
        assert test["chat_id"] == str(_JORGE)
        assert "reply_markup" not in test

    asyncio.run(scenario())


def test_a_tap_from_the_paired_user_is_a_reply_and_the_message_is_edited_to_its_outcome(tmp_path):
    async def scenario() -> None:
        service = FakeTelegram()
        async with telegram(service) as base_url:
            hub = telegram_hub(tmp_path / "hub")
            client = hub_client(hub)
            alice_id = (await created_instance(client)).instance_id
            async with served(hub) as address:
                channel, secret_token = await paired(client, address, service, base_url)
                await raised(raising(hub, alice_id), "news")
                await until(lambda: len(service.sent()) == 1)
                [sent] = service.sent()
                await updated(address, channel, tap(7, 1, 1), secret_token)
                await updated(address, channel, tap(_JORGE, 1, 1), "forged")
                await updated(address, channel, tap(_JORGE, 1, 1), secret_token)
                await until(lambda: len(service.sent("editMessageText")) == 1)
            muted = await listed_channel(client, channel.channel_id)
            listed = await client.call(
                NOTIFICATION_LIST, NotificationListCommand(instance_id=alice_id)
            )

        assert sent["reply_markup"] == {
            "inline_keyboard": [
                [{"text": "Rerun", "callback_data": "0"}],
                [{"text": "Mute this instance", "callback_data": "1"}],
            ]
        }
        assert service.sent("editMessageText") == [
            {
                "chat_id": str(_JORGE),
                "message_id": 1,
                "text": "Muted this instance on this channel.",
            }
        ]
        assert muted.muted == [alice_id]
        assert isinstance(listed, NotificationListResult), listed
        [news] = listed.notifications
        assert len(news.replies) == 1

    asyncio.run(scenario())


def test_a_notification_is_sent_as_html_with_its_link_and_its_options_as_buttons():
    async def scenario() -> None:
        service = FakeTelegram()
        async with telegram(service) as base_url:
            sent = await bot(base_url).send(notification(options=("Approve", "Deny")))

        assert sent == "1"
        [(token, method, body)] = service.calls
        assert (token, method) == (_BOT_TOKEN, "sendMessage")
        assert body["chat_id"] == str(_JORGE)
        assert body["parse_mode"] == "HTML"
        text = str(body["text"])
        assert "<b>Kinby · Daily news</b>" in text
        assert "The feed answered 500 &lt;twice&gt;." in text
        assert f'<a href="{_LINK}">Open in kinby</a>' in text
        assert body["reply_markup"] == {
            "inline_keyboard": [
                [{"text": "Approve", "callback_data": "0"}],
                [{"text": "Deny", "callback_data": "1"}],
            ]
        }

    asyncio.run(scenario())


def test_an_error_from_telegram_raises_with_its_answer():
    async def scenario() -> None:
        async with telegram(FakeTelegram(refusing=True)) as base_url:
            with pytest.raises(TelegramRefused, match="Unauthorized"):
                await bot(base_url).send(notification())

    asyncio.run(scenario())
