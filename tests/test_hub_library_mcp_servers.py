"""The library keeps MCP servers beside skills and factories: added, read, edited and deleted."""

import asyncio

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
from tests.test_hub_library import library

GITHUB = McpServer.model_validate(
    {
        "transport": {
            "type": "url",
            "url": "https://api.githubcopilot.com/mcp/",
            "headers": {"Authorization": "GITHUB_AUTHORIZATION"},
        },
        "include": ["get_issue", "list_issues"],
    }
)
FETCH = McpServer.model_validate(
    {
        "transport": {"type": "uvx", "package": "mcp-server-fetch", "args": ["--ignore-robots"]},
        "env": ["FETCH_USER_AGENT"],
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


@pytest.mark.parametrize(
    ("server", "reason"),
    [
        ({"transport": {"type": "npx", "package": "@acme/server"}}, "'url', 'uvx'"),
        ({"transport": {"type": "uvx", "package": "a"}, "env": ["PATH"]}, "PATH"),
        ({"transport": {"type": "uvx", "package": "a"}, "env": ["LD_PRELOAD"]}, "LD_PRELOAD"),
        ({"transport": {"type": "uvx", "package": "a"}, "env": ["PYTHONPATH"]}, "PYTHONPATH"),
        ({"transport": {"type": "uvx", "package": "--index-url=http://evil"}}, "package"),
    ],
    ids=["npx", "path", "ld-preload", "pythonpath", "uvx-option"],
)
def test_another_transport_or_an_env_key_that_hijacks_the_process_is_refused(
    tmp_path, server, reason
):
    hub = hub_at(tmp_path / "hub")
    command = {"name": "evil", "server": server, "hash": None}

    answer = asyncio.run(
        hub.dispatcher.dispatch(LIBRARY_MCP_SERVER_WRITE.name, command, HUB_SCOPES)
    )

    assert isinstance(answer, ErrorEnvelope), answer
    assert answer.code is ErrorCode.INVALID_ARGUMENT
    assert reason in answer.message
    assert mcp_servers(hub) == []
    assert not (hub.directory / "mcp-servers").exists()


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
