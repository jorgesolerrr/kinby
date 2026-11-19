"""Code mode's tool: run a script that calls the turn's tools (ADR 0129)."""

from kinby.plugins.tools import ToolContext, tool


@tool(write=False)
async def run_code(code: str, context: ToolContext) -> str:
    """Run Python that awaits your tools by name with keyword arguments, and get back only what
    it prints and returns. It has about 30 standard modules, and no files, network,
    `async with` or `async for`. Tools whose calls always need an approval are left out of it:
    call those directly."""
    # Monty loads only in a turn that runs a script.
    from kinby.plugins.code_mode import run_script

    return await run_script(code, context.broker)
