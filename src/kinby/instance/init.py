"""Write a readable starter instance to disk."""

from __future__ import annotations

import json
import re
import tomllib
import unicodedata
from pathlib import Path
from typing import cast

from kinby.instance.dataclasses import FeedbackPolicy, RecapPolicy
from kinby.instance.errors import InstanceExistsError
from kinby.instance.layout import (
    ENV_NAME,
    GITIGNORE_NAME,
    GRAPH_DIR,
    MANIFEST_NAME,
    MEMORY_DIR,
    PERMISSIONS_NAME,
    PROFILE_NAME,
    RECAP_NAME,
    ROUTINES_DIR,
    SKILLS_DIR,
    STATE_DIR,
    SYSTEM_NAME,
    TOOLS_DIR,
    WORKSPACE_DIR,
)
from kinby.instance.permissions import SHIPPED_BASH_DENY
from kinby.instance.recap import DEFAULT_RECAP_LENS
from kinby.packages import InstalledPackage

PLACEHOLDER_MODEL = "provider:model"
README_NAME = "README.md"
_PROTECTED_TEMPLATE_ROOTS = {STATE_DIR, WORKSPACE_DIR}
_REFERENCED_TEMPLATE_ROOTS = {SKILLS_DIR, TOOLS_DIR}
_FORBIDDEN_TEMPLATE_MANIFEST_KEYS = ("id", "persona_name", "state_dir", "package")
_STARTER_DIRECTORIES = frozenset(
    {
        MEMORY_DIR,
        f"{MEMORY_DIR}/{GRAPH_DIR}",
        TOOLS_DIR,
        SKILLS_DIR,
        ROUTINES_DIR,
        WORKSPACE_DIR,
        STATE_DIR,
    }
)
_STARTER_FILES = frozenset(
    {
        MANIFEST_NAME,
        SYSTEM_NAME,
        RECAP_NAME,
        PERMISSIONS_NAME,
        f"{MEMORY_DIR}/{PROFILE_NAME}",
        GITIGNORE_NAME,
        f"{TOOLS_DIR}/{README_NAME}",
        f"{SKILLS_DIR}/{README_NAME}",
        f"{ROUTINES_DIR}/{README_NAME}",
    }
)


def _slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return text.strip("-") or "instance"


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _write_readme(directory: Path, explanation: str) -> None:
    directory.mkdir(exist_ok=True)
    (directory / README_NAME).write_text(
        f"<!-- {explanation} -->\n",
        encoding="utf-8",
    )


type TomlValue = str | int | float | bool | list["TomlValue"] | dict[str, "TomlValue"]


def _merge(base: dict[str, TomlValue], override: dict[str, TomlValue]) -> None:
    for key, value in override.items():
        current = base.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            _merge(current, value)
        else:
            base[key] = value


def _toml_key(key: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", key):
        return key
    return json.dumps(key)


def _toml_value(value: TomlValue) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError("TOML tables are written separately.")


def _toml_document(values: dict[str, TomlValue]) -> str:
    lines: list[str] = []

    def write_table(table: dict[str, TomlValue], path: tuple[str, ...]) -> None:
        if path:
            if lines and lines[-1]:
                lines.append("")
            lines.append("[" + ".".join(_toml_key(part) for part in path) + "]")
        for key, value in table.items():
            if not isinstance(value, dict):
                lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
        for key, value in table.items():
            if isinstance(value, dict):
                write_table(value, (*path, key))

    write_table(values, ())
    return "\n".join(lines) + "\n"


def _relative_template_path(name: str) -> Path:
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError(f'Package template path is invalid: "{name}".')
    return relative


def _copied_template_path(name: str) -> Path | None:
    relative = _relative_template_path(name)
    if name == MANIFEST_NAME or relative.parts[0] in _REFERENCED_TEMPLATE_ROOTS:
        return None
    if relative.parts[0] in _PROTECTED_TEMPLATE_ROOTS or name == ENV_NAME:
        raise ValueError(f'Package template cannot copy "{name}".')
    if relative.parts[0] == MEMORY_DIR and name != f"{MEMORY_DIR}/{PROFILE_NAME}":
        raise ValueError(f'Package template cannot copy "{name}".')
    posix = relative.as_posix()
    if posix in _STARTER_DIRECTORIES:
        raise ValueError(f'Package template cannot copy "{name}".')
    for index in range(len(relative.parts) - 1):
        ancestor = Path(*relative.parts[: index + 1]).as_posix()
        if ancestor in _STARTER_FILES:
            raise ValueError(f'Package template cannot copy "{name}".')
    return relative


def _package_template_manifest(package: InstalledPackage) -> dict[str, TomlValue]:
    template_body = package.files.get(MANIFEST_NAME, "")
    template = cast(dict[str, TomlValue], tomllib.loads(template_body))
    forbidden = [key for key in _FORBIDDEN_TEMPLATE_MANIFEST_KEYS if key in template]
    if forbidden:
        raise ValueError(f'Package template cannot set "{forbidden[0]}".')
    models = template.get("models")
    if models is not None and not isinstance(models, dict):
        raise ValueError("Package template [models] must be a table.")
    return template


def _validate_package_template(package: InstalledPackage) -> None:
    for name in package.files:
        _copied_template_path(name)
    template = _package_template_manifest(package)
    try:
        _toml_document(template)
    except TypeError as exc:
        raise ValueError("Package template manifest cannot be serialized.") from exc


def _package_manifest(
    directory: Path,
    package: InstalledPackage,
    *,
    model: str,
) -> None:
    path = directory / MANIFEST_NAME
    base = cast(dict[str, TomlValue], tomllib.loads(path.read_text(encoding="utf-8")))
    _merge(base, _package_template_manifest(package))
    models = base["models"]
    if not isinstance(models, dict):
        raise ValueError("Package template [models] must be a table.")
    models["main"] = model
    descriptor = package.descriptor
    base["package"] = {
        "id": descriptor.id,
        "distribution": descriptor.distribution,
        "version": descriptor.version,
    }
    path.write_text(_toml_document(base), encoding="utf-8")


def _copy_package_template(directory: Path, package: InstalledPackage) -> None:
    for name, body in package.files.items():
        relative = _copied_template_path(name)
        if relative is None:
            continue
        destination = directory / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(body, encoding="utf-8")


def init_instance(
    directory: Path,
    model: str = PLACEHOLDER_MODEL,
    *,
    package: InstalledPackage | None = None,
) -> Path:
    """Write a readable starter instance at *directory*."""
    directory = Path(directory)
    if package is not None and directory.is_dir() and any(directory.iterdir()):
        raise InstanceExistsError(f"instance directory is not empty: {directory}")
    manifest = directory / MANIFEST_NAME
    if manifest.is_file():
        raise InstanceExistsError(f"instance already exists: {manifest}")
    if package is not None:
        _validate_package_template(package)
    directory.mkdir(parents=True, exist_ok=True)

    instance_id = _slugify(directory.name)

    (directory / MANIFEST_NAME).write_text(
        (
            "# Instance manifest. Commit this file; keep secrets in the environment.\n"
            "# id stays the same if you move the directory. "
            "[models].main is the only required setting.\n"
            "\n"
            f"id = {_toml_string(instance_id)}\n"
            "\n"
            "[models]\n"
            f"main = {_toml_string(model)}\n"
            "\n"
            "[memory]\n"
            f"recap = {_toml_string(RecapPolicy.EVERY_TURN)}\n"
            "\n"
            "[feedback]\n"
            f"ask = {_toml_string(FeedbackPolicy.EVERY_TURN)}\n"
            "\n"
            "# [budgets]\n"
            "# One step is one node execution.\n"
            "# A budget of 7 steps allows four model calls and three tool rounds.\n"
            "# steps = 7\n"
            "# tokens = 50000\n"
            "# seconds = 300\n"
            "# usd_per_day = 5.0\n"
            "\n"
            "# [serve]\n"
            '# listen = "127.0.0.1:8484"\n'
        ),
        encoding="utf-8",
    )
    (directory / SYSTEM_NAME).write_text(
        (
            "<!-- SYSTEM.md is this instance's behavior prompt. "
            "Edit it to change how the agent acts. -->\n"
            "\n"
            "You are a personal AI teammate.\n"
        ),
        encoding="utf-8",
    )
    (directory / RECAP_NAME).write_text(
        (
            "<!-- RECAP.md tells the recap model what to examine after each turn. "
            "Edit it to change the retrospective. -->\n"
            "\n"
            f"{DEFAULT_RECAP_LENS}\n"
        ),
        encoding="utf-8",
    )
    (directory / PERMISSIONS_NAME).write_text(
        (
            "# Permission policy. Changes apply at the next turn boundary.\n"
            "# Modes: read-only denies writes, ask requests approval, "
            "and full-access allows writes.\n"
            "# auto allows writes with declared paths inside the workspace. It asks before\n"
            "# bash, undeclared write tools, and paths outside the workspace.\n"
            'mode = "ask"\n'
            'ceiling = "full-access"\n'
            "\n"
            "[tools]\n"
            "# Override any core or plugin tool without changing the mode.\n"
            '# bash = "deny"\n'
            '# edit = "allow"\n'
            "\n"
            "[bash]\n"
            "deny = [\n"
            "    # Delete the instance home.\n"
            f"    '''{SHIPPED_BASH_DENY[0]}''',\n"
            "    # Rewrite Git history.\n"
            f"    '''{SHIPPED_BASH_DENY[1]}''',\n"
            "    # Force-push Git history.\n"
            f"    '''{SHIPPED_BASH_DENY[2]}''',\n"
            "]\n"
            "ask = []\n"
        ),
        encoding="utf-8",
    )
    (directory / MEMORY_DIR).mkdir(exist_ok=True)
    (directory / MEMORY_DIR / PROFILE_NAME).write_text(
        (
            "<!-- The profile is the human-legible record of preferences, "
            "persona settings, and standing instructions. "
            "It is always in the agent's context; edit it directly. -->\n"
        ),
        encoding="utf-8",
    )
    (directory / MEMORY_DIR / GRAPH_DIR).mkdir(exist_ok=True)
    (directory / GITIGNORE_NAME).write_text(
        (f"# Runtime state and local secrets stay off git.\n{STATE_DIR}/\n{ENV_NAME}\n"),
        encoding="utf-8",
    )
    _write_readme(
        directory / TOOLS_DIR,
        "Instance-local tools live here. kinby does not load tools from the workspace.",
    )
    _write_readme(
        directory / SKILLS_DIR,
        "Instance-local skills live here.",
    )
    _write_readme(
        directory / ROUTINES_DIR,
        "Each routine is routines/<name>/ROUTINE.md with frontmatter and a prompt body. "
        "An optional run.py beside it supplies the code step. "
        "When kinby's packaged defaults are enabled, read the write-routine skill "
        "for the format.",
    )
    (directory / WORKSPACE_DIR).mkdir(exist_ok=True)
    (directory / STATE_DIR).mkdir(exist_ok=True)

    if package is not None:
        _copy_package_template(directory, package)
        _package_manifest(directory, package, model=model)

    return directory.resolve()
