"""Tools and skills available unless the instance disables defaults."""

from pathlib import Path

from kinby.plugins.defaults.code import run_code
from kinby.plugins.defaults.files import edit, glob, grep, read, write
from kinby.plugins.defaults.shell import bash
from kinby.plugins.defaults.web import web_fetch, web_search

TOOLS = (read, write, edit, grep, glob, bash, web_search, web_fetch, run_code)
SKILLS: Path = Path(__file__).parent / "skills"

__all__ = ["SKILLS", "TOOLS"]
