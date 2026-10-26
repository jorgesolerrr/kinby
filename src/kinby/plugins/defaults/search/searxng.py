"""A SearXNG server's JSON search, at the URL the backend's variable holds."""

from collections.abc import Sequence

import httpx

from kinby.plugins.defaults.search import SearchResult, with_sites


async def search(
    client: httpx.AsyncClient, key: str, query: str, *, count: int, domains: Sequence[str]
) -> list[SearchResult]:
    response = await client.get(
        f"{key.rstrip('/')}/search",
        params={"q": with_sites(query, domains), "format": "json"},
    )
    response.raise_for_status()
    # SearXNG takes no result count, so the first results stand in for one.
    return [
        SearchResult(title=result["title"], url=result["url"], snippet=result.get("content", ""))
        for result in response.json().get("results", [])[:count]
    ]
