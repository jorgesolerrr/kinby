"""The search backends web_search sends its queries to, one module each."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class Search(Protocol):
    """Sends a query to one backend's own host and reads back its results."""

    async def __call__(
        self,
        client: httpx.AsyncClient,
        key: str,
        query: str,
        *,
        count: int,
        domains: Sequence[str],
    ) -> list[SearchResult]: ...


def with_sites(query: str, domains: Sequence[str]) -> str:
    """The query limited to *domains* with the `site:` operator, for a backend with no domain
    parameter."""
    return " ".join([query, " OR ".join(f"site:{domain}" for domain in domains)]).strip()
