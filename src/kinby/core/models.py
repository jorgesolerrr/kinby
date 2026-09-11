"""Create chat models from ``provider:model`` names."""

from __future__ import annotations

from functools import cached_property

from langchain.chat_models import init_chat_model
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel

from kinby.instance import ModelName

_ANTHROPIC_PROVIDER = "anthropic"


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
