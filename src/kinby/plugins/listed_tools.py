"""The tools an instance's turns listed, kept in its state dir for the hub's toolset (ADR 0129).
A record that does not parse holds none, so it never stops the hub reading the instance."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import TypeAdapter

from kinby.instance.layout import AUTHORED_TOOLS_NAME, MCP_TOOLS_NAME
from kinby.plugins.tools import Tool


@dataclass(frozen=True)
class ListedTool:
    """A tool as a turn listed it, with about how many *tokens* its definition adds to every
    model call that is sent it."""

    name: str
    description: str
    tokens: int


_MCP = TypeAdapter(dict[str, tuple[ListedTool, ...]])
_AUTHORED = TypeAdapter(tuple[ListedTool, ...])


def listed(name: str, tool: Tool) -> ListedTool:
    """*tool* listed as *name*, a token for each four characters of its definition as the model is
    sent it."""
    definition = json.dumps(convert_to_openai_tool(tool.runnable))
    return ListedTool(name, tool.runnable.description, math.ceil(len(definition) / 4))


def mcp_tools_listed(state_dir: Path) -> dict[str, tuple[ListedTool, ...]]:
    """The tools each MCP server listed at the last turn connected to it, by the server's name."""
    try:
        return _MCP.validate_json((state_dir / MCP_TOOLS_NAME).read_bytes())
    except FileNotFoundError, ValueError:
        return {}


def record_mcp_tools(state_dir: Path, servers: Mapping[str, Iterable[ListedTool]]) -> None:
    path = state_dir / MCP_TOOLS_NAME
    if not servers:
        path.unlink(missing_ok=True)
        return
    _write(path, _MCP.dump_json({name: _by_name(tools) for name, tools in servers.items()}))


def authored_tools_listed(state_dir: Path) -> tuple[ListedTool, ...] | None:
    """The authored tools the last turn loaded, or None until a turn has."""
    try:
        return _AUTHORED.validate_json((state_dir / AUTHORED_TOOLS_NAME).read_bytes())
    except FileNotFoundError, ValueError:
        return None


def record_authored_tools(state_dir: Path, tools: Iterable[Tool]) -> None:
    listed_tools = _by_name(listed(tool.name, tool) for tool in tools)
    _write(state_dir / AUTHORED_TOOLS_NAME, _AUTHORED.dump_json(listed_tools))


def _by_name(tools: Iterable[ListedTool]) -> tuple[ListedTool, ...]:
    return tuple(sorted(tools, key=lambda tool: tool.name))


def _write(path: Path, content: bytes) -> None:
    # Turns of two threads may record at once, so each stages its own file.
    staging = path.with_name(f".{path.name}.{uuid4().hex}.staging")
    staging.write_bytes(content)
    staging.replace(path)
