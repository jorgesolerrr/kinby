"""Price model tokens from the exact model name recorded on an event."""

from __future__ import annotations

from collections.abc import Mapping

from kinby.instance import ModelPrice

TOKENS_PER_MILLION = 1_000_000

SHIPPED_PRICES: Mapping[str, ModelPrice] = {
    "anthropic:claude-sonnet-4-6": ModelPrice(input=3, output=15),
    "openai:gpt-5": ModelPrice(input=1.25, output=10),
}


def price_map(overrides: Mapping[str, ModelPrice] | None = None) -> dict[str, ModelPrice]:
    """Return shipped prices with exact-name manifest overrides applied."""
    prices = dict(SHIPPED_PRICES)
    if overrides is not None:
        prices.update(overrides)
    return prices


def token_cost(input_tokens: int, output_tokens: int, price: ModelPrice) -> float:
    """Return the cost of input and output tokens at *price*."""
    return (input_tokens * price.input + output_tokens * price.output) / TOKENS_PER_MILLION
