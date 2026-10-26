"""Load and run the authored tools an instance exposes from its instance code (ADR 0109).

Each one is a function decorated with `tool` in its own module of the instance code. kinby never
imports that module itself: a child process on the venv's Python describes the tool at turn start
and runs each call, so the tool sees the packages the instance code added.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from functools import lru_cache, partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, cast

from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import JsonValue, TypeAdapter

from kinby.instance.code import authored_tool_module, code_python, script_environment
from kinby.instance.layout import CODE_DIR
from kinby.plugins.defaults.shell import run_capped
from kinby.plugins.errors import exception_message
from kinby.plugins.tools import Tool, ToolContext

#: How long describing one authored tool, which imports its module, may take at turn start.
LOAD_SECONDS = 60
_CONTEXT = "_kinby_context"


@dataclass(frozen=True)
class _Described:
    description: str
    parameters: dict[str, JsonValue]
    write: bool


_DESCRIBED = TypeAdapter(_Described)
_RESULT = TypeAdapter(str | None)


def load_authored_tool(instance_path: Path, name: str, *, timeout_seconds: int) -> Tool:
    """The authored tool *name*, described by its module in the instance code's venv.

    Raises ValueError, naming what is wrong, when the module does not import or defines no
    tool *name* of the shape the registry expects.
    """
    module = instance_path / authored_tool_module(name)
    described = _describe(instance_path, name, module.stat().st_mtime_ns, timeout_seconds)
    return Tool(
        name=name,
        write=described.write,
        source=module.resolve(),
        runnable=StructuredTool(
            name=name,
            description=described.description,
            args_schema=described.parameters,
            coroutine=partial(_call, instance_path, name),
        ),
        context_parameter=_CONTEXT,
        authored=True,
    )


@lru_cache(maxsize=64)
def _describe(instance_path: Path, name: str, modified_ns: int, timeout_seconds: int) -> _Described:
    """Describe the tool, once per version of its module."""
    if not code_python(instance_path).exists():
        raise ValueError(f"The instance code has no venv yet, so {name} cannot load.")
    with TemporaryDirectory(prefix="kinby-authored-") as exchange:
        exchanged = Path(exchange) / "described.json"
        _run_child("describe", instance_path, name, exchanged, timeout_seconds)
        return _DESCRIBED.validate_json(exchanged.read_bytes())


async def _call(instance_path: Path, name: str, /, **arguments: object) -> str | None:
    context = cast(ToolContext, arguments.pop(_CONTEXT))
    timeout_seconds = context.instance.manifest.tools.bash_timeout_seconds
    with TemporaryDirectory(prefix="kinby-authored-") as exchange:
        exchanged = Path(exchange) / "exchange.json"
        exchanged.write_text(json.dumps(arguments), encoding="utf-8")
        await asyncio.to_thread(_run_child, "call", instance_path, name, exchanged, timeout_seconds)
        return _RESULT.validate_json(exchanged.read_bytes())


def _run_child(
    command: Literal["describe", "call"],
    instance_path: Path,
    name: str,
    exchanged: Path,
    timeout_seconds: int,
) -> None:
    # The child imports kinby from where this process does, ahead of the venv's own packages.
    env = {
        **script_environment(instance_path),
        "PYTHONPATH": os.pathsep.join(entry for entry in sys.path if entry),
    }
    returncode, output = run_capped(
        (
            str(code_python(instance_path)),
            "-m",
            __name__,
            command,
            str(instance_path / authored_tool_module(name)),
            name,
            str(exchanged),
        ),
        cwd=instance_path / CODE_DIR,
        timeout_seconds=timeout_seconds,
        label=name,
        env=env,
    )
    if returncode:
        raise ValueError(output)


def _child(command: str, module: Path, name: str, exchanged: Path) -> None:
    """Describe the tool *name* of *module* into *exchanged*, or call it with the arguments
    *exchanged* holds and leave its result there."""
    from kinby.plugins.registry import load_tool_file

    tool = next((each for each in load_tool_file(module) if each.name == name), None)
    if tool is None:
        raise ValueError(
            f'{module.name} defines no tool "{name}". Decorate a function named {name} with '
            "@tool(write=...) from kinby.plugins."
        )
    if tool.context_parameter is not None:
        raise ValueError(f'Tool "{name}" runs in its own process and cannot take a ToolContext.')
    if command == "describe":
        described = _Described(
            description=tool.runnable.description,
            parameters=convert_to_openai_tool(tool.runnable)["function"]["parameters"],
            write=tool.write,
        )
        exchanged.write_bytes(_DESCRIBED.dump_json(described))
        return
    arguments = json.loads(exchanged.read_text(encoding="utf-8"))
    result = asyncio.run(tool.runnable.ainvoke(arguments))
    exchanged.write_bytes(_RESULT.dump_json(None if result is None else str(result)))


if __name__ == "__main__":
    command, module, name, exchanged = sys.argv[1:]
    try:
        _child(command, Path(module), name, Path(exchanged))
    except Exception as exc:
        sys.exit(exception_message(exc))
