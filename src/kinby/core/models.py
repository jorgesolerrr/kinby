"""Create chat models from ``provider:model`` names."""

from __future__ import annotations

from collections.abc import Callable
from functools import cached_property

from langchain.chat_models import init_chat_model
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from kinby.instance import ModelName

_ANTHROPIC_PROVIDER = "anthropic"

#: Counts a text's tokens with a model's provider tokenizer.
Tokenizer = Callable[[ModelName, str], int]


def _split_model_name(name: ModelName) -> tuple[str, str]:
    provider, _, model = name.partition(":")
    return provider, model


def is_anthropic_model(name: ModelName) -> bool:
    provider, _ = _split_model_name(name)
    return provider == _ANTHROPIC_PROVIDER


class _ProfileChatAnthropic(ChatAnthropic):
    """Let the Anthropic SDK find an ``ant auth login`` profile when no API key is set.

    ChatAnthropic passes an empty key to the SDK, which then skips its credential chain.
    """

    @cached_property
    def _client_params(self) -> dict[str, object]:
        params = dict(super()._client_params)
        if not params.get("api_key"):
            params["api_key"] = None
        return params


def init_model(name: ModelName) -> BaseChatModel:
    provider, model = _split_model_name(name)
    if provider == _ANTHROPIC_PROVIDER:
        return _ProfileChatAnthropic(model=model)
    return init_chat_model(name)


def count_tokens(name: ModelName, text: str) -> int:
    """Count the tokens *text* adds to a system prompt with *name*'s provider tokenizer.

    Anthropic counts through its count endpoint, and OpenAI locally with tiktoken. Both count
    whole messages, so the text's share is how much a system prompt grows when it joins.
    """
    model = init_model(name)
    question = HumanMessage(".")
    without = model.get_num_tokens_from_messages([SystemMessage("."), question])
    joined = model.get_num_tokens_from_messages([SystemMessage(f".\n\n{text}"), question])
    return joined - without
