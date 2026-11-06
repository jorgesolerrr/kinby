"""Create chat models from ``provider:model`` names."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage, SystemMessage

from kinby.instance import ModelName

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

_ANTHROPIC_PROVIDER = "anthropic"

#: Counts a text's tokens with a model's provider tokenizer.
Tokenizer = Callable[[ModelName, str], int]


def _split_model_name(name: ModelName) -> tuple[str, str]:
    provider, _, model = name.partition(":")
    return provider, model


def is_anthropic_model(name: ModelName) -> bool:
    provider, _ = _split_model_name(name)
    return provider == _ANTHROPIC_PROVIDER


def init_model(name: ModelName) -> BaseChatModel:
    """A chat model for *name*, importing its provider's integration only now, so an instance
    loads the SDKs of the providers it uses and no other."""
    provider, model = _split_model_name(name)
    if provider == _ANTHROPIC_PROVIDER:
        from kinby.core.anthropic_model import ProfileChatAnthropic

        return ProfileChatAnthropic(model=model)
    from langchain.chat_models import init_chat_model

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
