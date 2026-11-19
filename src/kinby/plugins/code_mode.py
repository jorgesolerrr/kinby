"""Code mode: a script the model writes runs in Monty, a fresh interpreter for each script, and
reaches nothing but the turn's tools, each an async function whose calls the broker makes
(ADR 0129). This module and Monty load on a turn's first script."""

import asyncio
import json
from collections.abc import Awaitable, Callable

from pydantic import JsonValue, TypeAdapter
from pydantic_monty import (
    AsyncMonty,
    CollectString,
    MontyError,
    MontyRuntimeError,
    MontySyntaxError,
    ResourceLimits,
)

from kinby.plugins.tool_search import argument_names
from kinby.plugins.tools import Tool, ToolBroker

#: The running time a script stops at, which leaves out the time it waits on its tools.
SCRIPT_SECONDS = 30.0
_MEMORY_MIB = 256
#: Monty counts each tool call a script makes as a suspension, and each sleep too.
_TOOL_CALLS = 100

_ARGUMENTS = TypeAdapter(dict[str, JsonValue])


class ScriptFailed(Exception):
    """A script that did not finish: its error, and the tool calls it made before."""


async def run_script(code: str, broker: ToolBroker | None) -> str:
    """What the script *code* prints, followed by what it returns, its calls to the tools of
    *broker* made by *broker*. Without a broker the script calls no tool. Raises ScriptFailed
    when the script fails or passes a limit."""
    finished: list[str] = []
    in_flight: set[asyncio.Future[str]] = set()
    functions = (
        {}
        if broker is None
        else {
            name: _function(tool, broker, finished, in_flight)
            for name, tool in broker.tools.items()
        }
    )
    printed = CollectString()
    limits = ResourceLimits(
        max_feed_duration_secs=SCRIPT_SECONDS,
        max_memory=_MEMORY_MIB * 2**20,
        max_suspensions=_TOOL_CALLS,
    )
    try:
        async with (
            AsyncMonty(max_processes=1) as pool,
            pool.checkout(limits=limits) as session,
        ):
            returned = await session.feed_run(
                code, external_lookup=functions, print_callback=printed
            )
    except MontyError as error:
        raise ScriptFailed(_failure(error, finished)) from None
    except asyncio.CancelledError:
        # Monty runs each call in a task of its own, which outlives a cancelled script.
        for task in in_flight:
            task.cancel()
        await asyncio.gather(*in_flight, return_exceptions=True)
        raise
    parts = [printed.output.rstrip("\n")] if printed.output else []
    if returned is not None:
        parts.append(returned if isinstance(returned, str) else repr(returned))
    return "\n".join(parts) or "The script printed nothing and returned None."


def _failure(error: MontyError, finished: list[str]) -> str:
    """What *error* was, at the line it happened on when it has one, the limit it passed, and
    the tool calls that *finished* before."""
    passed = {
        "feed time limit exceeded": f"{SCRIPT_SECONDS:g} seconds of running time",
        "memory limit exceeded": f"{_MEMORY_MIB} MiB of memory",
        "suspension limit": f"{_TOOL_CALLS} tool calls",
    }
    limit = next((limit for words, limit in passed.items() if words in str(error)), None)
    return "\n".join(
        [
            *([f"The script passed its limit of {limit}."] if limit else []),
            error.display()
            if isinstance(error, MontySyntaxError | MontyRuntimeError)
            else str(error),
            f"The tool calls that finished: {', '.join(finished)}."
            if finished
            else "No tool call finished.",
        ]
    )


def _function(
    tool: Tool, broker: ToolBroker, finished: list[str], in_flight: set[asyncio.Future[str]]
) -> Callable[..., Awaitable[JsonValue]]:
    """*tool* as a script calls it: by keyword, giving back the parsed JSON when its result is
    JSON, and the text otherwise. Each call is *in_flight* while it runs, and *finished* names
    it once it returns."""
    names = argument_names(tool)

    async def call(**arguments: JsonValue) -> JsonValue:
        made = asyncio.ensure_future(
            broker.call(
                tool,
                _ARGUMENTS.validate_python(
                    {names.get(name, name): value for name, value in arguments.items()}
                ),
            )
        )
        in_flight.add(made)
        try:
            output = await made
        finally:
            in_flight.discard(made)
        finished.append(tool.name)
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return output

    return call
