"""Connect a turn to the MCP servers its instance picked, and offer their tools in the states the
turn started with (ADR 0111, ADR 0129)."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import dotenv_values
from pydantic import TypeAdapter

from kinby.contracts import McpHttpEntry, McpServerEntry, McpServerName, ToolState
from kinby.instance import Instance, ToolStates
from kinby.instance.layout import ENV_NAME, MCP_FAILURES_NAME, SKILLS_LOCK_NAME
from kinby.instance.skills_lock import read_lock
from kinby.plugins.listed_tools import listed, mcp_tools_listed, record_mcp_tools

if TYPE_CHECKING:
    from kinby.plugins.tools import Tool

_FAILURES = TypeAdapter(dict[str, str])
_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


@dataclass(frozen=True)
class McpTools:
    """The tools of a turn's MCP servers: those the model is sent, and the turn's search set."""

    loaded: tuple[Tool, ...] = ()
    searchable: tuple[Tool, ...] = ()


def mcp_tool_states(instance: Instance) -> dict[McpServerName, ToolStates]:
    """The states of the tools of each MCP server the instance picked, by the server's name."""
    return {name: pick.states() for name, pick in read_lock(instance.path).mcp_servers.items()}


@asynccontextmanager
async def mcp_tools(
    instance: Instance, started: Mapping[McpServerName, ToolStates]
) -> AsyncIterator[McpTools]:
    """The loaded and searchable tools of each MCP server the instance picked, connected while the
    body runs, in the states the turn *started* with. A server the turn started without takes
    the states the instance holds now.

    The tools each server lists are recorded for the hub's toolset. A server that needs a sign-in
    or a variable the instance's secrets lack, or that does not start or list its tools, is left
    out, keeps what it listed at the last turn connected to it, and is recorded for the hub to
    report as an instance notice until a turn connects to it again.
    """
    state_dir = instance.manifest.state_dir
    picks = read_lock(instance.path).mcp_servers
    if not picks:
        _record_failures(state_dir, {})
        record_mcp_tools(state_dir, {})
        yield McpTools()
        return
    # The MCP SDK loads only in an instance that picked a server.
    from kinby.plugins.mcp_connection import NotConnected, SignInNeeded, connected_tools

    secrets = {
        name: value
        for name, value in dotenv_values(instance.path / ENV_NAME, interpolate=False).items()
        if value is not None
    }
    source = (instance.path / SKILLS_LOCK_NAME).resolve()
    tools: dict[ToolState, list[Tool]] = {state: [] for state in ToolState}
    before = mcp_tools_listed(state_dir)
    listed_tools = {name: before[name] for name in picks if name in before}
    failures: dict[str, str] = {}
    raised: Exception | None = None
    async with AsyncExitStack() as stack:
        for name, pick in sorted(picks.items()):
            if missing := sorted(
                variable
                for variable in needed_variables(pick.server.entry)
                if not secrets.get(variable)
            ):
                failures[name] = (
                    f'MCP server "{name}" needs {", ".join(missing)}, which the instance\'s '
                    "secrets lack, so the turn ran without it."
                )
                continue
            states = started.get(name) or pick.states()
            try:
                connected = await connected_tools(name, pick, secrets, source, stack)
            except SignInNeeded:
                failures[name] = f'MCP server "{name}" needs a sign-in, so the turn ran without it.'
                continue
            except NotConnected as failed:
                failures[name] = (
                    f'MCP server "{name}" did not start or list its tools, so the turn ran '
                    f"without them: {failed}"
                )
                continue
            for tool, adapted in connected:
                tools[states.of(tool)].append(adapted)
            listed_tools[name] = tuple(listed(tool, adapted) for tool, adapted in connected)
        _record_failures(state_dir, failures)
        record_mcp_tools(state_dir, listed_tools)
        try:
            yield McpTools(tuple(tools[ToolState.LOADED]), tuple(tools[ToolState.SEARCHABLE]))
        except Exception as exc:
            # Closing the servers' clients with it would wrap it in their task group's exception.
            raised = exc
    if raised is not None:
        raise raised


def needed_variables(entry: McpServerEntry) -> set[str]:
    """The variables *entry*'s values name with no default, which the instance's secrets must
    hold before the server starts."""
    if isinstance(entry, McpHttpEntry):
        values = [entry.url, *entry.headers.values()]
    else:
        values = [*entry.args, *entry.env.values()]
    return {match[1] for value in values for match in _VARIABLE.finditer(value) if match[2] is None}


def filled(value: str, secrets: Mapping[str, str]) -> str:
    """*value* with each ``${VAR}`` the secret's, or ``${VAR:-default}``'s default when the
    instance holds none."""
    return _VARIABLE.sub(lambda match: secrets.get(match[1]) or match[2] or "", value)


def failed_mcp_servers(state_dir: Path) -> dict[str, str]:
    """Why each MCP server that failed at the instance's last turn failed, by its name. A record
    that does not parse holds none, so it never stops the hub listing the instance."""
    try:
        return _FAILURES.validate_json((state_dir / MCP_FAILURES_NAME).read_bytes())
    except FileNotFoundError, ValueError:
        return {}


def _record_failures(state_dir: Path, failures: dict[str, str]) -> None:
    path = state_dir / MCP_FAILURES_NAME
    if not failures:
        path.unlink(missing_ok=True)
        return
    staging = path.with_name(f".{path.name}.staging")
    staging.write_bytes(_FAILURES.dump_json(failures))
    staging.replace(path)
