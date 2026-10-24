"""The Discord channel kind: a bot that messages the user in a DM and takes the buttons they
click, through the interactions endpoint the user gives the app (ADR 0103)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import ClassVar

import aiohttp
from aiohttp import web
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from kinby.contracts import SetupField, SetupFieldKind, SetupFieldType, SetupValue
from kinby.hub.channels import (
    ChannelNotification,
    ChannelSetup,
    ChannelUser,
    Inbound,
    Reply,
    SentRef,
)

__all__ = ["DiscordBot", "DiscordRefused"]

_CALL_TIMEOUT_SECONDS = 30
#: The most characters of an embed's author name, and of its title.
_HEADING_CHARACTERS = 256
_LABEL_CHARACTERS = 80
_CONTENT_CHARACTERS = 2_000
# Interaction response types, from Discord's API.
_PONG = 1
_MESSAGE = 4
_DEFERRED_UPDATE = 6
_BOT_DM_CONTEXT = 1
_EPHEMERAL = 1 << 6


class DiscordRefused(Exception):
    """Discord answered a call with an error status."""


class DiscordBot:
    """A bot the user pairs with by sending it the code with its pair command, in their DM with
    it. It knows the user by their id and that DM's, and messages that DM."""

    setup: ClassVar[ChannelSetup] = ChannelSetup(
        settings=(
            SetupField(
                name="application_id",
                label="Application ID",
                description=(
                    "From the app's General Information page in the Discord developer portal. "
                    "Once the channel is added, paste its Interactions endpoint URL on that page. "
                    "The bot must share a server with you to message you."
                ),
                kind=SetupFieldKind.CONFIG,
                type=SetupFieldType.TEXT,
                required=True,
            ),
            SetupField(
                name="public_key",
                label="Public key",
                description="From the same page. It checks that a click came from Discord.",
                kind=SetupFieldKind.CONFIG,
                type=SetupFieldType.TEXT,
                required=True,
            ),
            SetupField(
                name="bot_token",
                label="Bot token",
                description="From the app's Bot page.",
                kind=SetupFieldKind.SECRET,
                type=SetupFieldType.TEXT,
                required=True,
            ),
            SetupField(
                name="base_url",
                label="API URL",
                description="Where Discord's API answers.",
                kind=SetupFieldKind.CONFIG,
                type=SetupFieldType.URL,
                required=False,
                default="https://discord.com/api/v10",
            ),
        ),
        pairs=True,
        # An embed's description.
        message_limit=4_096,
    )

    def __init__(self, settings: Mapping[str, SetupValue], paired_user: ChannelUser | None) -> None:
        self._application = str(settings["application_id"])
        self._public_key = str(settings["public_key"])
        self._token = str(settings["bot_token"])
        self._base_url = str(settings["base_url"])
        self._dm = paired_user.partition("/")[2] if paired_user is not None else None

    async def register(self, route: str) -> None:
        """Give the bot its pair command, which the user sends from their DM with it. The user
        pastes *route* into the developer portal, since Discord checks it as it is set."""
        await self._call(
            "POST",
            f"applications/{self._application}/commands",
            {
                "name": "pair",
                "description": "Pair this DM with kinby.",
                "options": [
                    {
                        "type": 3,
                        "name": "code",
                        "description": "The code kinby's Channels page shows.",
                        "required": True,
                    }
                ],
                "contexts": [_BOT_DM_CONTEXT],
            },
        )

    async def send(self, notification: ChannelNotification) -> SentRef:
        """Send the notification as one embed, with a button for each option."""
        embed: dict[str, object] = {
            "author": {"name": _cut(notification.header, _HEADING_CHARACTERS)},
            "title": _cut(notification.title, _HEADING_CHARACTERS),
            "description": notification.text,
        }
        if notification.link is not None:
            embed["url"] = notification.link
        body = {
            "embeds": [embed],
            "components": [
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 2,
                            "style": 2,
                            "label": _cut(option, _LABEL_CHARACTERS),
                            "custom_id": str(index),
                        }
                    ],
                }
                for index, option in enumerate(notification.options)
            ],
        }
        match await self._call("POST", f"channels/{self._dm}/messages", body):
            case {"id": str() as message_id}:
                return SentRef(message_id)
            case sent:
                raise DiscordRefused(f"Discord sent no message id: {sent!r}")

    async def edit(self, message: SentRef, outcome: str) -> None:
        """Replace the message with the outcome, without its embed or buttons."""
        await self._call(
            "PATCH",
            f"channels/{self._dm}/messages/{message}",
            {
                "content": _cut(outcome, _CONTENT_CHARACTERS),
                "embeds": [],
                "components": [],
            },
        )

    async def receive(self, request: web.Request) -> Inbound | Reply | None:
        """The code the user sent with the pair command, or the button they clicked, from a DM.
        A call without the app's signature is refused, as Discord requires."""
        body = await request.read()
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(self._public_key)).verify(
                bytes.fromhex(request.headers.get("X-Signature-Ed25519", "")),
                request.headers.get("X-Signature-Timestamp", "").encode() + body,
            )
        except (ValueError, InvalidSignature) as exc:
            raise web.HTTPUnauthorized() from exc
        match json.loads(body):
            # A button.
            case {
                "type": 3,
                "user": {"id": str() as sender},
                "channel_id": str() as dm,
                "message": {"id": str() as message},
                "data": {"custom_id": str() as data},
            } if data.isdigit():
                return Reply(
                    sender=ChannelUser(f"{sender}/{dm}"), message=SentRef(message), option=int(data)
                )
            # An application command.
            case {
                "type": 2,
                "user": {"id": str() as sender},
                "channel_id": str() as dm,
                "data": {
                    "name": "pair",
                    "options": [{"name": "code", "value": str() as code}],
                },
            }:
                return Inbound(sender=ChannelUser(f"{sender}/{dm}"), text=code)
        return None

    def answer(self, received: Inbound | Reply | None) -> web.Response:
        """A click gets a deferred update, since the message is edited once the reply is
        applied. The pair command gets a message only the user sees, and a PING its PONG."""
        match received:
            case Reply():
                return web.json_response({"type": _DEFERRED_UPDATE})
            case Inbound():
                return web.json_response(
                    {
                        "type": _MESSAGE,
                        "data": {
                            "content": "Got it. kinby's Channels page shows the channel paired "
                            "once the code matches.",
                            "flags": _EPHEMERAL,
                        },
                    }
                )
        return web.json_response({"type": _PONG})

    async def _call(self, method: str, path: str, body: Mapping[str, object]) -> object:
        async with (
            aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=_CALL_TIMEOUT_SECONDS)
            ) as session,
            session.request(
                method,
                f"{self._base_url}/{path}",
                json=body,
                headers={"Authorization": f"Bot {self._token}"},
            ) as answer,
        ):
            if answer.status >= 400:
                raise DiscordRefused(f"Discord answered {answer.status}: {await answer.text()}")
            return await answer.json()


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"
