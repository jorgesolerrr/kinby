"""The Anthropic chat model kinby builds, which loads only with a model of that provider."""

from __future__ import annotations

from functools import cached_property

from langchain_anthropic import ChatAnthropic


class ProfileChatAnthropic(ChatAnthropic):
    """Let the Anthropic SDK find an ``ant auth login`` profile when no API key is set.

    ChatAnthropic passes an empty key to the SDK, which then skips its credential chain.
    """

    @cached_property
    def _client_params(self) -> dict[str, object]:
        params = dict(super()._client_params)
        if not params.get("api_key"):
            params["api_key"] = None
        return params
