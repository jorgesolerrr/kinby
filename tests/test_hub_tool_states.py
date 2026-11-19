"""The user changes an instance's tool states from its toolset: each group's switch and default
state, and a tool's own state, each a config change the instance's next turn takes (ADR 0129)."""

import asyncio
import json
from pathlib import Path
from uuid import UUID

from kinby.contracts import (
    INSTANCE_PICKS_GET,
    INSTANCE_PICKS_SET,
    INSTANCE_TOOL_STATES_SET,
    BuiltInToolGroup,
    ConfigActor,
    ErrorCode,
    ErrorEnvelope,
    InstancePicksGetCommand,
    InstancePicksResult,
    InstancePicksSetCommand,
    InstanceToolsetResult,
    InstanceToolStatesSetCommand,
    McpServerChoice,
    ToolGroupStates,
    ToolState,
    ToolStateSource,
)
from kinby.hub import Hub
from kinby.instance import Instance, load_instance
from kinby.instance.config_changes import ConfigChangeLog
from kinby.instance.skills_lock import LockedMcpServer, SkillsLock, write_lock
from tests.test_hub import hub_at, hub_client
from tests.test_hub_mcp_picks import _library_server
from tests.test_hub_toolset import stopped_instance, tool, toolset_of
from tests.test_mcp_servers import echo
from tests.test_tool_groups import sent


def set_states(
    hub: Hub,
    instance_id: UUID,
    read: InstanceToolsetResult,
    *,
    built_in_groups: dict[BuiltInToolGroup, ToolGroupStates] | None = None,
    mcp_servers: dict[str, ToolGroupStates] | None = None,
) -> InstanceToolsetResult | ErrorEnvelope:
    async def call() -> object:
        return await hub_client(hub).call(
            INSTANCE_TOOL_STATES_SET,
            InstanceToolStatesSetCommand(
                instance_id=instance_id,
                built_in_groups=built_in_groups or {},
                mcp_servers=mcp_servers or {},
                hash=read.hash,
            ),
        )

    answer = asyncio.run(call())
    assert isinstance(answer, InstanceToolsetResult | ErrorEnvelope), answer
    return answer


def created(tmp_path: Path) -> tuple[Hub, UUID, Instance]:
    hub = hub_at(tmp_path / "hub")
    instance_id, instance = asyncio.run(stopped_instance(hub))
    return hub, instance_id, instance


def read(hub: Hub, instance_id: UUID) -> InstanceToolsetResult:
    return asyncio.run(toolset_of(hub, instance_id))


def test_switching_a_group_off_and_giving_a_tool_its_own_state_is_a_config_change_the_user_made(
    tmp_path: Path,
) -> None:
    hub, instance_id, instance = created(tmp_path)

    changed = set_states(
        hub,
        instance_id,
        read(hub, instance_id),
        built_in_groups={
            BuiltInToolGroup.DEFAULTS: ToolGroupStates(tool_states={"bash": ToolState.OFF}),
            BuiltInToolGroup.MEMORY: ToolGroupStates(on=False),
        },
    )

    assert isinstance(changed, InstanceToolsetResult)
    assert changed == read(hub, instance_id)
    defaults, memory, _, _ = changed.built_in_groups
    assert (tool(defaults, "bash").state, tool(defaults, "bash").source) == (
        ToolState.OFF,
        ToolStateSource.INSTANCE,
    )
    assert defaults.instance_states == ToolGroupStates(tool_states={"bash": ToolState.OFF})
    assert not memory.on
    assert {each.state for each in memory.tools or ()} == {ToolState.OFF}
    [change] = ConfigChangeLog(instance.manifest.state_dir).history(None, 10)
    assert (change.file, change.actor) == ("kinby.toml", ConfigActor.APP)
    assert "+memory = false" in change.diff
    assert '+states = {bash = "off"}' in change.diff


def test_the_instances_next_turn_takes_the_states(tmp_path: Path) -> None:
    hub, instance_id, instance = created(tmp_path)
    assert {"bash", "memory_search"} <= sent(load_instance(instance.path))

    set_states(
        hub,
        instance_id,
        read(hub, instance_id),
        built_in_groups={
            BuiltInToolGroup.DEFAULTS: ToolGroupStates(tool_states={"bash": ToolState.OFF}),
            BuiltInToolGroup.MEMORY: ToolGroupStates(default_state=ToolState.SEARCHABLE),
        },
    )

    assert not {"bash", "memory_search"} & sent(load_instance(instance.path))


def test_a_built_in_group_resets_to_kinbys_default(tmp_path: Path) -> None:
    hub, instance_id, _ = created(tmp_path)
    memory = BuiltInToolGroup.MEMORY
    changed = set_states(
        hub,
        instance_id,
        read(hub, instance_id),
        built_in_groups={
            memory: ToolGroupStates(
                default_state=ToolState.SEARCHABLE, tool_states={"remember": ToolState.OFF}
            )
        },
    )
    assert isinstance(changed, InstanceToolsetResult)

    reset = set_states(hub, instance_id, changed, built_in_groups={memory: ToolGroupStates()})

    assert isinstance(reset, InstanceToolsetResult)
    group = reset.built_in_groups[1]
    assert (group.default_state, group.instance_states) == (ToolState.LOADED, ToolGroupStates())
    assert (tool(group, "remember").state, tool(group, "remember").source) == (
        ToolState.LOADED,
        ToolStateSource.GROUP,
    )


def test_a_servers_states_change_in_its_pick_and_reset_to_the_librarys(tmp_path: Path) -> None:
    hub, instance_id, instance = created(tmp_path)
    library = echo("look", default_state=ToolState.SEARCHABLE)
    write_lock(instance.path, SkillsLock(mcp_servers={"echo": LockedMcpServer(server=library)}))
    states = ToolGroupStates(
        default_state=ToolState.OFF, tool_states={"look": ToolState.SEARCHABLE}
    )

    changed = set_states(hub, instance_id, read(hub, instance_id), mcp_servers={"echo": states})

    assert isinstance(changed, InstanceToolsetResult)
    [server] = changed.mcp_server_groups
    assert (server.default_state, server.instance_states) == (ToolState.OFF, states)
    locked = json.loads((instance.path / "skills-lock.json").read_bytes())["mcpServers"]["echo"]
    assert (locked["defaultState"], locked["toolStates"]) == ("off", {"look": "searchable"})
    [change] = ConfigChangeLog(instance.manifest.state_dir).history(None, 10)
    assert (change.file, change.actor) == ("skills-lock.json", ConfigActor.APP)

    reset = set_states(hub, instance_id, changed, mcp_servers={"echo": ToolGroupStates()})

    assert isinstance(reset, InstanceToolsetResult)
    [server] = reset.mcp_server_groups
    assert (server.default_state, server.instance_states) == (
        ToolState.SEARCHABLE,
        ToolGroupStates(),
    )


def test_a_set_over_a_toolset_changed_since_it_was_read_is_refused_as_stale(
    tmp_path: Path,
) -> None:
    hub, instance_id, instance = created(tmp_path)
    before = read(hub, instance_id)
    manifest = instance.path / "kinby.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n# Edited by hand.\n")

    refused = set_states(
        hub,
        instance_id,
        before,
        built_in_groups={BuiltInToolGroup.MEMORY: ToolGroupStates(on=False)},
    )

    assert isinstance(refused, ErrorEnvelope)
    assert refused.code is ErrorCode.STALE
    assert read(hub, instance_id).built_in_groups[1].on


def test_a_tool_outside_the_group_and_a_server_the_instance_does_not_pick_are_refused(
    tmp_path: Path,
) -> None:
    hub, instance_id, _ = created(tmp_path)

    refused = set_states(
        hub,
        instance_id,
        read(hub, instance_id),
        built_in_groups={
            BuiltInToolGroup.MEMORY: ToolGroupStates(tool_states={"skill": ToolState.OFF})
        },
        mcp_servers={"echo": ToolGroupStates(on=False)},
    )

    assert isinstance(refused, ErrorEnvelope)
    assert refused.code is ErrorCode.INVALID_ARGUMENT
    assert refused.fields == {
        "built_in_groups": '"skill" is not one of the memory tools.',
        "mcp_servers": 'The instance picks no MCP server "echo".',
    }


def test_an_old_list_of_default_tools_switched_off_joins_the_tools_own_states(
    tmp_path: Path,
) -> None:
    hub, instance_id, instance = created(tmp_path)
    manifest = instance.path / "kinby.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + '\n[tools]\noff = ["bash", "glob"]\n'
    )

    changed = set_states(
        hub,
        instance_id,
        read(hub, instance_id),
        built_in_groups={
            BuiltInToolGroup.DEFAULTS: ToolGroupStates(tool_states={"bash": ToolState.OFF})
        },
    )

    assert isinstance(changed, InstanceToolsetResult)
    defaults = changed.built_in_groups[0]
    assert tool(defaults, "glob").state is ToolState.LOADED
    assert "off =" not in manifest.read_text(encoding="utf-8")


def test_the_picks_carry_each_servers_states_and_saving_them_back_keeps_them(
    tmp_path: Path,
) -> None:
    hub, instance_id, _ = created(tmp_path)
    _library_server(hub, "echo", echo())
    states = ToolGroupStates(on=False, tool_states={"look": ToolState.LOADED})

    async def scenario() -> InstancePicksResult:
        client = hub_client(hub)
        picked = await client.call(
            INSTANCE_PICKS_SET,
            InstancePicksSetCommand(
                instance_id=instance_id,
                skills=[],
                mcp_servers=[McpServerChoice(name="echo", states=states)],
                hash=None,
            ),
        )
        assert isinstance(picked, InstancePicksResult), picked
        again = await client.call(
            INSTANCE_PICKS_SET,
            InstancePicksSetCommand(
                instance_id=instance_id,
                skills=[],
                mcp_servers=[
                    McpServerChoice(name=pick.name, trust_hints=True, states=pick.states)
                    for pick in picked.mcp_servers
                ],
                hash=picked.hash,
            ),
        )
        assert isinstance(again, InstancePicksResult), again
        held = await client.call(
            INSTANCE_PICKS_GET, InstancePicksGetCommand(instance_id=instance_id)
        )
        assert isinstance(held, InstancePicksResult)
        return held

    held = asyncio.run(scenario())

    [pick] = held.mcp_servers
    assert (pick.trust_hints, pick.states) == (True, states)
    [server] = read(hub, instance_id).mcp_server_groups
    assert server.instance_states == states
