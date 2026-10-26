"""Tavily's search API."""

from collections.abc import Sequence

import httpx

from kinby.plugins.defaults.search import SearchResult

_URL = "https://api.tavily.com/search"


async def search(
    client: httpx.AsyncClient, key: str, query: str, *, count: int, domains: Sequence[str]
) -> list[SearchResult]:
    response = await client.post(
        _URL,
        json={
            "query": query,
            "max_results": count,
            **({"include_domains": list(domains)} if domains else {}),
        },
        headers={"Authorization": f"Bearer {key}"},
    )
    response.raise_for_status()
    return [
        SearchResult(title=result["title"], url=result["url"], snippet=result.get("content", ""))
        for result in response.json().get("results", [])
    ]
