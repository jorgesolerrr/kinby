"""Refresh the immutable tool set used by one model turn."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from types import ModuleType
from uuid import uuid4

from kinby.contracts import Warning
from kinby.instance.layout import TOOLS_DIR
from kinby.plugins.errors import exception_message
from kinby.plugins.tools import Tool

FileSignature = tuple[tuple[Path, int], ...]


@dataclass(frozen=True)
class ToolSnapshot:
    tools: tuple[Tool, ...] = ()
    _by_name: dict[str, Tool] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        tools = tuple(sorted(self.tools, key=lambda tool: tool.name))
        object.__setattr__(self, "tools", tools)
        object.__setattr__(self, "_by_name", {tool.name: tool for tool in tools})

    def get(self, name: str) -> Tool | None:
        return self._by_name.get(name)

    def with_core(self, core: Tool) -> tuple[ToolSnapshot, tuple[Warning, ...]]:
        """Add a core tool, replacing and warning about a namesake plugin."""
        plugin = self.get(core.name)
        warnings: tuple[Warning, ...] = ()
        if plugin is not None:
            warnings = (
                Warning(
                    sources=(str(plugin.source), str(core.source)),
                    message=f'Plugin tool "{plugin.name}" was replaced by the core tool.',
                ),
            )
        tools = (*[tool for tool in self.tools if tool.name != core.name], core)
        return ToolSnapshot(tools), warnings


@dataclass(frozen=True)
class _FileTools:
    modified_ns: int
    tools: tuple[Tool, ...]


class ToolRegistry:
    def __init__(self, instance_path: Path, *, defaults: bool = True) -> None:
        self._tools_path = instance_path / TOOLS_DIR
        self._packaged, self._package_warnings = _load_entry_points(defaults=defaults)
        self._signature: FileSignature | None = None
        self._files: dict[Path, _FileTools] = {}
        self._snapshot = ToolSnapshot(tuple(sorted(self._packaged, key=lambda tool: tool.name)))

    def refresh(self) -> tuple[ToolSnapshot, tuple[Warning, ...]]:
        try:
            signature = _directory_signature(self._tools_path)
        except OSError as exc:
            return (
                self._snapshot,
                (
                    Warning(
                        sources=(str(self._tools_path),),
                        message=exception_message(exc),
                    ),
                    *self._package_warnings,
                ),
            )
        if signature == self._signature:
            return self._snapshot, self._package_warnings

        candidate: dict[Path, _FileTools] = {}
        warnings: list[Warning] = []
        for path, modified_ns in signature:
            cached = self._files.get(path)
            if cached is not None and cached.modified_ns == modified_ns:
                candidate[path] = cached
                continue
            try:
                candidate[path] = _FileTools(modified_ns, _load_file(path))
            except Exception as exc:
                # Tool files are user code. Report every broken file and keep the last valid set.
                warnings.append(Warning(sources=(str(path),), message=exception_message(exc)))
        self._files = candidate
        if warnings:
            return self._snapshot, (*warnings, *self._package_warnings)

        instance_tools = tuple(
            tool for file_tools in candidate.values() for tool in file_tools.tools
        )
        duplicate = _duplicate_warning(instance_tools)
        if duplicate is not None:
            return self._snapshot, (duplicate, *self._package_warnings)

        instance_names = {tool.name for tool in instance_tools}
        tools = instance_tools + tuple(
            tool for tool in self._packaged if tool.name not in instance_names
        )
        self._signature = signature
        self._snapshot = ToolSnapshot(tools)
        return self._snapshot, self._package_warnings


def _directory_signature(tools_path: Path) -> FileSignature:
    if not tools_path.is_dir():
        return ()
    return tuple(
        (path, path.stat().st_mtime_ns)
        for path in sorted(tools_path.glob("*.py"), key=lambda item: item.name)
    )


def _load_file(path: Path) -> tuple[Tool, ...]:
    module_name = f"_kinby_tool_{uuid4().hex}"
    module = ModuleType(module_name)
    module.__file__ = str(path)
    # Decorators and annotation resolvers need the defining module during execution.
    sys.modules[module_name] = module
    try:
        code = compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
        exec(code, module.__dict__)
    finally:
        sys.modules.pop(module_name, None)
    return _module_tools(module)


def _module_tools(module: ModuleType) -> tuple[Tool, ...]:
    return tuple(value for value in vars(module).values() if isinstance(value, Tool))


def _load_entry_points(*, defaults: bool) -> tuple[tuple[Tool, ...], tuple[Warning, ...]]:
    tools: list[Tool] = []
    warnings: list[Warning] = []
    for entry_point in entry_points(group="kinby.tools"):
        if not defaults and entry_point.name == "defaults":
            distribution = entry_point.dist
            if distribution is not None and distribution.name == "kinby":
                continue
        try:
            tools.extend(_entry_point_tools(entry_point))
        except Exception as exc:
            warnings.append(Warning(sources=(entry_point.value,), message=exception_message(exc)))
    packaged, duplicates = _deduplicate_packaged_tools(tuple(tools))
    return packaged, (*warnings, *duplicates)


def _entry_point_tools(entry_point: EntryPoint) -> tuple[Tool, ...]:
    loaded = entry_point.load()
    if not isinstance(loaded, Sequence):
        raise TypeError(f'Entry point "{entry_point.value}" does not export tools.')
    tools = tuple(item for item in loaded if isinstance(item, Tool))
    if len(tools) != len(loaded):
        raise TypeError(f'Entry point "{entry_point.value}" does not export tools.')
    return tools


def _duplicate_warning(tools: tuple[Tool, ...]) -> Warning | None:
    found: dict[str, Tool] = {}
    for current in tools:
        previous = found.get(current.name)
        if previous is not None:
            return _duplicate_warning_for(previous, current)
        found[current.name] = current
    return None


def _deduplicate_packaged_tools(
    tools: tuple[Tool, ...],
) -> tuple[tuple[Tool, ...], tuple[Warning, ...]]:
    found: dict[str, Tool] = {}
    duplicate_names: set[str] = set()
    warnings: list[Warning] = []
    for current in tools:
        previous = found.get(current.name)
        if previous is None:
            found[current.name] = current
            continue
        duplicate_names.add(current.name)
        warnings.append(_duplicate_warning_for(previous, current))
    unique = tuple(tool for name, tool in found.items() if name not in duplicate_names)
    return unique, tuple(warnings)


def _duplicate_warning_for(first: Tool, second: Tool) -> Warning:
    return Warning(
        sources=(str(first.source), str(second.source)),
        message=f'Tool "{second.name}" is exported by both sources.',
    )
