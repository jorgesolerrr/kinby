"""The hub searches the MCP Registry and turns each latest record into the entry kinby would
write, against a local app standing in for the registry."""

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from aiohttp import web

from kinby.contracts import (
    MCP_REGISTRY_SEARCH,
    ErrorEnvelope,
    McpHttpEntry,
    McpRegistrySearchCommand,
    McpRegistrySearchResult,
    McpRegistryServer,
    McpRegistryVariable,
    McpStdioEntry,
)
from tests.test_hub import hub_at, hub_client

_OFFICIAL = "io.modelcontextprotocol.registry/official"


def listed(server: Mapping[str, object], *, latest: bool = True) -> dict[str, object]:
    return {
        "server": {"description": "A server.", "version": "1.0.0", **server},
        "_meta": {_OFFICIAL: {"status": "active", "isLatest": latest}},
    }


@dataclass
class FakeRegistry:
    """The records it lists for any search, and the queries it was sent."""

    servers: list[dict[str, object]] = field(default_factory=list)
    queries: list[dict[str, str]] = field(default_factory=list)

    async def search(self, request: web.Request) -> web.Response:
        self.queries.append(dict(request.query))
        return web.json_response({"servers": self.servers, "metadata": {"count": 0}})


@asynccontextmanager
async def serving(registry: FakeRegistry) -> AsyncIterator[str]:
    application = web.Application()
    application.router.add_get("/v0.1/servers", registry.search)
    runner = web.AppRunner(application)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        yield f"http://127.0.0.1:{site.port}"
    finally:
        await runner.cleanup()


async def search(directory: Path, url: str, words: str) -> McpRegistrySearchResult | ErrorEnvelope:
    client = hub_client(hub_at(directory, mcp_registry_url=url))
    result = await client.call(MCP_REGISTRY_SEARCH, McpRegistrySearchCommand(words=words))
    assert isinstance(result, McpRegistrySearchResult | ErrorEnvelope), result
    return result


def searched(
    directory: Path, registry: FakeRegistry, words: str = "acme"
) -> list[McpRegistryServer]:
    async def run() -> McpRegistrySearchResult | ErrorEnvelope:
        async with serving(registry) as url:
            return await search(directory, url, words)

    result = asyncio.run(run())
    assert isinstance(result, McpRegistrySearchResult), result
    return result.servers


def test_a_search_sends_only_its_words_and_returns_each_latest_record(tmp_path):
    remote = {"remotes": [{"type": "streamable-http", "url": "https://mcp.acme.com/mcp"}]}
    registry = FakeRegistry(
        [
            listed({"name": "com.acme/acme", "version": "1.0.0", **remote}, latest=False),
            listed({"name": "com.acme/acme", "description": "Acme.", "version": "1.1.0", **remote}),
        ]
    )

    servers = searched(tmp_path / "hub", registry, "acme issues")

    assert registry.queries == [{"search": "acme issues"}]
    assert servers == [
        McpRegistryServer(
            name="com.acme/acme",
            description="Acme.",
            version="1.1.0",
            entry=McpHttpEntry(url="https://mcp.acme.com/mcp"),
            reason=None,
            variables=[],
        )
    ]


def test_a_streamable_http_remote_becomes_an_http_entry_with_its_variables_as_secrets(tmp_path):
    remote = {
        "type": "streamable-http",
        "url": "https://{tenant}.acme.com/mcp",
        "variables": {"tenant": {"description": "Your Acme tenant.", "isRequired": True}},
        "headers": [
            {"name": "Authorization", "value": "Bearer {api_key}", "isSecret": True},
            {"name": "X-Acme-Region", "default": "eu"},
            {"name": "X-Client", "value": "kinby"},
        ],
    }
    registry = FakeRegistry([listed({"name": "com.acme/acme", "remotes": [remote]})])

    [server] = searched(tmp_path / "hub", registry)

    assert server.entry == McpHttpEntry(
        url="https://${TENANT}.acme.com/mcp",
        headers={
            "Authorization": "Bearer ${API_KEY}",
            "X-Acme-Region": "${X_ACME_REGION:-eu}",
            "X-Client": "kinby",
        },
    )
    assert server.variables == [
        McpRegistryVariable(name="TENANT", secret=False, description="Your Acme tenant."),
        McpRegistryVariable(name="API_KEY", secret=True, description=None),
    ]


def test_a_package_runs_through_its_launcher_with_its_environment_variables(tmp_path):
    environment = [
        {"name": "ACME_TOKEN", "isSecret": True, "isRequired": True, "description": "A token."},
        {"name": "ACME_REGION", "default": "eu"},
    ]
    registry = FakeRegistry(
        [
            listed(
                {
                    "name": "com.acme/node",
                    "packages": [
                        {
                            "registryType": "npm",
                            "identifier": "@acme/mcp",
                            "version": "2.0.0",
                            "transport": {"type": "stdio"},
                            "environmentVariables": environment,
                        }
                    ],
                }
            ),
            listed(
                {
                    "name": "com.acme/python",
                    "packages": [
                        {
                            "registryType": "pypi",
                            "identifier": "acme-mcp",
                            "version": "3.0.0",
                            "transport": {"type": "stdio"},
                        }
                    ],
                }
            ),
        ]
    )

    node, python = searched(tmp_path / "hub", registry)

    assert node.entry == McpStdioEntry(
        command="npx",
        args=["@acme/mcp@2.0.0"],
        env={"ACME_TOKEN": "${ACME_TOKEN}", "ACME_REGION": "${ACME_REGION:-eu}"},
    )
    assert node.variables == [
        McpRegistryVariable(name="ACME_TOKEN", secret=True, description="A token.")
    ]
    assert python.entry == McpStdioEntry(command="uvx", args=["acme-mcp@3.0.0"])


def test_a_record_with_a_remote_and_a_package_uses_the_remote(tmp_path):
    record = {
        "name": "com.acme/acme",
        "packages": [
            {
                "registryType": "npm",
                "identifier": "@acme/mcp",
                "version": "1.0.0",
                "transport": {"type": "stdio"},
            }
        ],
        "remotes": [
            {"type": "sse", "url": "https://mcp.acme.com/sse"},
            {"type": "streamable-http", "url": "https://mcp.acme.com/mcp"},
        ],
    }

    [server] = searched(tmp_path / "hub", FakeRegistry([listed(record)]))

    assert server.entry == McpHttpEntry(url="https://mcp.acme.com/mcp")


def test_a_record_kinby_cannot_run_comes_back_with_the_reason_and_no_entry(tmp_path):
    record = {
        "name": "com.acme/acme",
        "packages": [
            {"registryType": "oci", "identifier": "acme/mcp", "transport": {"type": "stdio"}},
            {
                "registryType": "pypi",
                "identifier": "acme-mcp",
                "version": "1.0.0",
                "transport": {"type": "streamable-http", "url": "http://localhost:8080/mcp"},
            },
        ],
        "remotes": [{"type": "sse", "url": "https://mcp.acme.com/sse"}],
    }
    hijacking = {
        "name": "com.acme/hijack",
        "packages": [
            {
                "registryType": "pypi",
                "identifier": "acme-mcp",
                "version": "1.0.0",
                "transport": {"type": "stdio"},
                "environmentVariables": [{"name": "PYTHONPATH", "isRequired": True}],
            }
        ],
    }

    registry = FakeRegistry([listed(record), listed(hijacking)])

    unrunnable, hijack = searched(tmp_path / "hub", registry)

    assert unrunnable.entry is None
    assert unrunnable.reason is not None
    assert "a remote over sse" in unrunnable.reason
    assert "a package from oci over stdio" in unrunnable.reason
    assert "a package from pypi over streamable-http" in unrunnable.reason
    assert hijack.entry is None
    assert hijack.reason is not None
    assert "PYTHONPATH" in hijack.reason


def test_a_registry_that_cannot_be_reached_fails_the_search_saying_so(tmp_path):
    async def run() -> McpRegistrySearchResult | ErrorEnvelope:
        async with serving(FakeRegistry()) as url:
            pass
        return await search(tmp_path / "hub", url, "acme")

    result = asyncio.run(run())

    assert isinstance(result, ErrorEnvelope), result
    assert "MCP Registry" in result.message
    assert "could not be reached" in result.message
