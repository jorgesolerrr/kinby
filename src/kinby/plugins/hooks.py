"""Declare hooks: code kinby runs when a factory step ends, never offered to the model. A
factory's hooks are its checks, which record its steps' results on a workbench."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import FunctionType

from kinby.contracts import StepEnding, StepValue, ValueName, Warning
from kinby.instance.layout import HOOKS_DIR
from kinby.plugins.errors import exception_message
from kinby.plugins.registry import load_plugin_module


@dataclass(frozen=True)
class StepEnd:
    """The step a hook records the result of, as it ended.

    It carries no summary: a step result's values come from the world, never from what the agent
    said.
    """

    #: Where the step ran: the run's folder on the workbench.
    workspace: Path
    ending: StepEnding
    work_item: Mapping[ValueName, StepValue]
    #: Every value the run's earlier steps recorded, by name.
    results: Mapping[ValueName, StepValue]


@dataclass(frozen=True)
class HookResult:
    """What a hook records: the step result's values, and the declared outcome it names.

    A code step's tool returns one to name an outcome too.
    """

    values: Mapping[ValueName, StepValue] = field(default_factory=dict)
    #: None takes the next step.
    outcome: str | None = None
    #: A code step's summary. A hook's step keeps the summary it ended with.
    summary: str = ""


@dataclass(frozen=True)
class Hook:
    """A declared hook. A step names it by its function's name."""

    name: str
    source: Path
    function: FunctionType

    async def record(self, end: StepEnd) -> HookResult | None:
        """Run the hook. One that blocks runs in a thread, off the event loop."""
        if inspect.iscoroutinefunction(self.function):
            recorded = await self.function(end)
        else:
            recorded = await asyncio.to_thread(self.function, end)
        if recorded is not None and not isinstance(recorded, HookResult):
            raise TypeError(f"It returned {type(recorded).__name__}, not a HookResult or None.")
        return recorded


def hook(function: FunctionType) -> Hook:
    """Declare *function* as a hook. It takes a StepEnd and returns a HookResult or None."""
    source_file = inspect.getsourcefile(function)
    if source_file is None:
        raise ValueError(f'Hook "{function.__name__}" has no source file.')
    return Hook(name=function.__name__, source=Path(source_file).resolve(), function=function)


def load_hooks(directory: Path) -> tuple[tuple[Hook, ...], tuple[Warning, ...]]:
    """The hooks in *directory*'s hooks folder, by name. Each file that does not load is a
    warning."""
    hooks: dict[str, Hook] = {}
    warnings: list[Warning] = []
    for path in sorted((directory / HOOKS_DIR).glob("*.py")):
        try:
            module = load_plugin_module(path)
        except Exception as exc:
            # Hook files are user code. Report every broken file.
            warnings.append(Warning(sources=(str(path),), message=exception_message(exc)))
            continue
        hooks.update(
            (value.name, value) for value in vars(module).values() if isinstance(value, Hook)
        )
    return tuple(hooks.values()), tuple(warnings)
