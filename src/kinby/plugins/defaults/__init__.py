"""Tools and skills available unless the instance disables defaults."""

from pathlib import Path

from kinby.plugins.defaults.files import edit, glob, grep, read, write
from kinby.plugins.defaults.shell import bash

TOOLS = (read, write, edit, grep, glob, bash)
SKILLS: Path = Path(__file__).parent / "skills"

__all__ = ["SKILLS", "TOOLS"]
