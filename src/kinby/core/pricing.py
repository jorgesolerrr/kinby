"""Price model tokens from the exact model name recorded on an event.

Shipped prices are the providers' published per-million-token prices. Anthropic
(https://platform.claude.com/docs/en/about-claude/pricing): cache reads are 0.1x
input, except Claude Fable 5.1 at $0.25 and Claude Opus 5.5 at $0.20; 5-minute
cache writes are 1.25x input. OpenAI: cached input is 10% of the input rate, with
no cache-write surcharge on models before GPT-5.6
(https://developers.openai.com/api/docs/guides/prompt-caching).
"""

from __future__ import annotations

from collections.abc import Mapping

from kinby.contracts import TokenTotals
from kinby.instance import ModelPrice

TOKENS_PER_MILLION = 1_000_000

SHIPPED_PRICES: Mapping[str, ModelPrice] = {
    "anthropic:claude-fable-5-1": ModelPrice(
        input=10, output=50, cache_read=0.25, cache_write=12.5
    ),
    "anthropic:claude-opus-5-5": ModelPrice(input=4, output=20, cache_read=0.2, cache_write=5),
    "anthropic:claude-opus-5": ModelPrice(input=5, output=25, cache_read=0.5, cache_write=6.25),
    "anthropic:claude-sonnet-5": ModelPrice(input=2, output=10, cache_read=0.2, cache_write=2.5),
    "anthropic:claude-sonnet-4-6": ModelPrice(input=3, output=15, cache_read=0.3, cache_write=3.75),
    "anthropic:claude-haiku-4-5": ModelPrice(input=1, output=5, cache_read=0.1, cache_write=1.25),
    "openai:gpt-5.2": ModelPrice(input=1.75, output=14, cache_read=0.175),
    "openai:gpt-5.1": ModelPrice(input=1.25, output=10, cache_read=0.125),
    "openai:gpt-5": ModelPrice(input=1.25, output=10, cache_read=0.125),
    "openai:gpt-5-mini": ModelPrice(input=0.25, output=2, cache_read=0.025),
    "openai:gpt-5-nano": ModelPrice(input=0.05, output=0.4, cache_read=0.005),
}


def price_map(overrides: Mapping[str, ModelPrice] | None = None) -> dict[str, ModelPrice]:
    """Return shipped prices with exact-name manifest overrides applied."""
    prices = dict(SHIPPED_PRICES)
    if overrides is not None:
        prices.update(overrides)
    return prices


def token_cost(totals: TokenTotals, price: ModelPrice) -> float:
    """Return the cost of *totals* at *price*.

    Cache read and cache write tokens are subsets of input. They price at
    ``cache_read`` and ``cache_write`` when set, otherwise at the input rate.
    """
    cache_read_rate = price.input if price.cache_read is None else price.cache_read
    cache_write_rate = price.input if price.cache_write is None else price.cache_write
    uncached = totals.input_tokens - totals.cache_read_tokens - totals.cache_creation_tokens
    return (
        uncached * price.input
        + totals.cache_read_tokens * cache_read_rate
        + totals.cache_creation_tokens * cache_write_rate
        + totals.output_tokens * price.output
    ) / TOKENS_PER_MILLION
