"""Read what an instance's instance code declares in its pyproject.toml, and the environment its
scripts and authored tools run in."""

import os
import re
import tomllib
from pathlib import Path
from typing import TypedDict

from pydantic import TypeAdapter, ValidationError

from kinby.instance.layout import (
    CODE_DIR,
    CODE_PROJECT_NAME,
    CODE_TOOLS_DIR,
    CODE_VENV_DIR,
)

_SECRET_NAME = re.compile("KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.IGNORECASE)


class _KinbyTable(TypedDict, total=False):
    secrets: list[str]
    tools: list[str]


class _ToolTable(TypedDict, total=False):
    kinby: _KinbyTable


class _Project(TypedDict, total=False):
    tool: _ToolTable


_PROJECT = TypeAdapter(_Project)


def declared_secrets(instance_path: Path) -> tuple[str, ...]:
    """The variables the instance code lists under `secrets` in its `[tool.kinby]` table.
    Empty while it has no project."""
    return tuple(_kinby_table(instance_path).get("secrets", []))


def exposed_tools(instance_path: Path) -> tuple[str, ...]:
    """The authored tools the instance code lists under `tools` in its `[tool.kinby]` table.
    Empty while it has no project."""
    return tuple(_kinby_table(instance_path).get("tools", []))


def authored_tool_module(name: str) -> str:
    """The module of the authored tool *name*, by its path in the instance."""
    return f"{CODE_DIR}/{CODE_TOOLS_DIR}/{name}.py"


def code_python(instance_path: Path) -> Path:
    """The Python of the instance code's venv."""
    return instance_path / CODE_DIR / CODE_VENV_DIR / "bin" / "python"


def script_environment(instance_path: Path) -> dict[str, str]:
    """The container's environment without the variables named like secrets that the instance
    code does not declare."""
    declared = set(declared_secrets(instance_path))
    return {
        name: value
        for name, value in os.environ.items()
        if name in declared or _SECRET_NAME.search(name) is None
    }


def _kinby_table(instance_path: Path) -> _KinbyTable:
    path = instance_path / CODE_DIR / CODE_PROJECT_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        project = _PROJECT.validate_python(tomllib.loads(text))
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ValueError(
            f"{CODE_DIR}/{CODE_PROJECT_NAME} must be TOML whose [tool.kinby] secrets and tools "
            f"are lists of names: {exc}"
        ) from exc
    return project.get("tool", {}).get("kinby", {})
