"""Tools available to every instance unless its manifest disables defaults."""

from kinby.plugins.defaults.files import edit, glob, grep, read, write
from kinby.plugins.defaults.shell import bash

TOOLS = (read, write, edit, grep, glob, bash)

__all__ = ["TOOLS"]
