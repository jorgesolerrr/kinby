"""Default web tools."""

import os
from typing import Annotated

import httpx
from pydantic import Field

from kinby.instance import SearchBackend
from kinby.plugins import ToolContext, tool
from kinby.plugins.defaults.search import Search, SearchResult, brave, exa, searxng, tavily

_BACKENDS: dict[SearchBackend, Search] = {
    SearchBackend.BRAVE: brave.search,
    SearchBackend.TAVILY: tavily.search,
    SearchBackend.EXA: exa.search,
    SearchBackend.SEARXNG: searxng.search,
}
_TIMEOUT_SECONDS = 20


@tool(write=False)
async def web_search(
    query: str,
    context: ToolContext,
    count: Annotated[int, Field(ge=1, le=20)] = 5,
    domains: list[str] | None = None,
) -> str:
    """Search the web, returning each result's title, URL and snippet. `domains` limits the
    search to those sites, and `count` sets how many results come back."""
    backend = context.instance.manifest.tools.search
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        results = await _BACKENDS[backend](
            client, os.environ[backend.key_variable], query, count=count, domains=domains or ()
        )
    return _rendered(results)


def _rendered(results: list[SearchResult]) -> str:
    if not results:
        return "No results."
    return "\n\n".join(f"{result.title}\n{result.url}\n{result.snippet}" for result in results)
