"""The Discord channel kind, against a local app standing in for Discord's API, and signed
interactions from an Ed25519 test key pair."""

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import aiohttp
import pytest
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from kinby.cli.client import ContractClient
from kinby.contracts import (
    CHANNEL_CREATE,
    CHANNEL_TEST,
    Channel,
    ChannelCreateCommand,
    ChannelTestCommand,
    SetupFieldKind,
    SetupValue,
)
from kinby.hub import Hub
from kinby.hub.channels import ChannelNotification, ChannelUser, SentRef, installed_kinds
from kinby.hub.discord import DiscordBot, DiscordRefused
from kinby.instance import Serve
from tests.test_hub import FakeControl, created_instance, hub_at, hub_client
from tests.test_hub_channels import listed_channel, raised, raising, until
from tests.test_hub_server import served, url

_APPLICATION = "1100"
_BOT_TOKEN = "sentinel-fake-discord-bot-token"
_LINK = "https://kinby.example/instances/1/threads/2"
_JORGE = "4200"
_DM = "4300"
_KEY = Ed25519PrivateKey.generate()
_PUBLIC_KEY = _KEY.public_key().public_bytes_raw().hex()


@dataclass
class FakeDiscord:
    """What the stand-in took, as method, path, authorization and body, and whether it refuses
    every call."""

    refusing: bool = False
    calls: list[tuple[str, str, str, dict[str, object]]] = field(default_factory=list)

    async def call(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.calls.append(
            (request.method, request.path, request.headers.get("Authorization", ""), body)
        )
        if self.refusing:
            return web.json_response({"message": "401: Unauthorized", "code": 0}, status=401)
        return web.json_response({"id": str(len(self.calls))})

    def sent(self, method: str, path: str) -> list[dict[str, object]]:
        return [body for called, at, _, body in self.calls if (called, at) == (method, path)]


@asynccontextmanager
async def discord(service: FakeDiscord) -> AsyncIterator[str]:
    application = web.Application()
    application.router.add_route("*", "/api/{path:.*}", service.call)
    runner = web.AppRunner(application)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        yield f"http://127.0.0.1:{site.port}/api"
    finally:
        await runner.cleanup()


def discord_hub(directory: Path) -> Hub:
    """A hub with the kinds kinby installs."""
    return hub_at(directory, control=FakeControl(), public_url="https://kinby.example")


async def created_channel(client: ContractClient, base_url: str) -> Channel:
    created = await client.call(
        CHANNEL_CREATE,
        ChannelCreateCommand(
            name="dms",
            kind="discord",
            settings={
                "application_id": _APPLICATION,
                "public_key": _PUBLIC_KEY,
                "base_url": base_url,
            },
            secrets={"bot_token": _BOT_TOKEN},
        ),
    )
    assert isinstance(created, Channel), created
    return created


async def interaction(
    address: Serve,
    channel: Channel,
    body: Mapping[str, object],
    *,
    key: Ed25519PrivateKey = _KEY,
    status: int = 200,
) -> dict[str, object] | None:
    """Call the channel's route as Discord does, signed with *key*, and return its JSON answer."""
    raw = json.dumps(body).encode()
    timestamp = "1761300000"
    async with (
        aiohttp.ClientSession() as session,
        session.post(
            url(address, f"/channels/{channel.channel_id}/discord"),
            data=raw,
            headers={
                "Content-Type": "application/json",
                "X-Signature-Ed25519": key.sign(timestamp.encode() + raw).hex(),
                "X-Signature-Timestamp": timestamp,
            },
        ) as answer,
    ):
        assert answer.status == status, await answer.text()
        return await answer.json() if status == 200 else None


def pair_command(user: str, channel: str, code: str) -> dict[str, object]:
    return {
        "type": 2,
        "user": {"id": user},
        "channel_id": channel,
        "data": {"name": "pair", "options": [{"name": "code", "type": 3, "value": code}]},
    }


def click(user: str, channel: str, message: str, option: int) -> dict[str, object]:
    return {
        "type": 3,
        "user": {"id": user},
        "channel_id": channel,
        "message": {"id": message},
        "data": {"custom_id": str(option), "component_type": 2},
    }


async def paired(client: ContractClient, address: Serve, base_url: str) -> Channel:
    """A channel Jorge paired by sending the bot its code from his DM with it."""
    channel = await created_channel(client, base_url)
    await interaction(address, channel, pair_command(_JORGE, _DM, str(channel.pairing_code)))
    return await listed_channel(client, channel.channel_id)


def bot(base_url: str) -> DiscordBot:
    settings: dict[str, SetupValue] = {
        "application_id": _APPLICATION,
        "public_key": _PUBLIC_KEY,
        "bot_token": _BOT_TOKEN,
        "base_url": base_url,
    }
    return DiscordBot(settings, ChannelUser(f"{_JORGE}/{_DM}"))


def button(label: str, option: int) -> dict[str, object]:
    """A row holding one button, as a message's options are sent."""
    return {
        "type": 1,
        "components": [{"type": 2, "style": 2, "label": label, "custom_id": str(option)}],
    }


def notification(*, options: tuple[str, ...] = ()) -> ChannelNotification:
    return ChannelNotification(
        notification_id=UUID(int=7),
        header="Kinby · Daily news",
        title="The news failed",
        text="The feed answered 500 twice.",
        link=_LINK,
        options=options,
    )


def test_kinby_registers_the_discord_kind_through_its_entry_point_group():
    kind = installed_kinds()["discord"]

    assert kind is DiscordBot
    assert kind.setup.pairs
    assert [(setting.name, setting.kind) for setting in kind.setup.settings] == [
        ("application_id", SetupFieldKind.CONFIG),
        ("public_key", SetupFieldKind.CONFIG),
        ("bot_token", SetupFieldKind.SECRET),
        ("base_url", SetupFieldKind.CONFIG),
    ]
    descriptions = " ".join(setting.description for setting in kind.setup.settings)
    assert "Interactions endpoint URL" in descriptions
    assert "share a server" in descriptions


def test_creating_a_channel_gives_the_bot_a_pair_command_for_its_dms(tmp_path):
    async def scenario() -> None:
        service = FakeDiscord()
        async with discord(service) as base_url:
            await created_channel(hub_client(discord_hub(tmp_path / "hub")), base_url)

        [(method, path, authorization, command)] = service.calls
        assert (method, path) == ("POST", f"/api/applications/{_APPLICATION}/commands")
        assert authorization == f"Bot {_BOT_TOKEN}"
        assert command["name"] == "pair"
        assert command["contexts"] == [1]
        options = command["options"]
        assert isinstance(options, list)
        assert [(option["name"], option["required"]) for option in options] == [("code", True)]

    asyncio.run(scenario())


def test_sending_the_code_from_a_dm_pairs_the_channel_with_that_dm(tmp_path):
    async def scenario() -> None:
        service = FakeDiscord()
        async with discord(service) as base_url:
            hub = discord_hub(tmp_path / "hub")
            client = hub_client(hub)
            channel = await created_channel(client, base_url)
            code = str(channel.pairing_code)
            async with served(hub) as address:
                await interaction(address, channel, pair_command("7", "8", "wrong"))
                unpaired = await listed_channel(client, channel.channel_id)
                answer = await interaction(address, channel, pair_command(_JORGE, _DM, code))
            await client.call(CHANNEL_TEST, ChannelTestCommand(channel_id=channel.channel_id))

        assert not unpaired.paired
        assert answer is not None
        assert answer["type"] == 4
        [test] = service.sent("POST", f"/api/channels/{_DM}/messages")
        assert test["components"] == []

    asyncio.run(scenario())


def test_a_click_from_the_paired_user_is_a_reply_and_the_message_is_edited_to_its_outcome(
    tmp_path,
):
    async def scenario() -> None:
        service = FakeDiscord()
        async with discord(service) as base_url:
            hub = discord_hub(tmp_path / "hub")
            client = hub_client(hub)
            alice_id = (await created_instance(client)).instance_id
            async with served(hub) as address:
                channel = await paired(client, address, base_url)
                await raised(raising(hub, alice_id), "news")
                messages = f"/api/channels/{_DM}/messages"
                await until(lambda: len(service.sent("POST", messages)) == 1)
                message = str(len(service.calls))
                stranger = await interaction(address, channel, click("7", "8", message, 1))
                answer = await interaction(address, channel, click(_JORGE, _DM, message, 1))
                edited = f"{messages}/{message}"
                await until(lambda: len(service.sent("PATCH", edited)) == 1)
            muted = await listed_channel(client, channel.channel_id)

        [sent] = service.sent("POST", messages)
        assert sent["components"] == [button("Rerun", 0), button("Mute this instance", 1)]
        assert stranger == answer == {"type": 6}
        assert service.sent("PATCH", edited) == [
            {"content": "Muted this instance on this channel.", "embeds": [], "components": []}
        ]
        assert muted.muted == [alice_id]

    asyncio.run(scenario())


def test_an_interaction_with_a_bad_signature_is_refused_and_a_ping_gets_a_pong(tmp_path):
    async def scenario() -> None:
        async with discord(FakeDiscord()) as base_url:
            hub = discord_hub(tmp_path / "hub")
            client = hub_client(hub)
            channel = await created_channel(client, base_url)
            code = str(channel.pairing_code)
            async with served(hub) as address:
                forged = Ed25519PrivateKey.generate()
                await interaction(
                    address, channel, pair_command(_JORGE, _DM, code), key=forged, status=401
                )
                pong = await interaction(address, channel, {"type": 1})
            unpaired = await listed_channel(client, channel.channel_id)

        assert pong == {"type": 1}
        assert not unpaired.paired

    asyncio.run(scenario())


def test_a_notification_is_sent_to_the_dm_as_an_embed_with_its_options_as_buttons():
    async def scenario() -> None:
        service = FakeDiscord()
        async with discord(service) as base_url:
            sent = await bot(base_url).send(notification(options=("Approve", "Deny")))

        assert sent == "1"
        [(method, path, authorization, body)] = service.calls
        assert (method, path) == ("POST", f"/api/channels/{_DM}/messages")
        assert authorization == f"Bot {_BOT_TOKEN}"
        assert body["embeds"] == [
            {
                "author": {"name": "Kinby · Daily news"},
                "title": "The news failed",
                "description": "The feed answered 500 twice.",
                "url": _LINK,
            }
        ]
        assert body["components"] == [button("Approve", 0), button("Deny", 1)]

    asyncio.run(scenario())


def test_an_error_from_discord_raises_with_its_answer():
    async def scenario() -> None:
        async with discord(FakeDiscord(refusing=True)) as base_url:
            with pytest.raises(DiscordRefused, match="Unauthorized"):
                await bot(base_url).send(notification())

    asyncio.run(scenario())


def test_a_long_outcome_is_cut_to_what_one_discord_message_holds():
    async def scenario() -> None:
        service = FakeDiscord()
        async with discord(service) as base_url:
            await bot(base_url).edit(SentRef("9"), f'Answered "{"o" * 3_000}".')

        [edited] = service.sent("PATCH", f"/api/channels/{_DM}/messages/9")
        content = str(edited["content"])
        assert len(content) <= 2_000
        assert content.startswith('Answered "ooo')

    asyncio.run(scenario())
