"""The web_search default tool: each search backend's request and results, and when the tool is
offered to a turn."""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import AIMessageChunk

from kinby.contracts import ApprovalRequested, ToolResult
from kinby.core import LangGraphRunner
from kinby.instance import Instance, load_instance
from kinby.plugins import ToolContext
from kinby.plugins.defaults.web import web_search
from tests.test_gate import _run_turn
from tests.test_plugins import ScriptedModel

_MODEL = "openai:gpt-5"
_KEYS = ("BRAVE_API_KEY", "TAVILY_API_KEY", "EXA_API_KEY", "SEARXNG_URL")


def _instance(tmp_path: Path, tools: str = "") -> Instance:
    instance_path = tmp_path / "instance"
    instance_path.mkdir()
    (instance_path / "workspace").mkdir()
    (instance_path / "kinby.toml").write_text(
        f'id = "test"\n\n[models]\nmain = "{_MODEL}"\n\n[tools]\n{tools}', encoding="utf-8"
    )
    (instance_path / "permissions.toml").write_text('mode = "ask"\n', encoding="utf-8")
    return load_instance(instance_path)


@pytest.fixture(autouse=True)
def _no_search_keys(monkeypatch) -> None:
    for variable in _KEYS:
        monkeypatch.delenv(variable, raising=False)


def _fake_http(monkeypatch, body: object) -> list[httpx.Request]:
    """Answer every request with *body* as JSON, and keep each request."""
    sent: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs),
    )
    return sent


async def _search(instance: Instance) -> str:
    return await web_search.ainvoke(
        {"query": "kinby", "count": 3, "domains": ["github.com", "example.org"]},
        ToolContext(instance=instance, thread_id=uuid4()),
    )


_EXPECTED = "Kinby\nhttps://github.com/jorgesolerrr/kinby\nA personal AI teammate."


def _brave(request: httpx.Request) -> None:
    assert request.method == "GET"
    assert request.url.copy_with(query=None) == "https://api.search.brave.com/res/v1/web/search"
    assert request.headers["X-Subscription-Token"] == "secret-key"
    assert request.url.params["q"] == "kinby site:github.com OR site:example.org"
    assert request.url.params["count"] == "3"


def _tavily(request: httpx.Request) -> None:
    assert request.method == "POST"
    assert request.url == "https://api.tavily.com/search"
    assert request.headers["Authorization"] == "Bearer secret-key"
    assert json.loads(request.content) == {
        "query": "kinby",
        "max_results": 3,
        "include_domains": ["github.com", "example.org"],
    }


def _exa(request: httpx.Request) -> None:
    assert request.method == "POST"
    assert request.url == "https://api.exa.ai/search"
    assert request.headers["x-api-key"] == "secret-key"
    assert json.loads(request.content) == {
        "query": "kinby",
        "numResults": 3,
        "includeDomains": ["github.com", "example.org"],
        "contents": {"highlights": True},
    }


def _searxng(request: httpx.Request) -> None:
    assert request.method == "GET"
    assert request.url.copy_with(query=None) == "https://search.example.net/search"
    assert request.url.params["q"] == "kinby site:github.com OR site:example.org"
    assert request.url.params["format"] == "json"


_RESULT = {"title": "Kinby", "url": "https://github.com/jorgesolerrr/kinby"}
_OTHER = {"title": "Other", "url": "https://example.org"}


@pytest.mark.parametrize(
    ("setting", "variable", "key", "body", "check"),
    [
        (
            'search = "brave"\n',
            "BRAVE_API_KEY",
            "secret-key",
            {"web": {"results": [{**_RESULT, "description": "A personal AI teammate."}]}},
            _brave,
        ),
        (
            'search = "tavily"\n',
            "TAVILY_API_KEY",
            "secret-key",
            {"results": [{**_RESULT, "content": "A personal AI teammate."}]},
            _tavily,
        ),
        (
            'search = "exa"\n',
            "EXA_API_KEY",
            "secret-key",
            {"results": [{**_RESULT, "highlights": ["A personal", "AI teammate."]}]},
            _exa,
        ),
        (
            'search = "searxng"\n',
            "SEARXNG_URL",
            "https://search.example.net/",
            # SearXNG takes no count, so the tool keeps the first results itself.
            {"results": [{**_RESULT, "content": "A personal AI teammate."}] + [_OTHER] * 3},
            _searxng,
        ),
        (
            "",
            "BRAVE_API_KEY",
            "secret-key",
            {"web": {"results": [{**_RESULT, "description": "A personal AI teammate."}]}},
            _brave,
        ),
    ],
    ids=["brave", "tavily", "exa", "searxng", "brave-by-default"],
)
def test_each_backend_sends_its_request_to_its_own_host_and_returns_title_url_and_snippet(
    tmp_path,
    monkeypatch,
    setting: str,
    variable: str,
    key: str,
    body: object,
    check: Callable[[httpx.Request], None],
) -> None:
    instance = _instance(tmp_path, setting)
    monkeypatch.setenv(variable, key)
    sent = _fake_http(monkeypatch, body)

    output = asyncio.run(_search(instance))

    assert len(sent) == 1
    check(sent[0])
    assert output.split("\n\n")[0] == _EXPECTED
    assert len(output.split("\n\n")) == (3 if variable == "SEARXNG_URL" else 1)


def test_a_search_with_no_results_says_so(tmp_path, monkeypatch) -> None:
    instance = _instance(tmp_path)
    monkeypatch.setenv("BRAVE_API_KEY", "secret-key")
    _fake_http(monkeypatch, {"web": {"results": []}})

    assert asyncio.run(_search(instance)) == "No results."


def _searching_model() -> ScriptedModel:
    return ScriptedModel(
        [
            AIMessageChunk(
                content="",
                tool_calls=[
                    {
                        "name": "web_search",
                        "args": {"query": "kinby"},
                        "id": "search-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessageChunk(content="Found it."),
        ]
    )


def test_a_turn_in_ask_mode_searches_without_asking(tmp_path, monkeypatch) -> None:
    instance = _instance(tmp_path)
    monkeypatch.setenv("BRAVE_API_KEY", "secret-key")
    _fake_http(monkeypatch, {"web": {"results": [{**_RESULT, "description": "A teammate."}]}})
    model = _searching_model()

    _, payloads = asyncio.run(_run_turn(LangGraphRunner(instance, model_factory=lambda _: model)))

    assert not any(isinstance(payload, ApprovalRequested) for payload in payloads)
    result = next(payload for payload in payloads if isinstance(payload, ToolResult))
    assert (result.name, result.output, result.error) == (
        "web_search",
        "Kinby\nhttps://github.com/jorgesolerrr/kinby\nA teammate.",
        False,
    )


def _turn_tools(instance: Instance) -> list[str]:
    model = ScriptedModel([AIMessageChunk(content="Done")])
    asyncio.run(_run_turn(LangGraphRunner(instance, model_factory=lambda _: model)))
    return [tool.name for tool in model.bound_tools[0]]


_FILES_AND_SHELL = {"bash", "edit", "glob", "grep", "read", "write"}


def test_web_search_is_offered_once_its_backends_key_is_set(tmp_path, monkeypatch) -> None:
    instance = _instance(tmp_path, 'search = "tavily"\n')
    monkeypatch.setenv("BRAVE_API_KEY", "a-key-for-another-backend")
    assert "web_search" not in _turn_tools(instance)

    monkeypatch.setenv("TAVILY_API_KEY", "secret-key")
    assert "web_search" in _turn_tools(instance)


def test_the_manifest_switches_off_web_search_alone(tmp_path, monkeypatch) -> None:
    instance = _instance(tmp_path, 'off = ["web_search"]\n')
    monkeypatch.setenv("BRAVE_API_KEY", "secret-key")

    tools = set(_turn_tools(instance))

    assert "web_search" not in tools
    assert tools >= _FILES_AND_SHELL


def test_switching_defaults_off_drops_web_search_with_the_rest(tmp_path, monkeypatch) -> None:
    instance = _instance(tmp_path, "defaults = false\n")
    monkeypatch.setenv("BRAVE_API_KEY", "secret-key")

    tools = set(_turn_tools(instance))

    assert not tools & {*_FILES_AND_SHELL, "web_search"}
