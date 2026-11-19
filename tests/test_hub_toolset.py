"""An instance's toolset, which the hub reads from its files whether it runs or not. The home
instance reads it beside the instance's MCP servers, profiles and vault entries, and never writes
a profile or a vault entry."""

import asyncio
import json
from pathlib import Path
from uuid import UUID

from kinby.contracts import (
    BROWSER_VAULT_ADD,
    INSTANCE_LIST,
    INSTANCE_PICKS_SET,
    INSTANCE_TOOLSET_GET,
    AuthoredTool,
    BuiltInGroup,
    BuiltInToolGroup,
    ErrorCode,
    GroupTool,
    InstanceListCommand,
    InstanceListResult,
    InstancePicksSetCommand,
    InstanceToolsetGetCommand,
    InstanceToolsetResult,
    McpServerChoice,
    McpServerGroup,
    McpServerPick,
    PermissionMode,
    PickState,
    SearchBackend,
    SearchSetup,
    ToolState,
    ToolStateSource,
)
from kinby.hub import Hub
from kinby.instance import Instance, inspect_instance
from kinby.instance.layout import AUTHORED_TOOLS_NAME, MCP_TOOLS_NAME
from kinby.instance.read_only_tools import set_read_only
from kinby.instance.skills_lock import LockedMcpServer, SkillsLock, write_lock
from tests.test_hub import created_instance, hold_search_key, hub_at, hub_client
from tests.test_hub_browser import _LIMITS, browser_hub
from tests.test_hub_home_reads import answers, error_code, home_hub
from tests.test_hub_library_mcp_servers import FETCH
from tests.test_hub_mcp_picks import _library_server
from tests.test_hub_read_command import as_home, kinby
from tests.test_hub_vault import _PASSWORD, _SEED, _USERNAME, _linkedin
from tests.test_instance_code import turn
from tests.test_mcp_servers import echo, launchers  # noqa: F401

_PROJECT = """\
[project]
name = "code"
version = "0.1.0"
dependencies = ["httpx>=0.27"]

[tool.kinby]
tools = ["lookup", "post"]
"""


def test_the_toolset_shows_the_manifest_the_search_key_picks_authored_tools_and_browser(
    tmp_path,
):
    async def scenario() -> object:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            record = hub.hub.registry.instance(hub.alice)
            assert record is not None
            manifest = record.path / "kinby.toml"
            manifest.write_text(
                manifest.read_text(encoding="utf-8") + '\n[tools]\noff = ["bash"]\n',
                encoding="utf-8",
            )
            (record.path / "code").mkdir()
            (record.path / "code" / "pyproject.toml").write_text(_PROJECT, encoding="utf-8")
            set_read_only(inspect_instance(record.path), "lookup", read_only=True)
            _library_server(hub.hub, "fetch", FETCH)
            client = hub_client(hub.hub)
            await client.call(
                INSTANCE_PICKS_SET,
                InstancePicksSetCommand(
                    instance_id=hub.alice,
                    skills=[],
                    mcp_servers=[McpServerChoice(name="fetch", trust_hints=True)],
                    hash=None,
                ),
            )
            return await client.call(
                INSTANCE_TOOLSET_GET, InstanceToolsetGetCommand(instance_id=hub.alice)
            )

    toolset = asyncio.run(scenario())

    assert isinstance(toolset, InstanceToolsetResult)
    assert toolset.search == SearchSetup(
        backend=SearchBackend.BRAVE, variable="BRAVE_API_KEY", is_set=False
    )
    assert toolset.mcp_servers == [
        McpServerPick(name="fetch", trust_hints=True, state=PickState.UP_TO_DATE)
    ]
    assert toolset.packages == ["httpx>=0.27"]
    assert toolset.authored_tools == [
        AuthoredTool(name="lookup", read_only=True),
        AuthoredTool(name="post", read_only=False),
    ]
    assert toolset.browser


async def stopped_instance(hub: Hub) -> tuple[UUID, Instance]:
    """An instance the hub created and never started, by its id."""
    created = await created_instance(hub_client(hub))
    return created.instance_id, inspect_instance(hub.instances_directory / str(created.instance_id))


async def toolset_of(hub: Hub, instance_id: UUID) -> InstanceToolsetResult:
    toolset = await hub_client(hub).call(
        INSTANCE_TOOLSET_GET, InstanceToolsetGetCommand(instance_id=instance_id)
    )
    assert isinstance(toolset, InstanceToolsetResult)
    return toolset


def tool(group: BuiltInGroup | McpServerGroup, name: str) -> GroupTool:
    [found] = [each for each in group.tools or () if each.name == name]
    return found


def loaded_tokens(group: BuiltInGroup | McpServerGroup) -> int:
    return sum(each.tokens for each in group.tools or () if each.state is ToolState.LOADED)


def test_a_stopped_instance_gives_each_built_in_group_its_tools_states_and_loaded_size(
    tmp_path: Path,
) -> None:
    async def scenario() -> InstanceToolsetResult:
        hub = hub_at(tmp_path / "hub")
        instance_id, instance = await stopped_instance(hub)
        manifest = instance.path / "kinby.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            + '\n[tools]\nauthored = false\ndefault_states = { memory = "searchable" }\n'
            'states = { bash = "off", memory_open = "loaded" }\n',
            encoding="utf-8",
        )
        return await toolset_of(hub, instance_id)

    toolset = asyncio.run(scenario())

    defaults, memory, browser, authored = toolset.built_in_groups
    assert [group.group for group in toolset.built_in_groups] == list(BuiltInToolGroup)
    assert (defaults.on, defaults.default_state) == (True, ToolState.LOADED)
    assert (tool(defaults, "bash").state, tool(defaults, "bash").source) == (
        ToolState.OFF,
        ToolStateSource.INSTANCE,
    )
    assert (tool(defaults, "read").state, tool(defaults, "read").source) == (
        ToolState.LOADED,
        ToolStateSource.GROUP,
    )
    assert defaults.loaded_tokens == loaded_tokens(defaults) > tool(defaults, "read").tokens
    assert memory.default_state is ToolState.SEARCHABLE
    assert tool(memory, "memory_search").state is ToolState.SEARCHABLE
    assert tool(memory, "memory_open").source is ToolStateSource.INSTANCE
    assert memory.loaded_tokens == tool(memory, "memory_open").tokens
    assert tool(memory, "remember").description == "Remember one fact learned in this thread."
    assert (browser.on, browser.loaded_tokens) == (False, 0)
    assert {each.state for each in browser.tools or ()} == {ToolState.OFF}
    assert (authored.on, authored.tools, authored.loaded_tokens) == (False, None, 0)


def test_web_search_is_off_and_adds_nothing_until_the_instance_holds_its_backends_key(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[BuiltInGroup, BuiltInGroup]:
        hub = hub_at(tmp_path / "hub")
        instance_id, _ = await stopped_instance(hub)
        without = (await toolset_of(hub, instance_id)).built_in_groups[0]
        hold_search_key(hub, instance_id)
        return without, (await toolset_of(hub, instance_id)).built_in_groups[0]

    without, held = asyncio.run(scenario())

    unavailable = tool(without, "web_search")
    assert (unavailable.state, unavailable.source) == (ToolState.OFF, ToolStateSource.UNAVAILABLE)
    assert without.loaded_tokens == loaded_tokens(without)
    assert (tool(held, "web_search").state, tool(held, "web_search").source) == (
        ToolState.LOADED,
        ToolStateSource.GROUP,
    )
    assert held.loaded_tokens == without.loaded_tokens + tool(held, "web_search").tokens


def test_the_toolset_lists_a_servers_tools_as_the_last_turn_connected_to_it_listed_them(
    tmp_path: Path,
) -> None:
    library = echo("look", default_state=ToolState.SEARCHABLE)
    changed = LockedMcpServer(server=library, tool_states={"post": ToolState.OFF})

    async def scenario() -> InstanceToolsetResult:
        hub = hub_at(tmp_path / "hub")
        instance_id, instance = await stopped_instance(hub)
        write_lock(instance.path, SkillsLock(mcp_servers={"echo": changed}))
        await turn(instance, [], PermissionMode.ASK)
        later = LockedMcpServer(server=echo(default_state=ToolState.LOADED))
        write_lock(instance.path, SkillsLock(mcp_servers={"echo": changed, "later": later}))
        return await toolset_of(hub, instance_id)

    toolset = asyncio.run(scenario())

    echo_group, later = toolset.mcp_server_groups
    assert (echo_group.server, echo_group.on, echo_group.default_state) == (
        "echo",
        True,
        ToolState.SEARCHABLE,
    )
    assert [(each.name, each.state, each.source) for each in echo_group.tools or ()] == [
        ("arguments", ToolState.SEARCHABLE, ToolStateSource.GROUP),
        ("environment", ToolState.SEARCHABLE, ToolStateSource.GROUP),
        ("headers", ToolState.SEARCHABLE, ToolStateSource.GROUP),
        ("look", ToolState.LOADED, ToolStateSource.LIBRARY),
        ("post", ToolState.OFF, ToolStateSource.INSTANCE),
    ]
    assert tool(echo_group, "look").description == "Repeat a line of text."
    assert echo_group.loaded_tokens == tool(echo_group, "look").tokens > 0
    assert (later.server, later.default_state, later.tools, later.loaded_tokens) == (
        "later",
        ToolState.LOADED,
        None,
        0,
    )


def test_a_record_that_does_not_parse_lists_no_tools_and_the_hub_still_lists_the_instance(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[InstanceToolsetResult, InstanceListResult]:
        hub = hub_at(tmp_path / "hub")
        instance_id, instance = await stopped_instance(hub)
        write_lock(instance.path, SkillsLock(mcp_servers={"echo": LockedMcpServer(server=echo())}))
        state = instance.manifest.state_dir
        state.mkdir(parents=True, exist_ok=True)
        for name in (MCP_TOOLS_NAME, AUTHORED_TOOLS_NAME):
            (state / name).write_text("{not json", encoding="utf-8")
        listed = await hub_client(hub).call(INSTANCE_LIST, InstanceListCommand())
        assert isinstance(listed, InstanceListResult)
        return await toolset_of(hub, instance_id), listed

    toolset, listed = asyncio.run(scenario())

    [server] = toolset.mcp_server_groups
    assert server.tools is None
    assert toolset.built_in_groups[-1].tools is None
    assert len(listed.instances) == 1


def test_kinby_reads_an_instances_toolset_picks_profiles_and_vault_without_vault_values(
    tmp_path, monkeypatch, capsys
):
    async def scenario() -> dict[str, str]:
        printed = {}
        async with home_hub(tmp_path) as hub:
            hub.hub.browser_profiles.save(
                hub.alice, "LinkedIn", "www.linkedin.com", {"cookies": [], "origins": []}
            )
            await hub_client(hub.hub).call(BROWSER_VAULT_ADD, _linkedin(hub.alice))
            as_home(monkeypatch, hub)
            for method in (
                "instance.toolset.get",
                "instance.picks.get",
                "browser.profile.list",
                "browser.vault.list",
            ):
                assert await kinby("hub", "read", method, f"instance_id={hub.alice}") == 0
                printed[method] = capsys.readouterr().out
        return printed

    printed = asyncio.run(scenario())

    toolset = InstanceToolsetResult.model_validate_json(printed["instance.toolset.get"])
    assert (toolset.search.variable, toolset.browser) == ("BRAVE_API_KEY", False)
    defaults = toolset.built_in_groups[0]
    assert (defaults.group, tool(defaults, "bash").state) == (
        BuiltInToolGroup.DEFAULTS,
        ToolState.LOADED,
    )
    assert json.loads(printed["instance.picks.get"])["mcp_servers"] == []
    assert json.loads(printed["browser.profile.list"]) == {
        "profiles": [{"name": "LinkedIn", "site": "www.linkedin.com"}]
    }
    assert json.loads(printed["browser.vault.list"]) == {
        "entries": [{"name": "LinkedIn", "site": "https://www.linkedin.com"}]
    }
    for value in (_USERNAME, _PASSWORD, _SEED):
        assert not any(value in output for output in printed.values())


def test_the_home_instance_is_refused_the_profile_writes(tmp_path):
    async def scenario() -> list[dict[str, object]]:
        async with home_hub(tmp_path) as hub:
            alice = str(hub.alice)
            return await answers(
                hub.route(hub.home),
                hub.home_token,
                (
                    "browser.profile.rename",
                    {"instance_id": alice, "name": "LinkedIn", "new_name": "Work"},
                ),
                ("browser.profile.delete", {"instance_id": alice, "name": "LinkedIn"}),
            )

    writes = asyncio.run(scenario())

    assert [error_code(write) for write in writes] == [ErrorCode.PERMISSION_DENIED.value] * 2
