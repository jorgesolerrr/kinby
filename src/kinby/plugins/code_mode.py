"""Code mode: a script the model writes runs in Monty, a fresh interpreter for each script, and
reaches nothing but the turn's tools, each an async function whose calls the broker makes
(ADR 0129). This module and Monty load on a turn's first script."""

import json
from collections.abc import Awaitable, Callable

from pydantic import JsonValue, TypeAdapter
from pydantic_monty import AsyncMonty, CollectString

from kinby.plugins.tool_search import argument_names
from kinby.plugins.tools import Tool, ToolBroker

_ARGUMENTS = TypeAdapter(dict[str, JsonValue])


async def run_script(code: str, broker: ToolBroker | None) -> str:
    """What the script *code* prints, followed by what it returns, its calls to the tools of
    *broker* made by *broker*. Without a broker the script calls no tool."""
    functions = (
        {}
        if broker is None
        else {name: _function(tool, broker) for name, tool in broker.tools.items()}
    )
    printed = CollectString()
    async with AsyncMonty(max_processes=1) as pool, pool.checkout() as session:
        returned = await session.feed_run(code, external_lookup=functions, print_callback=printed)
    parts = [printed.output.rstrip("\n")] if printed.output else []
    if returned is not None:
        parts.append(returned if isinstance(returned, str) else repr(returned))
    return "\n".join(parts) or "The script printed nothing and returned None."


def _function(tool: Tool, broker: ToolBroker) -> Callable[..., Awaitable[JsonValue]]:
    """*tool* as a script calls it: by keyword, giving back the parsed JSON when its result is
    JSON, and the text otherwise."""
    names = argument_names(tool)

    async def call(**arguments: JsonValue) -> JsonValue:
        output = await broker.call(
            tool,
            _ARGUMENTS.validate_python(
                {names.get(name, name): value for name, value in arguments.items()}
            ),
        )
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return output

    return call
