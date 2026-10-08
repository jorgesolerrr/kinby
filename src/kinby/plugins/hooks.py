"""Declare hooks: instance code kinby runs when a factory step ends, never offered to the model."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import FunctionType

from kinby.contracts import StepEnding, StepValue, ValueName, Warning
from kinby.instance import Instance
from kinby.instance.layout import HOOKS_DIR
from kinby.plugins.errors import exception_message
from kinby.plugins.registry import load_plugin_module


@dataclass(frozen=True)
class StepEnd:
    """The step a hook records the result of, as it ended.

    It carries no summary: a step result's values come from the world, never from what the agent
    said.
    """

    instance: Instance
    ending: StepEnding
    work_item: Mapping[ValueName, StepValue]
    #: Every value the run's earlier steps recorded, by name.
    results: Mapping[ValueName, StepValue]

    @property
    def workspace(self) -> Path:
        return self.instance.manifest.workspace.path


@dataclass(frozen=True)
class HookResult:
    """What a hook records: the step result's values, and the declared outcome it names."""

    values: Mapping[ValueName, StepValue] = field(default_factory=dict)
    #: None takes the next step.
    outcome: str | None = None


@dataclass(frozen=True)
class Hook:
    """A declared hook. A step names it by its function's name."""

    name: str
    source: Path
    function: FunctionType

    async def record(self, end: StepEnd) -> HookResult | None:
        """Run the hook. One that blocks runs in a thread, off the instance's event loop."""
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
    """kinby's default hooks and those in *directory*'s hooks folder, by name.

    A hook of the instance replaces the default of its name. Each file that does not load is a
    warning.
    """
    # The default hooks are declared with this module's decorator, so they load on first use.
    from kinby.plugins.defaults.hooks import HOOKS

    hooks = {default.name: default for default in HOOKS}
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
