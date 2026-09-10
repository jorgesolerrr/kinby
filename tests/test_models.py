"""Model construction from provider:model names."""

import pytest
from langchain_anthropic import ChatAnthropic

from kinby.core.models import init_model


def test_anthropic_model_uses_the_configured_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    model = init_model("anthropic:claude-opus-5")

    assert isinstance(model, ChatAnthropic)
    assert model._client.api_key == "sk-ant-test"


def test_anthropic_model_without_a_key_lets_the_sdk_resolve_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

    model = init_model("anthropic:claude-opus-5")

    assert isinstance(model, ChatAnthropic)
    assert model._client.api_key is None


def test_other_providers_load_through_langchain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    model = init_model("openai:gpt-5")

    assert type(model).__name__ == "ChatOpenAI"
