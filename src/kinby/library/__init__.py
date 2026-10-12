"""The library kinby ships. Its factories ship apart, in ``kinby.factories``."""

from pathlib import Path

#: The skills kinby ships, one Agent Skills folder each. A hub copies them into its library.
SHIPPED_SKILLS = Path(__file__).parent / "skills"

__all__ = ["SHIPPED_SKILLS"]
