"""Exa's search API."""

from collections.abc import Sequence

import httpx

from kinby.plugins.defaults.search import SearchResult

_URL = "https://api.exa.ai/search"


async def search(
    client: httpx.AsyncClient, key: str, query: str, *, count: int, domains: Sequence[str]
) -> list[SearchResult]:
    response = await client.post(
        _URL,
        json={
            "query": query,
            "numResults": count,
            **({"includeDomains": list(domains)} if domains else {}),
            "contents": {"highlights": True},
        },
        headers={"x-api-key": key},
    )
    response.raise_for_status()
    return [
        SearchResult(
            title=result.get("title") or "",
            url=result["url"],
            snippet=" ".join(result.get("highlights") or []),
        )
        for result in response.json().get("results", [])
    ]
