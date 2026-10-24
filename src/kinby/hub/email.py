"""The email channel kind: notifications sent to the user's inbox through Resend (ADR 0103)."""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
from typing import ClassVar

import aiohttp

from kinby.contracts import SetupField, SetupFieldKind, SetupFieldType, SetupValue
from kinby.hub.channels import ChannelNotification, ChannelSetup, ChannelUser, SentRef

__all__ = ["ResendEmail", "ResendRefused"]

_SEND_TIMEOUT_SECONDS = 30


class ResendRefused(Exception):
    """Resend answered a send with an error status."""


class ResendEmail:
    """Send-only: an email links into the web app, whose session does the acting, so a forwarded
    email can answer nothing."""

    setup: ClassVar[ChannelSetup] = ChannelSetup(
        settings=(
            SetupField(
                name="api_key",
                label="Resend API key",
                description="The key Resend sends with, from resend.com/api-keys.",
                kind=SetupFieldKind.SECRET,
                type=SetupFieldType.TEXT,
                required=True,
            ),
            SetupField(
                name="from",
                label="From address",
                description="The address emails come from, on a domain Resend verified.",
                kind=SetupFieldKind.CONFIG,
                type=SetupFieldType.EMAIL,
                required=True,
            ),
            SetupField(
                name="to",
                label="Your address",
                description="The inbox notifications reach.",
                kind=SetupFieldKind.CONFIG,
                type=SetupFieldType.EMAIL,
                required=True,
            ),
            SetupField(
                name="base_url",
                label="Resend API URL",
                description="Where Resend's API answers.",
                kind=SetupFieldKind.CONFIG,
                type=SetupFieldType.URL,
                required=False,
                default="https://api.resend.com",
            ),
        ),
        pairs=False,
        message_limit=10_000,
    )

    def __init__(self, settings: Mapping[str, SetupValue], paired_user: ChannelUser | None) -> None:
        self._api_key = str(settings["api_key"])
        self._from = str(settings["from"])
        self._to = str(settings["to"])
        self._base_url = str(settings["base_url"])

    async def send(self, notification: ChannelNotification) -> SentRef:
        """Send the notification as one email. A retry Resend already took sends nothing again."""
        async with (
            aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=_SEND_TIMEOUT_SECONDS)
            ) as session,
            session.post(
                f"{self._base_url}/emails",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    # Resend keys idempotency by API key, which two channels can share.
                    "Idempotency-Key": f"{notification.notification_id}/{self._to}",
                },
                json={
                    "from": self._from,
                    "to": [self._to],
                    "subject": f"{notification.header}: {notification.title}",
                    "html": _html(notification),
                },
            ) as answer,
        ):
            if answer.status >= 400:
                raise ResendRefused(f"Resend answered {answer.status}: {await answer.text()}")
            return SentRef((await answer.json())["id"])


def _html(notification: ChannelNotification) -> str:
    """The body: the text, then each option as a link to the page that answers it."""
    paragraphs = [
        f"<p><strong>{escape(notification.header)}</strong></p>",
        f'<p style="white-space: pre-wrap">{escape(notification.text)}</p>',
    ]
    if notification.options:
        options = " · ".join(_linked(option, notification.link) for option in notification.options)
        paragraphs.append(f"<p>Answer in kinby: {options}</p>")
    if notification.link is not None:
        paragraphs.append(f"<p>{_linked('Open in kinby', notification.link)}</p>")
    return "\n".join(paragraphs)


def _linked(text: str, link: str | None) -> str:
    """*text* as a link to *link*, or as plain text while the hub has no public URL."""
    return escape(text) if link is None else f'<a href="{escape(link)}">{escape(text)}</a>'
