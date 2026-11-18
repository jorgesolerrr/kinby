"""An instance picks MCP servers from the library: a pinned copy in its lock, with the user's trust
in each server's read-only hints."""

import asyncio
import json
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID

from kinby.contracts import (
    INSTANCE_PICKS_GET,
    INSTANCE_PICKS_SET,
    INSTANCE_SECRETS_SET,
    INSTANCE_TOOLSET_GET,
    INSTANCE_UPDATE,
    ConfigActor,
    ErrorCode,
    ErrorEnvelope,
    InstancePicksGetCommand,
    InstancePicksResult,
    InstancePicksSetCommand,
    InstanceSecretsSetCommand,
    InstanceSetup,
    InstanceToolsetGetCommand,
    InstanceToolsetResult,
    InstanceUpdateCommand,
    LifecycleOperationResult,
    LoginState,
    McpServer,
    McpServerChoice,
    McpServerPick,
    McpSignInSetup,
    OperationState,
    PickState,
    SecretSetup,
)
from kinby.hub import Hub
from kinby.instance import inspect_instance
from kinby.instance.config_changes import ConfigChangeLog
from tests.mcp_oauth_server import OAuthMcpServer
from tests.test_hub import finished_operation, hold_search_key, hub_client
from tests.test_hub_library import reopened
from tests.test_hub_library_mcp_servers import FETCH
from tests.test_hub_pick_sync import _hub, _picks_step, _started, _update
from tests.test_hub_picks import _instance_id, _lock, _picks, _set
from tests.test_hub_setup import setup_of

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
        "fetch": {
            "server": FETCH.model_dump(mode="json"),
            "trustHints": False,
            "needsSignIn": False,
        }
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
        "needsSignIn": False,
    }
    assert _servers(_picks(hub_client(hub), directory)) == [
        McpServerPick(name="fetch", trust_hints=True, state=PickState.UP_TO_DATE)
    ]


def test_the_hub_converts_a_picks_locked_copy_in_the_old_shape(tmp_path):
    hub = _hub(tmp_path)
    directory = _started(hub)
    _library_server(hub, "fetch", FETCH)
    _pick_servers(hub, directory, McpServerChoice(name="fetch", trust_hints=True))
    lock = json.loads((directory / "skills-lock.json").read_bytes())
    lock["mcpServers"]["fetch"]["server"] = {
        "transport": {"type": "uvx", "package": "mcp-server-fetch", "args": ["--ignore-robots"]},
        "env": ["FETCH_USER_AGENT"],
        "include": ["fetch"],
    }
    (directory / "skills-lock.json").write_text(json.dumps(lock), encoding="utf-8")

    hub = reopened(hub)

    assert _locked_servers(directory)["fetch"] == {
        "server": {
            "entry": {
                "type": "stdio",
                "command": "uvx",
                "args": ["mcp-server-fetch", "--ignore-robots"],
                "env": {"FETCH_USER_AGENT": "${FETCH_USER_AGENT}"},
            },
            "include": ["fetch"],
        },
        "trustHints": True,
        "needsSignIn": False,
    }
    assert _servers(_picks(hub_client(hub), directory)) == [
        McpServerPick(name="fetch", trust_hints=True, state=PickState.BEHIND)
    ]


def test_a_variable_a_picked_server_needs_and_the_instance_lacks_is_pending_setup(tmp_path):
    hub = _hub(tmp_path)
    directory = _started(hub)
    instance_id = _instance_id(directory)
    hold_search_key(hub, instance_id)
    needy = McpServer.model_validate(
        {
            "entry": {
                "url": "https://mcp.example.com/${ROUTE:-mcp}",
                "headers": {"Authorization": "Bearer ${FETCH_TOKEN}"},
            },
            "include": ["fetch"],
        }
    )
    _library_server(hub, "fetch", FETCH)
    _library_server(hub, "remote", needy)
    _pick_servers(hub, directory, McpServerChoice(name="fetch"), McpServerChoice(name="remote"))

    async def scenario() -> tuple[InstanceSetup, InstanceSetup]:
        missing = await setup_of(hub, instance_id)
        replaced = await hub_client(hub).call(
            INSTANCE_SECRETS_SET,
            InstanceSecretsSetCommand(instance_id=instance_id, secrets={"FETCH_TOKEN": "tok"}),
        )
        assert isinstance(replaced, LifecycleOperationResult)
        await finished_operation(hub_client(hub), replaced)
        return missing, await setup_of(hub, instance_id)

    missing, filled = asyncio.run(scenario())

    token = SecretSetup(
        name="FETCH_TOKEN",
        variable="FETCH_TOKEN",
        label="FETCH_TOKEN",
        required=True,
        is_set=False,
        mcp_servers=["remote"],
    )
    assert [secret for secret in missing.secrets if secret.mcp_servers] == [token]
    assert missing.pending
    assert [secret for secret in filled.secrets if secret.mcp_servers] == [
        token.model_copy(update={"is_set": True})
    ]
    assert not filled.pending


def _remote(url: str, headers: dict[str, str] | None = None) -> McpServer:
    return McpServer.model_validate(
        {"entry": {"url": url, "headers": headers or {}}, "include": ["look"]}
    )


def _setup_and_toolset(hub: Hub, instance_id: UUID) -> tuple[InstanceSetup, InstanceToolsetResult]:
    async def scenario() -> tuple[InstanceSetup, InstanceToolsetResult]:
        toolset = await hub_client(hub).call(
            INSTANCE_TOOLSET_GET, InstanceToolsetGetCommand(instance_id=instance_id)
        )
        assert isinstance(toolset, InstanceToolsetResult), toolset
        return await setup_of(hub, instance_id), toolset

    return asyncio.run(scenario())


def test_a_picked_server_that_asks_for_a_sign_in_records_it_and_its_sign_in_is_pending(
    tmp_path, oauth_server: OAuthMcpServer
):
    hub = _hub(tmp_path)
    directory = _started(hub)
    instance_id = _instance_id(directory)
    hold_search_key(hub, instance_id)
    _library_server(hub, "cockpit", _remote(oauth_server.url))

    picked = _pick_servers(hub, directory, McpServerChoice(name="cockpit"))

    pending = McpServerPick(
        name="cockpit", trust_hints=False, state=PickState.UP_TO_DATE, sign_in=LoginState.PENDING
    )
    assert _servers(picked) == [pending]
    assert _locked_servers(directory)["cockpit"]["needsSignIn"] is True
    setup, toolset = _setup_and_toolset(hub, instance_id)
    assert setup.mcp_sign_ins == [McpSignInSetup(server="cockpit", state=LoginState.PENDING)]
    assert setup.pending
    assert toolset.mcp_servers == [pending]


def test_a_local_server_an_open_one_and_one_with_its_own_authorization_need_no_sign_in(
    tmp_path, oauth_server: OAuthMcpServer, echo_port: int
):
    hub = _hub(tmp_path)
    directory = _started(hub)
    instance_id = _instance_id(directory)
    hold_search_key(hub, instance_id)
    _library_server(hub, "fetch", FETCH)
    _library_server(hub, "open", _remote(f"http://127.0.0.1:{echo_port}/mcp"))
    _library_server(hub, "keyed", _remote(oauth_server.url, {"authorization": "Bearer api-key"}))

    picked = _pick_servers(
        hub,
        directory,
        McpServerChoice(name="fetch"),
        McpServerChoice(name="open"),
        McpServerChoice(name="keyed"),
    )

    assert [server.sign_in for server in _servers(picked)] == [None, None, None]
    assert [server["needsSignIn"] for server in _locked_servers(directory).values()] == [
        False,
        False,
        False,
    ]
    setup, _ = _setup_and_toolset(hub, instance_id)
    assert setup.mcp_sign_ins == []
    assert not setup.pending


def test_a_server_the_hub_cannot_reach_is_picked_and_an_update_asks_it_again(
    tmp_path, oauth_server: OAuthMcpServer
):
    hub = _hub(tmp_path)
    directory = _started(hub)
    instance_id = _instance_id(directory)
    _library_server(hub, "cockpit", _remote("${COCKPIT_URL}"))

    picked = _pick_servers(hub, directory, McpServerChoice(name="cockpit"))

    assert _servers(picked) == [FETCHED.model_copy(update={"name": "cockpit"})]

    async def reachable() -> None:
        client = hub_client(hub)
        replaced = await client.call(
            INSTANCE_SECRETS_SET,
            InstanceSecretsSetCommand(
                instance_id=instance_id, secrets={"COCKPIT_URL": oauth_server.url}
            ),
        )
        assert isinstance(replaced, LifecycleOperationResult)
        await finished_operation(client, replaced)

    asyncio.run(reachable())
    _update(hub, directory)

    assert _servers(_picks(hub_client(hub), directory)) == [
        McpServerPick(
            name="cockpit",
            trust_hints=False,
            state=PickState.UP_TO_DATE,
            sign_in=LoginState.PENDING,
        )
    ]


class _KeyedHandler(BaseHTTPRequestHandler):
    """A server that takes an API key in X-Api-Key, and asks anyone without it to sign in."""

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers["Content-Length"]))
        if self.headers.get("X-Api-Key") == "key":
            self.send_response(200)
        else:
            self.send_response(401)
            self.send_header(
                "WWW-Authenticate",
                'Bearer resource_metadata="http://127.0.0.1/.well-known/oauth-protected-resource"',
            )
        self.send_header("Content-Length", "0")
        self.end_headers()


@contextmanager
def _keyed_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _KeyedHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/mcp"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_a_server_is_asked_with_the_headers_its_entry_sets(tmp_path):
    hub = _hub(tmp_path)
    directory = _started(hub)
    with (directory / ".env").open("a", encoding="utf-8") as env:
        env.write("API_KEY=key\n")
    with _keyed_server() as url:
        _library_server(hub, "keyed", _remote(url, {"X-Api-Key": "${API_KEY}"}))

        picked = _pick_servers(hub, directory, McpServerChoice(name="keyed"))

    assert _servers(picked) == [FETCHED.model_copy(update={"name": "keyed"})]


def test_a_pick_changed_while_an_update_asks_its_servers_is_kept(tmp_path):
    hub = _hub(tmp_path)
    directory = _started(hub)
    instance_id = _instance_id(directory)
    listening = socket.socket()
    listening.bind(("127.0.0.1", 0))
    # Not listening yet, so the pick's own asking is refused at once.
    remote = _remote(f"http://127.0.0.1:{listening.getsockname()[1]}/mcp")
    _library_server(hub, "cockpit", remote)
    _pick_servers(hub, directory, McpServerChoice(name="cockpit"))
    changed = remote.model_copy(update={"include": ["look", "post"]})
    _library_server(hub, "cockpit", changed)
    asked, answer = threading.Event(), threading.Event()

    def hold() -> None:
        connection, _ = listening.accept()
        asked.set()
        answer.wait()
        connection.close()

    listening.listen()
    holder = threading.Thread(target=hold)
    holder.start()

    async def scenario() -> tuple[object, OperationState]:
        client = hub_client(hub)
        accepted = await client.call(
            INSTANCE_UPDATE, InstanceUpdateCommand(instance_id=instance_id, revision="v2")
        )
        assert isinstance(accepted, LifecycleOperationResult), accepted
        assert await asyncio.to_thread(asked.wait, 30)
        read = await client.call(
            INSTANCE_PICKS_GET, InstancePicksGetCommand(instance_id=instance_id)
        )
        assert isinstance(read, InstancePicksResult), read
        trusted = await client.call(
            INSTANCE_PICKS_SET,
            InstancePicksSetCommand(
                instance_id=instance_id,
                skills=[],
                mcp_servers=[McpServerChoice(name="cockpit", trust_hints=True)],
                hash=read.hash,
            ),
        )
        answer.set()
        return trusted, (await finished_operation(client, accepted)).state

    try:
        trusted, state = asyncio.run(scenario())
    finally:
        answer.set()
        holder.join()
        listening.close()

    assert isinstance(trusted, InstancePicksResult), trusted
    assert state is OperationState.SUCCEEDED
    assert _locked_servers(directory)["cockpit"] == {
        "server": changed.model_dump(mode="json"),
        "trustHints": True,
        "needsSignIn": False,
    }
