"""Hand a subscriber the boundary between replay and live next to its items."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Stream[Item]:
    """One subscription: items up to ``head_sequence`` are replay, later ones are live."""

    head_sequence: int
    items: AsyncGenerator[Item]
    _close: Callable[[], None] | None = None

    async def aclose(self) -> None:
        """End the subscription, even if the caller never pulled an item."""
        try:
            await self.items.aclose()
        finally:
            if self._close is not None:
                self._close()
