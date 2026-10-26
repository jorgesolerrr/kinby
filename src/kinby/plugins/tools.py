"""Declare native Python tools loaded by an instance."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, cast, get_args, get_origin, get_type_hints
from uuid import UUID

from langchain_core.tools import InjectedToolArg, StructuredTool
from pydantic import JsonValue

from kinby.contracts import (
    DelegatedRun,
    Event,
    NodeId,
    NotificationAccepted,
    NotificationRaised,
    NotificationRefused,
    RoutineName,
    ViewChanged,
)
from kinby.instance import Instance
from kinby.plugins.browser import BrowserService

ToolFunction = Callable[..., object]
RunReporter = Callable[[DelegatedRun], Awaitable[object]]
RecallReporter = Callable[[tuple[NodeId, ...]], Awaitable[object]]
NotificationReporter = Callable[
    [NotificationRaised | NotificationAccepted | NotificationRefused], Awaitable[Event]
]
ViewReporter = Callable[[ViewChanged], Awaitable[object]]
#: What an approval of a call shows beside its arguments, from the call's arguments.
Preview = Callable[[Mapping[str, JsonValue], Instance], str | None]


@dataclass(frozen=True)
class ToolContext:
    """Instance-owned values supplied by kinby when a tool runs."""

    instance: Instance
    thread_id: UUID
    #: The turn the tool runs in, which an instance tool's config change names.
    turn_id: UUID | None = None
    #: The routine whose turn the tool runs in. None in a turn the user started.
    routine: RoutineName | None = None
    #: Whether the turn is a dreaming stage's, as it started, so its own writes cannot change it.
    dreaming_stage: bool = False
    #: The approval the user gave this call. None for a call the gate let through on its own.
    approval_id: UUID | None = None
    #: Where a notification raised in the turn came from: its routine, its factory step, or its
    #: thread's title.
    notification_source: str | None = None
    run_reporter: RunReporter | None = field(default=None, repr=False)
    recall_reporter: RecallReporter | None = field(default=None, repr=False)
    notification_reporter: NotificationReporter | None = field(default=None, repr=False)
    view_reporter: ViewReporter | None = field(default=None, repr=False)
    #: Where the browser tools open the turn's browser. None outside a turn.
    browser: BrowserService | None = field(default=None, repr=False)

    @property
    def workspace(self) -> Path:
        return self.instance.manifest.workspace.path

    async def report_run(self, run: DelegatedRun) -> None:
        """Record *run* in the turn that invoked the tool, as soon as the run finishes."""
        if self.run_reporter is None:
            # kinby.core imports this module, so its errors load on first use.
            from kinby.core.errors import NoActiveTurn

            raise NoActiveTurn(f'Thread "{self.thread_id}" has no open turn to report a run in.')
        await self.run_reporter(run)

    async def report_recall(self, nodes: tuple[NodeId, ...]) -> None:
        """Record that the turn that invoked the tool read *nodes* from memory. Outside a turn
        there is nothing to record."""
        if self.recall_reporter is not None:
            await self.recall_reporter(nodes)

    async def report_notification(
        self, payload: NotificationRaised | NotificationAccepted | NotificationRefused
    ) -> Event:
        """Record *payload* in the turn that invoked the tool, and return its event."""
        if self.notification_reporter is None:
            # kinby.core imports this module, so its errors load on first use.
            from kinby.core.errors import NoActiveTurn

            raise NoActiveTurn(f'Thread "{self.thread_id}" has no open turn to notify from.')
        return await self.notification_reporter(payload)

    async def report_view_changed(self, changed: ViewChanged) -> None:
        """Record that the turn that invoked the tool wrote a view. Outside a turn there is
        nothing to record."""
        if self.view_reporter is not None:
            await self.view_reporter(changed)


@dataclass(frozen=True)
class Tool:
    """A declared tool and the metadata kinby needs to run it."""

    name: str
    write: bool
    source: Path
    runnable: StructuredTool
    paths: tuple[str, ...] = ()
    #: A hub tool, which reaches past the instance into the hub (ADR 0096).
    hub: bool = False
    #: Consolidation's own write, which needs no approval (ADR 0100).
    consolidation: bool = False
    #: The notify tool, which runs unasked unless a rule names it (ADR 0103).
    notify: bool = False
    #: A view write, which runs unasked unless a rule names it (ADR 0108).
    view: bool = False
    #: An authored tool, a write whatever it declares until the user marks it read-only
    #: (ADR 0109).
    authored: bool = False
    preview: Preview | None = field(default=None, repr=False)
    context_parameter: str | None = field(default=None, repr=False)

    async def ainvoke(
        self,
        arguments: Mapping[str, JsonValue],
        context: ToolContext,
    ) -> str:
        return str(await self.ainvoke_raw(arguments, context))

    async def ainvoke_raw(
        self,
        arguments: Mapping[str, JsonValue],
        context: ToolContext,
    ) -> str | None:
        """Preserve a code step's None result before rendering tool output."""
        result = await self.ainvoke_value(arguments, context)
        return None if result is None else str(result)

    async def ainvoke_value(
        self,
        arguments: Mapping[str, JsonValue],
        context: ToolContext,
    ) -> object:
        """What the tool returns, as it returns it, for a factory code step to read."""
        invocation = self.resolve_paths(arguments, context.workspace)
        if self.context_parameter is not None:
            invocation[self.context_parameter] = context
        return await self.runnable.ainvoke(invocation)

    def resolve_paths(
        self,
        arguments: Mapping[str, JsonValue],
        workspace: Path,
    ) -> dict[str, object]:
        """Resolve declared path arguments against the workspace."""
        resolved: dict[str, object] = dict(arguments)
        for parameter in self.paths:
            path = arguments.get(parameter)
            if isinstance(path, str):
                resolved[parameter] = str((workspace / path).resolve())
        return resolved


def tool(
    *,
    write: bool,
    paths: tuple[str, ...] = (),
    hub: bool = False,
    notify: bool = False,
    view: bool = False,
) -> Callable[[ToolFunction], Tool]:
    """Build a structured tool from a function signature and docstring."""

    def decorate(function: ToolFunction) -> Tool:
        context_parameter = _mark_context_parameter(function)
        if inspect.iscoroutinefunction(function):
            runnable = StructuredTool.from_function(coroutine=function)
        else:
            runnable = StructuredTool.from_function(func=function)
        unknown_path = next(
            (parameter for parameter in paths if parameter not in runnable.args),
            None,
        )
        if unknown_path is not None:
            raise ValueError(
                f'Tool "{runnable.name}" declares unknown path parameter "{unknown_path}".'
            )
        source_file = inspect.getsourcefile(function)
        if source_file is None:
            raise ValueError(f'Tool "{_function_name(function)}" has no source file.')
        return Tool(
            name=runnable.name,
            write=write,
            source=Path(source_file).resolve(),
            runnable=runnable,
            paths=paths,
            hub=hub,
            notify=notify,
            view=view,
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
    # LangChain excludes InjectedToolArg parameters from the model-facing schema.
    function.__annotations__[name] = Annotated[ToolContext, InjectedToolArg]
    return name


def _is_tool_context(annotation: object) -> bool:
    if annotation is ToolContext:
        return True
    return get_origin(annotation) is Annotated and get_args(annotation)[0] is ToolContext


def _function_name(function: ToolFunction) -> str:
    return cast(str, getattr(function, "__name__", repr(function)))
