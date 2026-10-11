"""Tools, skills, and other loadable capabilities an instance can attach.

``load_skills`` resolves an instance's skills: instance, kinby, then workspace skills, each
with the source path its companion files sit beside.
"""

from kinby.plugins.skills import Skill, load_skills
from kinby.plugins.tools import Tool, ToolContext, tool

__all__ = ["Skill", "Tool", "ToolContext", "load_skills", "tool"]
