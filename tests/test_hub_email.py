"""The email channel kind, against a local app standing in for Resend."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from html.parser import HTMLParser
from uuid import UUID

import pytest
from aiohttp import web

from kinby.contracts import SetupValue
from kinby.hub.channels import ChannelNotification, installed_kinds
from kinby.hub.email import ResendEmail, ResendRefused

_KEY = "re_sentinel-fake-key"
_LINK = "https://kinby.example/instances/1/threads/2"


@dataclass
class FakeResend:
    """What the stand-in took, and the status and body it answers with."""

    status: int = 200
    answer: dict[str, object] = field(default_factory=lambda: {"id": "email-1"})
    requests: list[tuple[dict[str, str], dict[str, object]]] = field(default_factory=list)

    async def emails(self, request: web.Request) -> web.Response:
        self.requests.append((dict(request.headers), await request.json()))
        return web.json_response(self.answer, status=self.status)


@asynccontextmanager
async def resend(service: FakeResend) -> AsyncIterator[str]:
    application = web.Application()
    application.router.add_post("/emails", service.emails)
    runner = web.AppRunner(application)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        yield f"http://127.0.0.1:{site.port}"
    finally:
        await runner.cleanup()


def email(base_url: str) -> ResendEmail:
    settings: dict[str, SetupValue] = {
        "api_key": _KEY,
        "from": "kinby@example.com",
        "to": "jorge@example.com",
        "base_url": base_url,
    }
    return ResendEmail(settings, None)


def notification(*, options: tuple[str, ...] = (), link: str | None = _LINK) -> ChannelNotification:
    return ChannelNotification(
        notification_id=UUID(int=7),
        header="Kinby · Daily news",
        title="The news failed",
        text="The feed answered 500 <twice>.",
        link=link,
        options=options,
    )


class Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.hrefs.extend(value or "" for name, value in attrs if name == "href")


def hrefs(html: object) -> list[str]:
    assert isinstance(html, str), html
    links = Links()
    links.feed(html)
    return links.hrefs


def test_kinby_registers_the_email_kind_through_its_entry_point_group():
    kind = installed_kinds()["email"]

    assert kind is ResendEmail
    assert not kind.setup.pairs
    assert not hasattr(kind, "receive")


def test_the_send_carries_resends_idempotency_key_and_the_notifications_link():
    async def scenario() -> None:
        service = FakeResend()
        async with resend(service) as base_url:
            sent = await email(base_url).send(notification())

        assert sent == "email-1"
        [(headers, body)] = service.requests
        assert headers["Authorization"] == f"Bearer {_KEY}"
        assert headers["Idempotency-Key"] == f"{UUID(int=7)}/jorge@example.com"
        assert body["from"] == "kinby@example.com"
        assert body["to"] == ["jorge@example.com"]
        assert body["subject"] == "Kinby · Daily news: The news failed"
        assert "The feed answered 500 &lt;twice&gt;." in str(body["html"])
        assert hrefs(body["html"]) == [_LINK]

    asyncio.run(scenario())


def test_options_render_as_links_into_the_web_app_and_never_as_acting_links():
    async def scenario() -> None:
        service = FakeResend()
        async with resend(service) as base_url:
            await email(base_url).send(notification(options=("Approve", "Deny")))

        [(_, body)] = service.requests
        assert hrefs(body["html"]) == [_LINK, _LINK, _LINK]
        assert ">Approve</a>" in str(body["html"])
        assert ">Deny</a>" in str(body["html"])

    asyncio.run(scenario())


def test_without_the_hubs_public_url_options_are_plain_text_and_nothing_links():
    async def scenario() -> None:
        service = FakeResend()
        async with resend(service) as base_url:
            await email(base_url).send(notification(options=("Approve",), link=None))

        [(_, body)] = service.requests
        assert hrefs(body["html"]) == []
        assert "Answer in kinby: Approve" in str(body["html"])

    asyncio.run(scenario())


def test_an_error_status_from_resend_raises_with_its_message():
    async def scenario() -> None:
        service = FakeResend(
            status=422,
            answer={"statusCode": 422, "name": "validation_error", "message": "Bad from."},
        )
        async with resend(service) as base_url:
            with pytest.raises(ResendRefused, match="Bad from"):
                await email(base_url).send(notification())

    asyncio.run(scenario())
