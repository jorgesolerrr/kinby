"""Brave Search's web search API."""

from collections.abc import Sequence

import httpx

from kinby.plugins.defaults.search import SearchResult, with_sites

_URL = "https://api.search.brave.com/res/v1/web/search"


async def search(
    client: httpx.AsyncClient, key: str, query: str, *, count: int, domains: Sequence[str]
) -> list[SearchResult]:
    response = await client.get(
        _URL,
        params={"q": with_sites(query, domains), "count": count},
        headers={"Accept": "application/json", "X-Subscription-Token": key},
    )
    response.raise_for_status()
    return [
        SearchResult(
            title=result["title"], url=result["url"], snippet=result.get("description", "")
        )
        for result in response.json().get("web", {}).get("results", [])
    ]
