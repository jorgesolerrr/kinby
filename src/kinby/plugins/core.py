"""Assemble the core model tools for one instance, and tell each tool's state."""

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from pathlib import Path

from kinby.contracts import BuiltInToolGroup, ToolState
from kinby.core.clock import utc_today
from kinby.instance import Instance, Tools, ToolStates
from kinby.memory import GraphStore, memory_tools
from kinby.plugins import defaults
from kinby.plugins.browser_tools import BROWSER_TOOLS, browser_tools
from kinby.plugins.coding_runs import coding_run_tools
from kinby.plugins.hub import hub_tools
from kinby.plugins.instance_tools import instance_tools
from kinby.plugins.intake import intake_tools
from kinby.plugins.lines import line_tools
from kinby.plugins.notify import notify_tools
from kinby.plugins.registry import ToolSnapshot
from kinby.plugins.skills import Skill, skill_tool
from kinby.plugins.tools import Tool
from kinby.plugins.views import view_tools

#: Where the memory tools are defined. They are built per turn, so their source tells them apart.
_MEMORY_SOURCE = Path(memory_tools.__code__.co_filename).resolve()


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
        *intake_tools(),
        *line_tools(),
        *notify_tools(),
        *view_tools(),
        *hub_tools(),
        *browser_tools(instance),
        *coding_run_tools(instance),
    )


def built_in_states(tools: Tools) -> dict[BuiltInToolGroup, ToolStates]:
    """The states of each built-in group's tools, as the manifest's *tools* sets them."""
    return {group: tools.states(group) for group in BuiltInToolGroup}


def by_state(
    tools: ToolSnapshot, groups: Mapping[BuiltInToolGroup, ToolStates]
) -> tuple[ToolSnapshot, tuple[Tool, ...]]:
    """The loaded tools of *tools*, and the searchable ones, in the states of their built-in
    *groups*. A tool of no group is always loaded."""
    states = {tool.name: _state(tool, groups) for tool in tools.tools}
    return (
        ToolSnapshot(tuple(tool for tool in tools.tools if states[tool.name] is ToolState.LOADED)),
        tuple(tool for tool in tools.tools if states[tool.name] is ToolState.SEARCHABLE),
    )


def _state(tool: Tool, groups: Mapping[BuiltInToolGroup, ToolStates]) -> ToolState:
    group = _group(tool)
    if group is None or group not in groups:
        return ToolState.LOADED
    return groups[group].of(tool.name)


def _group(tool: Tool) -> BuiltInToolGroup | None:
    if tool.authored:
        return BuiltInToolGroup.AUTHORED
    if tool in defaults.TOOLS:
        return BuiltInToolGroup.DEFAULTS
    if tool in BROWSER_TOOLS:
        return BuiltInToolGroup.BROWSER
    if tool.source == _MEMORY_SOURCE:
        return BuiltInToolGroup.MEMORY
    return None
