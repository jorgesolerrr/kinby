"""Example instance-local tool for the coding-agent instance."""

from kinby.plugins import ToolContext, tool


@tool(write=False)
def workspace_name(context: ToolContext) -> str:
    """Return the workspace directory name."""
    return context.workspace.name
