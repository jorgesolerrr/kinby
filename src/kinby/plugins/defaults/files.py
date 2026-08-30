"""Default workspace file tools."""

import re
from pathlib import Path

from kinby.plugins import ToolContext, tool


@tool(write=False)
def read(path: str, context: ToolContext) -> str:
    """Read a UTF-8 text file from the workspace."""
    return _workspace_path(context.workspace, path).read_text(encoding="utf-8")


@tool(write=True)
def write(path: str, content: str, context: ToolContext) -> str:
    """Write a UTF-8 text file in the workspace."""
    target = _workspace_path(context.workspace, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {_relative_path(context.workspace, target)}."


@tool(write=True)
def edit(path: str, old: str, new: str, context: ToolContext) -> str:
    """Replace text in a UTF-8 workspace file."""
    target = _workspace_path(context.workspace, path)
    content = target.read_text(encoding="utf-8")
    if old not in content:
        relative = _relative_path(context.workspace, target)
        raise ValueError(f'Text was not found in "{relative}".')
    target.write_text(content.replace(old, new, 1), encoding="utf-8")
    return f"Edited {_relative_path(context.workspace, target)}."


@tool(write=False)
def grep(pattern: str, path: str, context: ToolContext) -> str:
    """Find text in workspace files."""
    target = _workspace_path(context.workspace, path)
    files = (
        [target]
        if target.is_file()
        else sorted(item for item in target.rglob("*") if item.is_file())
    )
    expression = re.compile(pattern)
    matches: list[str] = []
    for file in files:
        for line_number, line in enumerate(
            file.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if expression.search(line):
                relative = _relative_path(context.workspace, file)
                matches.append(f"{relative}:{line_number}:{line}")
    return "\n".join(matches)


@tool(write=False)
def glob(pattern: str, context: ToolContext) -> str:
    """List workspace paths matching a glob pattern."""
    return "\n".join(
        _relative_path(context.workspace, _workspace_path(context.workspace, path))
        for path in sorted(context.workspace.glob(pattern))
    )


def _workspace_path(workspace: Path, path: str | Path) -> Path:
    root = workspace.resolve()
    target = (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError(f'Path "{path}" is outside the workspace.') from None
    return target


def _relative_path(workspace: Path, path: Path) -> str:
    return path.relative_to(workspace.resolve()).as_posix()
