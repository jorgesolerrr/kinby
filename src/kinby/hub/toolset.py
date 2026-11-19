"""The tool groups of an instance's toolset, each tool in the state its next turn gives it, read
from the instance's files, and the user's changes to their states (ADR 0129)."""

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from kinby.contracts import (
    BuiltInGroup,
    BuiltInToolGroup,
    ConfigActor,
    ConfigFile,
    FileHash,
    GroupTool,
    InstanceToolStatesSetCommand,
    McpServerGroup,
    ToolState,
    ToolStateSource,
)
from kinby.core.errors import InvalidConfig, StaleWrite
from kinby.instance import Instance, Manifest, ToolStates
from kinby.instance.config_changes import files_hash, recorded_changes
from kinby.instance.layout import MANIFEST_NAME, SKILLS_LOCK_NAME
from kinby.instance.manifest import built_in_group_states, parse_manifest, with_tool_states
from kinby.instance.skills_lock import lock_text, read_lock, write_lock
from kinby.memory import GraphStore, memory_tools
from kinby.plugins.browser_tools import BROWSER_TOOLS
from kinby.plugins.defaults import TOOLS as DEFAULT_TOOLS
from kinby.plugins.defaults.web import web_search
from kinby.plugins.listed_tools import (
    ListedTool,
    authored_tools_listed,
    listed,
    mcp_tools_listed,
)
from kinby.plugins.tools import Tool

_GROUP_NAMES = {
    BuiltInToolGroup.DEFAULTS: "default tools",
    BuiltInToolGroup.MEMORY: "memory tools",
    BuiltInToolGroup.BROWSER: "browser tools",
    BuiltInToolGroup.AUTHORED: "authored tools",
}


def built_in_groups(
    instance_path: Path, manifest: Manifest, *, search_key_held: bool
) -> list[BuiltInGroup]:
    """Each built-in group: kinby's own tools, and the authored tools the last turn loaded. A turn
    offers web_search only while the instance holds its search backend's key."""
    kinby = _kinby_tools(instance_path)
    listed_tools = {
        **{group: _listed(tools) for group, tools in kinby.items()},
        BuiltInToolGroup.AUTHORED: authored_tools_listed(manifest.state_dir),
    }
    unavailable = frozenset() if search_key_held else frozenset({web_search.name})
    raw = parse_manifest((instance_path / MANIFEST_NAME).read_text(encoding="utf-8"))
    instance_states = built_in_group_states(raw, _group_of(kinby))
    groups = []
    for group in BuiltInToolGroup:
        states = manifest.tools.states(group)
        tools = _group_tools(listed_tools[group], states, own=states.tools, unavailable=unavailable)
        groups.append(
            BuiltInGroup(
                group=group,
                on=states.on,
                default_state=states.default,
                tools=tools,
                loaded_tokens=_loaded_tokens(tools),
                instance_states=instance_states[group],
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
                instance_states=pick.instance_states,
            )
        )
    return groups


def toolset_hash(instance_path: Path) -> FileHash:
    """The hash of the files that hold the instance's tool states."""
    return files_hash(
        {
            name: instance_path / name
            for name in (MANIFEST_NAME, SKILLS_LOCK_NAME)
            if (instance_path / name).is_file()
        }
    )


def set_tool_states(instance: Instance, command: InstanceToolStatesSetCommand) -> None:
    """Set the states of the groups *command* names, each file it changes a config change the
    user made."""
    path = instance.path
    if toolset_hash(path) != command.hash:
        raise StaleWrite(
            f"{MANIFEST_NAME} or {SKILLS_LOCK_NAME} changed since the toolset was read. "
            "Read it again."
        )
    group_of = _group_of(_kinby_tools(path))
    lock = read_lock(path)
    refusals: dict[str, str] = {}
    if outside := [
        f'"{name}" is not one of the {_GROUP_NAMES[group]}.'
        for group, states in command.built_in_groups.items()
        for name in states.tool_states
        if group_of(name) is not group
    ]:
        refusals["built_in_groups"] = " ".join(outside)
    if unpicked := [
        f'The instance picks no MCP server "{name}".'
        for name in command.mcp_servers
        if name not in lock.mcp_servers
    ]:
        refusals["mcp_servers"] = " ".join(unpicked)
    if refusals:
        raise InvalidConfig(refusals)
    manifest = path / MANIFEST_NAME
    text = manifest.read_text(encoding="utf-8")
    edited = with_tool_states(text, command.built_in_groups, group_of)
    locked = lock_text(lock)
    for name, states in command.mcp_servers.items():
        lock.mcp_servers[name] = lock.mcp_servers[name].with_states(states)
    relocked = lock_text(lock) != locked
    changed = [
        ConfigFile(file)
        for file, differs in ((MANIFEST_NAME, edited != text), (SKILLS_LOCK_NAME, relocked))
        if differs
    ]
    with recorded_changes(instance, changed, ConfigActor.APP):
        if edited != text:
            staging = manifest.with_name(f".{manifest.name}.staging")
            staging.write_text(edited, encoding="utf-8")
            staging.replace(manifest)
        if relocked:
            write_lock(path, lock)


def _kinby_tools(instance_path: Path) -> dict[BuiltInToolGroup, tuple[Tool, ...]]:
    """The tools of each built-in group kinby defines."""
    return {
        BuiltInToolGroup.DEFAULTS: DEFAULT_TOOLS,
        BuiltInToolGroup.MEMORY: tuple(memory_tools(GraphStore(instance_path))),
        BuiltInToolGroup.BROWSER: BROWSER_TOOLS,
    }


def _group_of(
    kinby: Mapping[BuiltInToolGroup, Iterable[Tool]],
) -> Callable[[str], BuiltInToolGroup]:
    """The built-in group a tool's own state in the manifest belongs to: one of *kinby*'s, else
    the authored tools'."""
    groups = {tool.name: group for group, tools in kinby.items() for tool in tools}
    return lambda name: groups.get(name, BuiltInToolGroup.AUTHORED)


def _listed(tools: Iterable[Tool]) -> tuple[ListedTool, ...]:
    return tuple(sorted((listed(tool.name, tool) for tool in tools), key=lambda tool: tool.name))


def _group_tools(
    listed_tools: Iterable[ListedTool] | None,
    states: ToolStates,
    own: Mapping[str, ToolState],
    unavailable: frozenset[str] = frozenset(),
) -> list[GroupTool] | None:
    """The group's *listed_tools* in its *states*, where the instance set the *own* states. An
    *unavailable* tool is off whatever its state."""
    if listed_tools is None:
        return None
    return [_group_tool(tool, states, own, unavailable) for tool in listed_tools]


def _group_tool(
    tool: ListedTool,
    states: ToolStates,
    own: Mapping[str, ToolState],
    unavailable: frozenset[str],
) -> GroupTool:
    state, source = states.of(tool.name), _source(tool.name, states, own)
    if tool.name in unavailable and state is not ToolState.OFF:
        state, source = ToolState.OFF, ToolStateSource.UNAVAILABLE
    return GroupTool(
        name=tool.name,
        description=tool.description,
        state=state,
        source=source,
        tokens=tool.tokens,
    )


def _source(tool: str, states: ToolStates, own: Mapping[str, ToolState]) -> ToolStateSource:
    if not states.on or tool not in states.tools:
        return ToolStateSource.GROUP
    return ToolStateSource.INSTANCE if tool in own else ToolStateSource.LIBRARY


def _loaded_tokens(tools: Iterable[GroupTool] | None) -> int:
    return sum(tool.tokens for tool in tools or () if tool.state is ToolState.LOADED)
