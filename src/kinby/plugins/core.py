"""Assemble the core model tools for one instance."""

from collections.abc import Callable, Sequence
from datetime import date

from kinby.core.clock import utc_today
from kinby.instance import Instance
from kinby.memory import GraphStore, memory_tools
from kinby.plugins.instance_tools import instance_tools
from kinby.plugins.skills import Skill, skill_tool
from kinby.plugins.tools import Tool


def core_tools(
    instance: Instance,
    skills: Sequence[Skill],
    *,
    today: Callable[[], date] = utc_today,
) -> tuple[Tool, ...]:
    """Build the complete core tool set for one model turn."""
    return (
        skill_tool(skills),
        *memory_tools(GraphStore(instance.path), today=today),
        *instance_tools(instance),
    )
