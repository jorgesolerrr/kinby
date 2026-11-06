"""Connect a turn to the MCP servers its instance picked, and offer their tools (ADR 0111)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import dotenv_values
from pydantic import TypeAdapter

from kinby.instance import Instance
from kinby.instance.layout import ENV_NAME, MCP_FAILURES_NAME, SKILLS_LOCK_NAME
from kinby.instance.skills_lock import read_lock

if TYPE_CHECKING:
    from kinby.plugins.tools import Tool

_FAILURES = TypeAdapter(dict[str, str])


@asynccontextmanager
async def mcp_tools(instance: Instance) -> AsyncIterator[tuple[Tool, ...]]:
    """The included tools of each MCP server the instance picked, connected while the body runs.

    A server that does not start or list its tools is left out, and recorded for the hub to
    report as an instance notice until a turn connects to it again.
    """
    picks = read_lock(instance.path).mcp_servers
    if not picks:
        _record_failures(instance.manifest.state_dir, {})
        yield ()
        return
    # The MCP SDK loads only in an instance that picked a server.
    from kinby.plugins.mcp_connection import NotConnected, connected_tools

    env = {
        name: value
        for name, value in dotenv_values(instance.path / ENV_NAME, interpolate=False).items()
        if value is not None
    }
    source = (instance.path / SKILLS_LOCK_NAME).resolve()
    tools: list[Tool] = []
    failures: dict[str, str] = {}
    async with AsyncExitStack() as stack:
        for name, pick in sorted(picks.items()):
            try:
                tools.extend(await connected_tools(name, pick, env, source, stack))
            except NotConnected as failed:
                failures[name] = (
                    f'MCP server "{name}" did not start or list its tools, so the turn ran '
                    f"without them: {failed}"
                )
        _record_failures(instance.manifest.state_dir, failures)
        yield tuple(tools)


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
