"""An instance picks MCP servers from the library: a pinned copy in its lock, with the user's trust
in each server's read-only hints."""

import json
from pathlib import Path

from kinby.contracts import (
    ConfigActor,
    ErrorCode,
    ErrorEnvelope,
    InstancePicksResult,
    McpServer,
    McpServerChoice,
    McpServerPick,
    PickState,
)
from kinby.hub import Hub
from kinby.instance import inspect_instance
from kinby.instance.config_changes import ConfigChangeLog
from tests.test_hub import hub_client
from tests.test_hub_library_mcp_servers import FETCH
from tests.test_hub_pick_sync import _hub, _picks_step, _started, _update
from tests.test_hub_picks import _lock, _picks, _set

FETCHED = McpServerPick(name="fetch", trust_hints=False, state=PickState.UP_TO_DATE)


def _library_server(hub: Hub, name: str, server: McpServer) -> None:
    folder = hub.directory / "mcp-servers"
    folder.mkdir(exist_ok=True)
    (folder / f"{name}.json").write_text(server.model_dump_json(), encoding="utf-8")


def _pick_servers(hub: Hub, directory: Path, *choices: McpServerChoice) -> object:
    client = hub_client(hub)
    return _set(client, directory, [], _picks(client, directory).hash, list(choices))


def _servers(answer: object) -> list[McpServerPick]:
    assert isinstance(answer, InstancePicksResult), answer
    return answer.mcp_servers


def _locked_servers(directory: Path) -> dict[str, dict[str, object]]:
    return json.loads((directory / "skills-lock.json").read_text(encoding="utf-8"))["mcpServers"]


def test_a_picked_mcp_server_is_pinned_in_the_lock_without_trusting_its_hints(tmp_path):
    hub = _hub(tmp_path)
    directory = _started(hub)
    _library_server(hub, "fetch", FETCH)

    picked = _pick_servers(hub, directory, McpServerChoice(name="fetch"))

    assert _servers(picked) == [FETCHED]
    assert _locked_servers(directory) == {
        "fetch": {"server": FETCH.model_dump(mode="json"), "trustHints": False}
    }
    assert _picks(hub_client(hub), directory) == picked


def test_trusting_a_servers_hints_is_a_config_change_the_user_made(tmp_path):
    hub = _hub(tmp_path)
    directory = _started(hub)
    _library_server(hub, "fetch", FETCH)
    _pick_servers(hub, directory, McpServerChoice(name="fetch"))

    trusted = _pick_servers(hub, directory, McpServerChoice(name="fetch", trust_hints=True))

    assert _servers(trusted) == [FETCHED.model_copy(update={"trust_hints": True})]
    state = inspect_instance(directory).manifest.state_dir
    trusting, _ = ConfigChangeLog(state).history(None, 10)
    assert (trusting.file, trusting.actor) == ("skills-lock.json", ConfigActor.APP)
    assert '+      "trustHints": true' in trusting.diff


def test_dropping_an_mcp_server_removes_it_from_the_lock_and_keeps_the_skills(tmp_path):
    hub = _hub(tmp_path)
    directory = _started(hub)
    _library_server(hub, "fetch", FETCH)
    client = hub_client(hub)
    _set(client, directory, ["tdd"], None, [McpServerChoice(name="fetch")])

    dropped = _set(client, directory, ["tdd"], _picks(client, directory).hash, [])

    assert _servers(dropped) == []
    assert "mcpServers" not in json.loads((directory / "skills-lock.json").read_bytes())
    assert set(_lock(directory)) == {"tdd"}


def test_picking_an_mcp_server_the_library_does_not_hold_fails_before_anything_changes(tmp_path):
    hub = _hub(tmp_path)
    directory = _started(hub)
    client = hub_client(hub)

    refused = _set(client, directory, ["tdd"], None, [McpServerChoice(name="fetch")])

    assert 'The library has no MCP server "fetch".' in _refused_servers(refused)
    assert _picks(client, directory).picks == []


def _refused_servers(answer: object) -> str:
    assert isinstance(answer, ErrorEnvelope), answer
    assert answer.code is ErrorCode.INVALID_ARGUMENT
    return answer.fields["mcp_servers"]


def test_a_library_change_to_a_picked_server_reaches_the_instance_only_through_an_update(tmp_path):
    hub = _hub(tmp_path)
    directory = _started(hub)
    _library_server(hub, "fetch", FETCH)
    _pick_servers(hub, directory, McpServerChoice(name="fetch", trust_hints=True))
    changed = FETCH.model_copy(update={"include": ["fetch", "fetch_html"]})
    _library_server(hub, "fetch", changed)

    assert _servers(_picks(hub_client(hub), directory)) == [
        McpServerPick(name="fetch", trust_hints=True, state=PickState.BEHIND)
    ]
    assert _locked_servers(directory)["fetch"]["server"] == FETCH.model_dump(mode="json")

    outcome = _update(hub, directory)

    assert _picks_step(outcome) == "Wrote fetch."
    assert _locked_servers(directory)["fetch"] == {
        "server": changed.model_dump(mode="json"),
        "trustHints": True,
    }
    assert _servers(_picks(hub_client(hub), directory)) == [
        McpServerPick(name="fetch", trust_hints=True, state=PickState.UP_TO_DATE)
    ]
