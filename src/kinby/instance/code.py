"""Read what an instance's instance code declares in its pyproject.toml."""

import tomllib
from pathlib import Path
from typing import TypedDict

from pydantic import TypeAdapter, ValidationError

from kinby.instance.layout import CODE_DIR, CODE_PROJECT_NAME


class _KinbyTable(TypedDict, total=False):
    secrets: list[str]


class _ToolTable(TypedDict, total=False):
    kinby: _KinbyTable


class _Project(TypedDict, total=False):
    tool: _ToolTable


_PROJECT = TypeAdapter(_Project)


def declared_secrets(instance_path: Path) -> tuple[str, ...]:
    """The variables the instance code lists under `secrets` in its `[tool.kinby]` table.
    Empty while it has no project."""
    path = instance_path / CODE_DIR / CODE_PROJECT_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ()
    try:
        project = _PROJECT.validate_python(tomllib.loads(text))
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ValueError(
            f"{CODE_DIR}/{CODE_PROJECT_NAME} must be TOML whose [tool.kinby] secrets is a list "
            f"of variable names: {exc}"
        ) from exc
    return tuple(project.get("tool", {}).get("kinby", {}).get("secrets", []))
