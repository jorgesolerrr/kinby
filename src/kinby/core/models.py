"""Create chat models from ``provider:model`` names."""

from __future__ import annotations

from functools import cached_property

from langchain.chat_models import init_chat_model
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel


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


def init_model(name: str) -> BaseChatModel:
    provider, _, model = name.partition(":")
    if provider == "anthropic":
        return _ProfileChatAnthropic(model=model)
    return init_chat_model(name)
