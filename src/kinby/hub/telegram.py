"""The Telegram channel kind: a bot that messages the user and takes the options they tap
(ADR 0103)."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from html import escape
from typing import ClassVar

import aiohttp
from aiohttp import web

from kinby.contracts import SetupField, SetupFieldKind, SetupFieldType, SetupValue
from kinby.hub.channels import (
    ChannelNotification,
    ChannelSetup,
    ChannelUser,
    Inbound,
    Reply,
    SentRef,
)

__all__ = ["TelegramBot", "TelegramRefused"]

_CALL_TIMEOUT_SECONDS = 30
_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
#: The most characters one message shows, counted once Telegram has parsed its HTML.
_MESSAGE_CHARACTERS = 4_096
#: The most characters of the header, and of the title, a message shows.
_HEADING_CHARACTERS = 256
_LINK_TEXT = "Open in kinby"


class TelegramRefused(Exception):
    """Telegram answered a call with an error status."""


class TelegramBot:
    """A bot the user pairs with by sending it the code. It messages that user's private chat,
    whose id is the user's."""

    setup: ClassVar[ChannelSetup] = ChannelSetup(
        settings=(
            SetupField(
                name="bot_token",
                label="Bot token",
                description="The token @BotFather gave the bot.",
                kind=SetupFieldKind.SECRET,
                type=SetupFieldType.TEXT,
                required=True,
            ),
            SetupField(
                name="base_url",
                label="Bot API URL",
                description="Where Telegram's Bot API answers.",
                kind=SetupFieldKind.CONFIG,
                type=SetupFieldType.URL,
                required=False,
                default="https://api.telegram.org",
            ),
        ),
        pairs=True,
        # The text's share of a message, once the header, title and link take theirs.
        message_limit=3_500,
    )

    def __init__(self, settings: Mapping[str, SetupValue], paired_user: ChannelUser | None) -> None:
        self._token = str(settings["bot_token"])
        self._base_url = str(settings["base_url"])
        self._chat = paired_user
        # Derived rather than kept, so a new bot token gets a new one when it sets the webhook.
        self._secret_token = hmac.new(
            self._token.encode(), b"kinby webhook", hashlib.sha256
        ).hexdigest()

    async def register(self, route: str) -> None:
        """Set the bot's webhook to *route*, with the secret token Telegram sends back on it."""
        await self._call(
            "setWebhook",
            {
                "url": route,
                "secret_token": self._secret_token,
                "allowed_updates": ["message", "callback_query"],
            },
        )

    async def send(self, notification: ChannelNotification) -> SentRef:
        """Send the notification as one message, with a button for each option."""
        body: dict[str, object] = {
            "chat_id": self._chat,
            "text": _html(notification),
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if notification.options:
            body["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": option, "callback_data": str(index)}]
                    for index, option in enumerate(notification.options)
                ]
            }
        match await self._call("sendMessage", body):
            case {"message_id": int() as message_id}:
                return SentRef(str(message_id))
            case sent:
                raise TelegramRefused(f"Telegram sent no message id: {sent!r}")

    async def edit(self, message: SentRef, notification: ChannelNotification, outcome: str) -> None:
        """Write the message again with the outcome under it. Sent without a keyboard, it has no
        buttons left."""
        await self._call(
            "editMessageText",
            {
                "chat_id": self._chat,
                "message_id": int(message),
                "text": _html(notification, outcome),
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            },
        )

    async def receive(self, request: web.Request) -> Inbound | Reply | None:
        """The text the user messaged the bot, or the button they tapped. A call without the
        webhook's secret token is not Telegram's."""
        if not hmac.compare_digest(
            request.headers.get(_SECRET_HEADER, "").encode(), self._secret_token.encode()
        ):
            return None
        match await request.json():
            case {
                "callback_query": {
                    "from": {"id": int() as sender},
                    "message": {"message_id": int() as message},
                    "data": str() as data,
                }
            } if data.isdigit():
                return Reply(
                    sender=ChannelUser(str(sender)), message=SentRef(str(message)), option=int(data)
                )
            case {"message": {"from": {"id": int() as sender}, "text": str() as text}}:
                return Inbound(sender=ChannelUser(str(sender)), text=text)
        return None

    def answer(self, received: Inbound | Reply | None) -> web.Response:
        return web.Response()

    async def _call(self, method: str, body: Mapping[str, object]) -> object:
        async with (
            aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=_CALL_TIMEOUT_SECONDS)
            ) as session,
            session.post(f"{self._base_url}/bot{self._token}/{method}", json=body) as answer,
        ):
            if answer.status >= 400:
                raise TelegramRefused(f"Telegram answered {answer.status}: {await answer.text()}")
            return (await answer.json())["result"]


def _html(notification: ChannelNotification, outcome: str | None = None) -> str:
    """The message, cut to what one message shows: the header, title and outcome to their share,
    and the text to what they and the link leave."""
    header = _cut(notification.header, _HEADING_CHARACTERS)
    title = _cut(notification.title, _HEADING_CHARACTERS)
    answered = _cut(outcome, _HEADING_CHARACTERS) if outcome is not None else ""
    separators = 7
    room = (
        _MESSAGE_CHARACTERS
        - len(header)
        - len(title)
        - len(_LINK_TEXT)
        - len(answered)
        - separators
    )
    parts = [
        f"<b>{escape(header)}</b>\n<b>{escape(title)}</b>",
        escape(_cut(notification.text, room)),
    ]
    if notification.link is not None:
        parts.append(f'<a href="{escape(notification.link)}">{_LINK_TEXT}</a>')
    if outcome is not None:
        parts.append(f"<i>{escape(answered)}</i>")
    return "\n\n".join(parts)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"
