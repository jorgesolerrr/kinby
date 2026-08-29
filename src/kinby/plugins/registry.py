"""Refresh the immutable tool set used by one model turn."""

from __future__ import annotations

import sys
from dataclasses import dataclass
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

    def get(self, name: str) -> Tool | None:
        return next((tool for tool in self.tools if tool.name == name), None)


@dataclass(frozen=True)
class _FileTools:
    modified_ns: int
    tools: tuple[Tool, ...]


class ToolRegistry:
    def __init__(self, instance_path: Path) -> None:
        self._tools_path = instance_path / TOOLS_DIR
        self._signature: FileSignature | None = None
        self._files: dict[Path, _FileTools] = {}
        self._snapshot = ToolSnapshot()

    def refresh(self) -> tuple[ToolSnapshot, tuple[Warning, ...]]:
        try:
            signature = _directory_signature(self._tools_path)
        except OSError as exc:
            return (
                self._snapshot,
                (Warning(source=str(self._tools_path), message=exception_message(exc)),),
            )
        if signature == self._signature:
            return self._snapshot, ()

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
                warnings.append(Warning(source=str(path), message=exception_message(exc)))
        if warnings:
            self._files = candidate
            return self._snapshot, tuple(warnings)

        tools = tuple(tool for file_tools in candidate.values() for tool in file_tools.tools)
        duplicate = _duplicate_warning(tools)
        if duplicate is not None:
            self._files = candidate
            return self._snapshot, (duplicate,)

        self._files = candidate
        self._signature = signature
        self._snapshot = ToolSnapshot(tuple(sorted(tools, key=lambda tool: tool.name)))
        return self._snapshot, ()


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
    sys.modules[module_name] = module
    try:
        code = compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
        exec(code, module.__dict__)
    finally:
        sys.modules.pop(module_name, None)
    return _module_tools(module)


def _module_tools(module: ModuleType) -> tuple[Tool, ...]:
    return tuple(value for value in vars(module).values() if isinstance(value, Tool))


def _duplicate_warning(tools: tuple[Tool, ...]) -> Warning | None:
    found: dict[str, Tool] = {}
    for current in tools:
        previous = found.get(current.name)
        if previous is not None:
            sources = f"{previous.source}, {current.source}"
            return Warning(
                source=sources,
                message=f'Tool "{current.name}" is exported by both sources.',
            )
        found[current.name] = current
    return None
