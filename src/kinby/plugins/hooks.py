"""Declare hooks: instance code kinby runs when a factory step ends, never offered to the model."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from types import FunctionType

from kinby.contracts import Warning
from kinby.instance.layout import HOOKS_DIR
from kinby.plugins.errors import exception_message
from kinby.plugins.registry import load_plugin_module


@dataclass(frozen=True)
class Hook:
    """A declared hook. A step names it by its function's name."""

    name: str
    source: Path
    function: FunctionType


def hook(function: FunctionType) -> Hook:
    """Declare *function* as a hook."""
    source_file = inspect.getsourcefile(function)
    if source_file is None:
        raise ValueError(f'Hook "{function.__name__}" has no source file.')
    return Hook(name=function.__name__, source=Path(source_file).resolve(), function=function)


def load_hooks(directory: Path) -> tuple[tuple[Hook, ...], tuple[Warning, ...]]:
    """The hooks in *directory*'s hooks folder, and a warning for each file that does not load."""
    hooks: list[Hook] = []
    warnings: list[Warning] = []
    for path in sorted((directory / HOOKS_DIR).glob("*.py")):
        try:
            module = load_plugin_module(path)
        except Exception as exc:
            # Hook files are user code. Report every broken file.
            warnings.append(Warning(sources=(str(path),), message=exception_message(exc)))
            continue
        hooks.extend(value for value in vars(module).values() if isinstance(value, Hook))
    return tuple(hooks), tuple(warnings)
