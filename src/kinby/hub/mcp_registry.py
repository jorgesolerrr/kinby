"""Search the MCP Registry and turn each latest ``server.json`` record into the entry kinby would
write (ADR 0127)."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

import aiohttp
from pydantic import (
    BaseModel,  # noqa: TID251 - the registry's answer boundary
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
)
from pydantic.alias_generators import to_camel

from kinby.contracts import (
    McpRegistrySearchResult,
    McpRegistryServer,
    McpRegistryVariable,
    McpServerEntry,
)
from kinby.core.errors import McpRegistryUnreachable

__all__ = ["REGISTRY_URL", "search"]

REGISTRY_URL = "https://registry.modelcontextprotocol.io"

_SEARCH_TIMEOUT_SECONDS = 30
_OFFICIAL = "io.modelcontextprotocol.registry/official"
_LAUNCHERS = {"npm": "npx", "pypi": "uvx"}
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
_ENTRY: TypeAdapter[McpServerEntry] = TypeAdapter(McpServerEntry)


class _Record(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)


class _Input(_Record):
    """A value the record asks for: a url variable, a header or an environment variable."""

    name: str = ""
    description: str | None = None
    #: A template whose ``{variables}`` the user fills. Without one, the user gives the value.
    value: str | None = None
    default: str | None = None
    is_required: bool = False
    is_secret: bool = False
    variables: dict[str, _Input] = Field(default_factory=dict)


class _Remote(_Record):
    type: str
    url: str
    headers: list[_Input] = Field(default_factory=list)
    variables: dict[str, _Input] = Field(default_factory=dict)


class _Transport(_Record):
    type: str


class _Package(_Record):
    registry_type: str
    identifier: str
    version: str | None = None
    transport: _Transport
    environment_variables: list[_Input] = Field(default_factory=list)


class _Server(_Record):
    name: str
    description: str = ""
    version: str
    remotes: list[_Remote] = Field(default_factory=list)
    packages: list[_Package] = Field(default_factory=list)


class _Official(_Record):
    is_latest: bool


class _Listed(BaseModel):
    server: _Server
    meta: dict[str, _Official] = Field(alias="_meta", default_factory=dict)


class _Page(BaseModel):
    servers: list[_Listed]


async def search(registry_url: str, words: str) -> McpRegistrySearchResult:
    """The latest records the registry at *registry_url* finds for *words*, the only thing sent."""
    try:
        async with (
            aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=_SEARCH_TIMEOUT_SECONDS)
            ) as session,
            session.get(f"{registry_url}/v0.1/servers", params={"search": words}) as answer,
        ):
            if answer.status != 200:
                raise McpRegistryUnreachable(
                    f"The MCP Registry at {registry_url} answered {answer.status}."
                )
            page = _Page.model_validate(await answer.json())
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise McpRegistryUnreachable(
            f"The MCP Registry at {registry_url} could not be reached: {exc}"
        ) from exc
    return McpRegistrySearchResult(
        servers=[
            _converted(listed.server)
            for listed in page.servers
            if (official := listed.meta.get(_OFFICIAL)) is None or official.is_latest
        ]
    )


def _converted(server: _Server) -> McpRegistryServer:
    variables = _Variables()
    remote = next((remote for remote in server.remotes if remote.type == "streamable-http"), None)
    package = next(
        (
            package
            for package in server.packages
            if package.registry_type in _LAUNCHERS and package.transport.type == "stdio"
        ),
        None,
    )
    entry: McpServerEntry | None = None
    reason: str | None = None
    try:
        if remote is not None:
            entry = _ENTRY.validate_python(_remote_entry(remote, variables))
        elif package is not None:
            entry = _ENTRY.validate_python(_package_entry(package, variables))
        else:
            reason = _unrunnable(server)
    except ValidationError as exc:
        reason = "; ".join(error["msg"] for error in exc.errors())
    return McpRegistryServer(
        name=server.name,
        description=server.description,
        version=server.version,
        entry=entry,
        reason=reason,
        variables=list(variables.listed.values()) if entry is not None else [],
    )


def _remote_entry(remote: _Remote, variables: _Variables) -> dict[str, object]:
    return {
        "type": "http",
        "url": variables.template(remote.url, remote.variables, _Input()),
        "headers": {header.name: variables.value(header) for header in remote.headers},
    }


def _package_entry(package: _Package, variables: _Variables) -> dict[str, object]:
    pinned = f"{package.identifier}@{package.version}" if package.version else package.identifier
    return {
        "type": "stdio",
        "command": _LAUNCHERS[package.registry_type],
        "args": [pinned],
        "env": {item.name: variables.value(item) for item in package.environment_variables},
    }


def _unrunnable(server: _Server) -> str:
    offers = [f"a remote over {remote.type}" for remote in server.remotes] + [
        f"a package from {package.registry_type} over {package.transport.type}"
        for package in server.packages
    ]
    return (
        "kinby runs a remote over streamable-http, or a package from npm or pypi over stdio. "
        f"This record has {' and '.join(offers) or 'neither'}."
    )


@dataclass
class _Variables:
    """The ``${VAR}`` values a conversion writes, keeping those the record marks required or
    secret."""

    listed: dict[str, McpRegistryVariable] = field(default_factory=dict)

    def value(self, item: _Input) -> str:
        """A header's or environment variable's value: its template filled, or a variable named
        after it."""
        if item.value is None:
            return self.reference(item.name, item)
        return self.template(item.value, item.variables, item)

    def template(self, template: str, inputs: Mapping[str, _Input], owner: _Input) -> str:
        """*template* with each ``{name}`` a variable, described by *inputs* or else *owner*, and
        required or secret when either says so."""

        def described(name: str) -> _Input:
            variable = inputs.get(name)
            if variable is None:
                return owner
            return variable.model_copy(
                update={
                    "is_required": variable.is_required or owner.is_required,
                    "is_secret": variable.is_secret or owner.is_secret,
                }
            )

        return _PLACEHOLDER.sub(
            lambda match: self.reference(match[1], described(match[1])), template
        )

    def reference(self, name: str, described: _Input) -> str:
        variable = re.sub(r"[^A-Z0-9_]", "_", name.upper())
        if described.is_required or described.is_secret:
            self.listed.setdefault(
                variable,
                McpRegistryVariable(
                    name=variable, secret=described.is_secret, description=described.description
                ),
            )
        if described.default:
            return f"${{{variable}:-{described.default}}}"
        return f"${{{variable}}}"
