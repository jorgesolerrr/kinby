"""Model construction from provider:model names."""

from collections.abc import Sequence

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage

from kinby.core.models import count_tokens, init_model


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


def test_a_count_holds_only_the_tokens_the_text_adds_to_a_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def count_endpoint(self: ChatAnthropic, messages: Sequence[BaseMessage]) -> int:
        """Charge each message a framing of 4, then one token per character."""
        return sum(4 + len(str(message.content)) for message in messages)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(ChatAnthropic, "get_num_tokens_from_messages", count_endpoint)

    assert count_tokens("anthropic:claude-opus-5", "Prefers tea.") == len("\n\nPrefers tea.")
