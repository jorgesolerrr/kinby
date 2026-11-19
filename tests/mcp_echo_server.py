"""A small MCP server the MCP server tests start over stdio, or over HTTP on the port given after
``http``."""

import json
import os
import sys

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations

server = MCPServer("echo", description="Repeats and posts lines of text.")


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def look(text: str) -> str:
    """Repeat a line of text."""
    return f"looked: {text}"


@server.tool()
def post(text: str) -> str:
    """Post a line of text."""
    return f"posted: {text}"


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def environment() -> str:
    """The variables the server's process sees, as JSON."""
    return json.dumps(dict(os.environ))


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def arguments() -> str:
    """The arguments the server's process was started with, as JSON."""
    return json.dumps(sys.argv[1:])


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def headers(context: Context) -> str:
    """The headers of the request that called the tool, as JSON."""
    return json.dumps(dict(context.headers or {}))


if __name__ == "__main__":
    if sys.argv[1:2] == ["http"]:
        server.run("streamable-http", port=int(sys.argv[2]))
    else:
        server.run()
