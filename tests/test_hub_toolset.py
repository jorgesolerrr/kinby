"""An instance's toolset, which the hub reads from its files whether it runs or not. The home
instance reads it beside the instance's MCP servers, profiles and vault entries, and never writes
a profile or a vault entry."""

import asyncio
import json

from langchain_core.messages import AIMessageChunk

from kinby.contracts import (
    BROWSER_VAULT_ADD,
    INSTANCE_PICKS_SET,
    INSTANCE_TOOLSET_GET,
    ApprovalRequested,
    AuthoredTool,
    DefaultTool,
    ErrorCode,
    InstancePicksSetCommand,
    InstanceToolsetGetCommand,
    InstanceToolsetResult,
    McpServerChoice,
    McpServerPick,
    PickState,
    SearchBackend,
    SearchSetup,
    ToolResult,
)
from kinby.instance import inspect_instance
from kinby.instance.read_only_tools import set_read_only
from tests.test_gate import ScriptedModel, _instance, _run
from tests.test_hub import hub_client
from tests.test_hub_browser import _LIMITS, browser_hub
from tests.test_hub_home_reads import (
    answers,
    as_the_home_instance,
    error_code,
    home_hub,
    hub_read_call,
)
from tests.test_hub_library_mcp_servers import FETCH
from tests.test_hub_mcp_picks import _library_server
from tests.test_hub_vault import _PASSWORD, _SEED, _USERNAME, _linkedin

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

    assert toolset == InstanceToolsetResult(
        default_tools=[
            DefaultTool(name=name, on=name != "bash")
            for name in ("read", "write", "edit", "grep", "glob", "bash", "web_search", "web_fetch")
        ],
        search=SearchSetup(backend=SearchBackend.BRAVE, variable="BRAVE_API_KEY", is_set=False),
        mcp_servers=[McpServerPick(name="fetch", trust_hints=True, state=PickState.UP_TO_DATE)],
        packages=["httpx>=0.27"],
        authored_tools=[
            AuthoredTool(name="lookup", read_only=True),
            AuthoredTool(name="post", read_only=False),
        ],
        browser=True,
    )


def test_kinby_reads_an_instances_toolset_picks_profiles_and_vault_without_vault_values(
    tmp_path, monkeypatch
):
    async def scenario() -> dict[str, ToolResult]:
        async with home_hub(tmp_path) as hub:
            hub.hub.browser_profiles.save(
                hub.alice, "LinkedIn", "www.linkedin.com", {"cookies": [], "origins": []}
            )
            await hub_client(hub.hub).call(BROWSER_VAULT_ADD, _linkedin(hub.alice))
            as_the_home_instance(monkeypatch, hub.route(hub.home))
            monkeypatch.setenv("KINBY_CONTROL_TOKEN", hub.home_token)
            alice: dict[str, object] = {"instance_id": str(hub.alice)}
            model = ScriptedModel(
                [
                    AIMessageChunk(
                        content="",
                        tool_calls=[
                            hub_read_call("toolset", "instance.toolset.get", alice),
                            hub_read_call("picks", "instance.picks.get", alice),
                            hub_read_call("profiles", "browser.profile.list", alice),
                            hub_read_call("vault", "browser.vault.list", alice),
                        ],
                    ),
                    AIMessageChunk(content="Alice can search the web."),
                ]
            )
            _, payloads = await _run(_instance(tmp_path), model)
        assert not any(isinstance(payload, ApprovalRequested) for payload in payloads)
        return {payload.call_id: payload for payload in payloads if isinstance(payload, ToolResult)}

    results = asyncio.run(scenario())

    assert not any(result.error for result in results.values()), results
    toolset = InstanceToolsetResult.model_validate_json(results["toolset"].output)
    assert (toolset.search.variable, toolset.browser) == ("BRAVE_API_KEY", False)
    assert json.loads(results["picks"].output)["mcp_servers"] == []
    assert json.loads(results["profiles"].output) == {
        "profiles": [{"name": "LinkedIn", "site": "www.linkedin.com"}]
    }
    assert json.loads(results["vault"].output) == {
        "entries": [{"name": "LinkedIn", "site": "https://www.linkedin.com"}]
    }
    for value in (_USERNAME, _PASSWORD, _SEED):
        assert not any(value in result.output for result in results.values())


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
