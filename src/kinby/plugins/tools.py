"""Declare native Python tools loaded by an instance."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, cast, get_args, get_origin, get_type_hints
from uuid import UUID

from langchain_core.tools import InjectedToolArg, StructuredTool
from pydantic import JsonValue

from kinby.instance import Instance

ToolFunction = Callable[..., str]


@dataclass(frozen=True)
class ToolContext:
    """Instance-owned values supplied by kinby when a tool runs."""

    instance: Instance
    thread_id: UUID

    @property
    def workspace(self) -> Path:
        return self.instance.manifest.workspace.path


@dataclass(frozen=True)
class Tool:
    """A declared tool and the metadata kinby needs to run it."""

    name: str
    write: bool
    source: Path
    runnable: StructuredTool
    context_parameter: str | None = field(default=None, repr=False)

    async def ainvoke(
        self,
        arguments: Mapping[str, JsonValue],
        context: ToolContext,
    ) -> str:
        invocation: dict[str, object] = dict(arguments)
        if self.context_parameter is not None:
            invocation[self.context_parameter] = context
        return str(await self.runnable.ainvoke(invocation))


def tool(*, write: bool) -> Callable[[ToolFunction], Tool]:
    """Build a structured tool from a function signature and docstring."""

    def decorate(function: ToolFunction) -> Tool:
        context_parameter = _mark_context_parameter(function)
        runnable = StructuredTool.from_function(func=function)
        source_file = inspect.getsourcefile(function)
        if source_file is None:
            raise ValueError(f'Tool "{_function_name(function)}" has no source file.')
        return Tool(
            name=runnable.name,
            write=write,
            source=Path(source_file).resolve(),
            runnable=runnable,
            context_parameter=context_parameter,
        )

    return decorate


def _mark_context_parameter(function: ToolFunction) -> str | None:
    annotations = get_type_hints(function, include_extras=True)
    parameters = [
        name
        for name, annotation in annotations.items()
        if name != "return" and _is_tool_context(annotation)
    ]
    if len(parameters) > 1:
        raise ValueError(
            f'Tool "{_function_name(function)}" has more than one ToolContext parameter.'
        )
    if not parameters:
        return None
    name = parameters[0]
    function.__annotations__[name] = Annotated[ToolContext, InjectedToolArg]
    return name


def _is_tool_context(annotation: object) -> bool:
    if annotation is ToolContext:
        return True
    return get_origin(annotation) is Annotated and get_args(annotation)[0] is ToolContext


def _function_name(function: ToolFunction) -> str:
    return cast(str, getattr(function, "__name__", repr(function)))
