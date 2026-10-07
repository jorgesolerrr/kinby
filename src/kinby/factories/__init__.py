"""Factories: the file that declares one, and the factory check that validates it as a whole."""

from pathlib import Path

#: The factories kinby ships. The hub serves them read-only.
SHIPPED_FACTORIES = Path(__file__).parent / "shipped"
#: The image recipes kinby ships, one ``<name>.Dockerfile`` each, appended to kinby's base image.
RECIPES_DIRECTORY = Path(__file__).parent / "recipes"

__all__ = ["RECIPES_DIRECTORY", "SHIPPED_FACTORIES"]
