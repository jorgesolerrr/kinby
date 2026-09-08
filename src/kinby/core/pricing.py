"""Price model tokens from the exact model name recorded on an event.

Shipped cache rates are the providers' published per-million-token prices.
Anthropic Claude Sonnet 4.6: cache read $0.30, 5-minute cache write $3.75
(https://platform.claude.com/docs/en/about-claude/pricing). OpenAI GPT-5:
cached input $0.125 (10% of the $1.25 input rate); no cache-write surcharge
on models before GPT-5.6 (https://developers.openai.com/api/docs/guides/prompt-caching).
"""

from __future__ import annotations

from collections.abc import Mapping

from kinby.contracts import TokenTotals
from kinby.instance import ModelPrice

TOKENS_PER_MILLION = 1_000_000

SHIPPED_PRICES: Mapping[str, ModelPrice] = {
    "anthropic:claude-sonnet-4-6": ModelPrice(input=3, output=15, cache_read=0.3, cache_write=3.75),
    "openai:gpt-5": ModelPrice(input=1.25, output=10, cache_read=0.125),
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
