"""The tool groups of an instance's toolset, each tool in the state its next turn gives it, read
from the instance's files (ADR 0129)."""

from collections.abc import Iterable, Mapping
from pathlib import Path

from kinby.contracts import (
    BuiltInGroup,
    BuiltInToolGroup,
    GroupTool,
    McpServerGroup,
    ToolState,
    ToolStateSource,
)
from kinby.instance import Manifest, ToolStates
from kinby.instance.skills_lock import read_lock
from kinby.memory import GraphStore, memory_tools
from kinby.plugins.browser_tools import BROWSER_TOOLS
from kinby.plugins.defaults import TOOLS as DEFAULT_TOOLS
from kinby.plugins.listed_tools import (
    ListedTool,
    authored_tools_listed,
    listed,
    mcp_tools_listed,
)
from kinby.plugins.tools import Tool


def built_in_groups(instance_path: Path, manifest: Manifest) -> list[BuiltInGroup]:
    """Each built-in group: kinby's own tools, and the authored tools the last turn loaded."""
    listed_tools = {
        BuiltInToolGroup.DEFAULTS: _listed(DEFAULT_TOOLS),
        BuiltInToolGroup.MEMORY: _listed(memory_tools(GraphStore(instance_path))),
        BuiltInToolGroup.BROWSER: _listed(BROWSER_TOOLS),
        BuiltInToolGroup.AUTHORED: authored_tools_listed(manifest.state_dir),
    }
    groups = []
    for group in BuiltInToolGroup:
        states = manifest.tools.states(group)
        tools = _group_tools(listed_tools[group], states, own=states.tools)
        groups.append(
            BuiltInGroup(
                group=group,
                on=states.on,
                default_state=states.default,
                tools=tools,
                loaded_tokens=_loaded_tokens(tools),
            )
        )
    return groups


def mcp_server_groups(instance_path: Path, manifest: Manifest) -> list[McpServerGroup]:
    """Each picked MCP server's group, with the tools it listed at the last turn connected to
    it."""
    listed_tools = mcp_tools_listed(manifest.state_dir)
    groups = []
    for server, pick in sorted(read_lock(instance_path).mcp_servers.items()):
        states = pick.states()
        tools = _group_tools(listed_tools.get(server), states, own=pick.tool_states)
        groups.append(
            McpServerGroup(
                server=server,
                on=states.on,
                default_state=states.default,
                tools=tools,
                loaded_tokens=_loaded_tokens(tools),
            )
        )
    return groups


def _listed(tools: Iterable[Tool]) -> tuple[ListedTool, ...]:
    return tuple(sorted((listed(tool.name, tool) for tool in tools), key=lambda tool: tool.name))


def _group_tools(
    listed_tools: Iterable[ListedTool] | None,
    states: ToolStates,
    own: Mapping[str, ToolState],
) -> list[GroupTool] | None:
    """The group's *listed_tools* in its *states*, where the instance set the *own* states."""
    if listed_tools is None:
        return None
    return [
        GroupTool(
            name=tool.name,
            description=tool.description,
            state=states.of(tool.name),
            source=_source(tool.name, states, own),
            tokens=tool.tokens,
        )
        for tool in listed_tools
    ]


def _source(tool: str, states: ToolStates, own: Mapping[str, ToolState]) -> ToolStateSource:
    if not states.on or tool not in states.tools:
        return ToolStateSource.GROUP
    return ToolStateSource.INSTANCE if tool in own else ToolStateSource.LIBRARY


def _loaded_tokens(tools: Iterable[GroupTool] | None) -> int:
    return sum(tool.tokens for tool in tools or () if tool.state is ToolState.LOADED)
