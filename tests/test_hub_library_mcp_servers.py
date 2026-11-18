"""The library keeps MCP servers beside skills and factories: added, read, edited and deleted."""

import asyncio
import json

import pytest

from kinby.cli.client import ContractClient
from kinby.contracts import (
    HUB_SCOPES,
    LIBRARY_MCP_SERVER_DELETE,
    LIBRARY_MCP_SERVER_READ,
    LIBRARY_MCP_SERVER_WRITE,
    ErrorCode,
    ErrorEnvelope,
    FileHash,
    LibraryEntry,
    LibraryKind,
    LibraryListResult,
    LibraryMcpServerDeleteCommand,
    LibraryMcpServerReadCommand,
    LibraryMcpServerWriteCommand,
    LibrarySource,
    McpServer,
    McpServerResult,
)
from kinby.hub import Hub
from tests.test_hub import hub_at, hub_client
from tests.test_hub_library import library, reopened

GITHUB = McpServer.model_validate(
    {
        "entry": {
            "type": "http",
            "url": "https://api.githubcopilot.com/mcp/",
            "headers": {"Authorization": "Bearer ${GITHUB_TOKEN}"},
        },
        "include": ["get_issue", "list_issues"],
    }
)
FETCH = McpServer.model_validate(
    {
        "entry": {
            "type": "stdio",
            "command": "uvx",
            "args": ["mcp-server-fetch", "--ignore-robots"],
            "env": {"FETCH_USER_AGENT": "${FETCH_USER_AGENT:-kinby}"},
        },
        "include": ["fetch"],
    }
)


def write(
    client: ContractClient, name: str, server: McpServer, hash: FileHash | None
) -> McpServerResult | ErrorEnvelope:
    result = asyncio.run(
        client.call(
            LIBRARY_MCP_SERVER_WRITE,
            LibraryMcpServerWriteCommand(name=name, server=server, hash=hash),
        )
    )
    assert isinstance(result, McpServerResult | ErrorEnvelope), result
    return result


def read(client: ContractClient, name: str) -> McpServerResult | ErrorEnvelope:
    result = asyncio.run(
        client.call(LIBRARY_MCP_SERVER_READ, LibraryMcpServerReadCommand(name=name))
    )
    assert isinstance(result, McpServerResult | ErrorEnvelope), result
    return result


def mcp_servers(hub: Hub) -> list[LibraryEntry]:
    return [entry for entry in library(hub) if entry.kind is LibraryKind.MCP_SERVER]


def test_an_http_and_a_uvx_server_written_on_the_hub_are_listed_as_mcp_servers(tmp_path):
    hub = hub_at(tmp_path / "hub")
    client = hub_client(hub)

    written = [write(client, "github", GITHUB, None), write(client, "fetch", FETCH, None)]

    assert all(isinstance(result, McpServerResult) for result in written), written
    assert mcp_servers(hub) == [
        LibraryEntry(
            kind=LibraryKind.MCP_SERVER, name=name, description=None, source=LibrarySource.WRITTEN
        )
        for name in ("fetch", "github")
    ]
    fetch = read(client, "fetch")
    assert isinstance(fetch, McpServerResult), fetch
    assert (fetch.name, fetch.server) == ("fetch", FETCH)


def dispatched_write(hub: Hub, entry: dict[str, object]) -> object:
    command = {"name": "pasted", "server": {"entry": entry}, "hash": None}
    return asyncio.run(hub.dispatcher.dispatch(LIBRARY_MCP_SERVER_WRITE.name, command, HUB_SCOPES))


@pytest.mark.parametrize(
    ("entry", "stored"),
    [
        ({"url": "https://mcp.example.com/mcp"}, "http"),
        ({"command": "uvx", "args": ["mcp-server-fetch"]}, "stdio"),
    ],
    ids=["url", "command"],
)
def test_an_entry_without_a_type_is_stored_with_the_type_its_fields_name(tmp_path, entry, stored):
    hub = hub_at(tmp_path / "hub")

    written = dispatched_write(hub, entry)

    assert isinstance(written, McpServerResult), written
    saved = json.loads((hub.directory / "mcp-servers" / "pasted.json").read_bytes())
    assert saved["entry"]["type"] == stored


@pytest.mark.parametrize(
    ("entry", "field", "reason"),
    [
        ({"type": "sse", "url": "https://mcp.example.com/sse"}, "server.entry", "deprecated"),
        (
            {"command": "npx", "args": ["@acme/server"]},
            "server.entry.stdio.command",
            "uvx",
        ),
        (
            {"command": "uvx", "args": ["a"], "env": {"PATH": "/evil"}},
            "server.entry.stdio.env.PATH.[key]",
            "PATH",
        ),
        (
            {"command": "uvx", "args": ["a"], "env": {"LD_PRELOAD": "evil.so"}},
            "server.entry.stdio.env.LD_PRELOAD.[key]",
            "LD_PRELOAD",
        ),
        (
            {"command": "uvx", "args": ["a"], "env": {"PYTHONPATH": "/evil"}},
            "server.entry.stdio.env.PYTHONPATH.[key]",
            "PYTHONPATH",
        ),
        (
            {"command": "uvx", "args": ["--index-url=http://evil", "a"]},
            "server.entry.stdio.args",
            "package",
        ),
        ({"command": "uvx", "args": ["${PACKAGE}"]}, "server.entry.stdio.args", "package"),
        ({"command": "uvx"}, "server.entry.stdio.args", "package"),
    ],
    ids=[
        "sse",
        "npx",
        "path",
        "ld-preload",
        "pythonpath",
        "uvx-option",
        "uvx-variable",
        "no-package",
    ],
)
def test_an_entry_kinby_cannot_run_or_whose_env_key_hijacks_the_process_is_refused(
    tmp_path, entry, field, reason
):
    hub = hub_at(tmp_path / "hub")

    answer = dispatched_write(hub, entry)

    assert isinstance(answer, ErrorEnvelope), answer
    assert answer.code is ErrorCode.INVALID_ARGUMENT
    assert list(answer.fields) == [field]
    assert reason in answer.fields[field]
    assert not answer.fields[field].startswith("Value error")
    assert mcp_servers(hub) == []
    assert not (hub.directory / "mcp-servers").exists()


def test_a_definition_in_the_old_shape_is_refused(tmp_path):
    hub = hub_at(tmp_path / "hub")
    old = {"transport": {"type": "uvx", "package": "mcp-server-fetch"}, "include": ["fetch"]}
    command = {"name": "fetch", "server": old, "hash": None}

    answer = asyncio.run(
        hub.dispatcher.dispatch(LIBRARY_MCP_SERVER_WRITE.name, command, HUB_SCOPES)
    )

    assert isinstance(answer, ErrorEnvelope), answer
    assert answer.code is ErrorCode.INVALID_ARGUMENT
    assert "transport" in answer.message


@pytest.mark.parametrize(
    ("old", "converted"),
    [
        (
            {
                "transport": {
                    "type": "url",
                    "url": "https://api.githubcopilot.com/mcp/",
                    "headers": {"Authorization": "GITHUB_AUTHORIZATION"},
                },
                "include": ["get_issue"],
            },
            {
                "entry": {
                    "type": "http",
                    "url": "https://api.githubcopilot.com/mcp/",
                    "headers": {"Authorization": "${GITHUB_AUTHORIZATION}"},
                },
                "include": ["get_issue"],
            },
        ),
        (
            {
                "transport": {"type": "uvx", "package": "mcp-server-fetch", "args": ["--raw"]},
                "env": ["FETCH_USER_AGENT"],
                "include": ["fetch"],
            },
            {
                "entry": {
                    "type": "stdio",
                    "command": "uvx",
                    "args": ["mcp-server-fetch", "--raw"],
                    "env": {"FETCH_USER_AGENT": "${FETCH_USER_AGENT}"},
                },
                "include": ["fetch"],
            },
        ),
    ],
    ids=["url", "uvx"],
)
def test_the_hub_converts_a_library_definition_in_the_old_shape(tmp_path, old, converted):
    hub = hub_at(tmp_path / "hub")
    (hub.directory / "mcp-servers").mkdir()
    (hub.directory / "mcp-servers" / "kept.json").write_text(json.dumps(old), encoding="utf-8")

    hub = reopened(hub)

    kept = read(hub_client(hub), "kept")
    assert isinstance(kept, McpServerResult), kept
    assert kept.server == McpServer.model_validate(converted)


def test_a_name_the_library_already_holds_is_refused_for_a_new_server(tmp_path):
    client = hub_client(hub_at(tmp_path / "hub"))
    write(client, "fetch", FETCH, None)

    answer = write(client, "fetch", GITHUB, None)

    assert isinstance(answer, ErrorEnvelope), answer
    assert answer.fields == {"name": 'The library already holds an MCP server "fetch".'}
    fetch = read(client, "fetch")
    assert isinstance(fetch, McpServerResult), fetch
    assert fetch.server == FETCH


def test_a_server_is_edited_and_deleted_only_as_it_was_read(tmp_path):
    hub = hub_at(tmp_path / "hub")
    client = hub_client(hub)
    added = write(client, "fetch", FETCH, None)
    assert isinstance(added, McpServerResult), added
    narrowed = FETCH.model_copy(update={"include": []})

    edited = write(client, "fetch", narrowed, added.hash)
    stale_edit = write(client, "fetch", FETCH, added.hash)
    stale_delete = asyncio.run(
        client.call(
            LIBRARY_MCP_SERVER_DELETE, LibraryMcpServerDeleteCommand(name="fetch", hash=added.hash)
        )
    )

    assert isinstance(edited, McpServerResult), edited
    assert edited.server == narrowed
    assert isinstance(stale_edit, ErrorEnvelope)
    assert isinstance(stale_delete, ErrorEnvelope)
    assert (stale_edit.code, stale_delete.code) == (ErrorCode.STALE, ErrorCode.STALE)

    deleted = asyncio.run(
        client.call(
            LIBRARY_MCP_SERVER_DELETE,
            LibraryMcpServerDeleteCommand(name="fetch", hash=edited.hash),
        )
    )

    assert isinstance(deleted, LibraryListResult), deleted
    assert [entry for entry in deleted.entries if entry.kind is LibraryKind.MCP_SERVER] == []
    gone = read(client, "fetch")
    assert isinstance(gone, ErrorEnvelope)
    assert gone.code is ErrorCode.NOT_FOUND
