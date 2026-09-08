from kinby.contracts import TokenTotals
from kinby.core.pricing import price_map, token_cost
from kinby.instance import ModelPrice


def test_token_cost_prices_cached_tokens_at_cache_rates() -> None:
    totals = TokenTotals(
        input_tokens=1000,
        output_tokens=200,
        cache_read_tokens=800,
        cache_creation_tokens=100,
    )
    price = ModelPrice(input=3, output=15, cache_read=0.3, cache_write=3.75)

    assert token_cost(totals, price) == 0.003915


def test_token_cost_prices_cached_tokens_at_the_input_rate_without_cache_rates() -> None:
    totals = TokenTotals(
        input_tokens=1000,
        output_tokens=200,
        cache_read_tokens=800,
        cache_creation_tokens=100,
    )
    price = ModelPrice(input=3, output=15)

    assert token_cost(totals, price) == 0.006
    assert token_cost(totals, price) > token_cost(
        totals, ModelPrice(input=3, output=15, cache_read=0.3, cache_write=3.75)
    )


def test_shipped_prices_include_published_cache_rates() -> None:
    prices = price_map()

    assert prices["anthropic:claude-sonnet-4-6"] == ModelPrice(
        input=3, output=15, cache_read=0.3, cache_write=3.75
    )
    assert prices["openai:gpt-5"] == ModelPrice(input=1.25, output=10, cache_read=0.125)
