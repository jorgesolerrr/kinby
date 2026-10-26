"""A small MCP server the MCP server tests start over stdio."""

import json
import os

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer("echo")


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


if __name__ == "__main__":
    server.run()
