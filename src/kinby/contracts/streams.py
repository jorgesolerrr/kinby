"""Hand a subscriber the boundary between replay and live next to its items."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass


@dataclass(frozen=True)
class Stream[Item]:
    """One subscription: items up to ``head_sequence`` are replay, later ones are live."""

    head_sequence: int
    items: AsyncGenerator[Item]
