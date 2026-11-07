"""A hub refusal names what is wrong with each value, for the agent's hub tools and the CLI."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from kinby.cli.client import format_error
from kinby.contracts import CallFrame, ErrorCode, ErrorEnvelope, ErrorFrame
from kinby.plugins.hub import HUB_URL_VARIABLE, call_hub

_NO_SKILL = "No SKILL.md at the repository's root, and none in a folder under skills/."
_TWO_FIELDS = {"url": "Not a URL.", "path": "Not in the repository."}


def invalid(fields: dict[str, str]) -> ErrorEnvelope:
    return ErrorEnvelope(
        code=ErrorCode.INVALID_ARGUMENT,
        message="Some values are invalid.",
        retryable=False,
        fields=fields,
    )


@asynccontextmanager
async def refusing_hub(
    monkeypatch: pytest.MonkeyPatch, refusal: ErrorEnvelope
) -> AsyncIterator[None]:
    """Plays the instance's hub route: refuses each call with *refusal*."""

    async def route(request: web.Request) -> web.StreamResponse:
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        call = CallFrame.model_validate_json(await socket.receive_str())
        await socket.send_str(ErrorFrame(id=call.id, error=refusal).model_dump_json())
        await socket.close()
        return socket

    application = web.Application()
    application.router.add_get("/hub", route)
    runner = web.AppRunner(application)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        monkeypatch.setenv(HUB_URL_VARIABLE, f"ws://127.0.0.1:{site.port}/hub")
        yield
    finally:
        await runner.cleanup()


def refused(monkeypatch: pytest.MonkeyPatch, refusal: ErrorEnvelope) -> str:
    async def scenario() -> str:
        async with refusing_hub(monkeypatch, refusal):
            with pytest.raises(ValueError) as raised:
                await call_hub("library.git.preview", {}, "the read")
        return str(raised.value)

    return asyncio.run(scenario())


def test_a_hub_tool_refusal_names_why_the_hub_refused_the_value(monkeypatch):
    assert refused(monkeypatch, invalid({"address": _NO_SKILL})) == (
        f"The hub refused the read: Some values are invalid. address: {_NO_SKILL}"
    )


def test_a_hub_tool_refusal_names_each_field_in_the_envelopes_order(monkeypatch):
    assert refused(monkeypatch, invalid(_TWO_FIELDS)) == (
        "The hub refused the read: Some values are invalid. "
        "url: Not a URL.; path: Not in the repository."
    )


def test_a_hub_tool_refusal_without_fields_is_the_message_alone(monkeypatch):
    assert refused(monkeypatch, invalid({})) == "The hub refused the read: Some values are invalid."


def test_the_cli_names_each_field_after_the_code_and_message():
    assert format_error(invalid(_TWO_FIELDS)) == (
        "INVALID_ARGUMENT: Some values are invalid. url: Not a URL.; path: Not in the repository."
    )
    assert format_error(invalid({})) == "INVALID_ARGUMENT: Some values are invalid."
