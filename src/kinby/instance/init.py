"""Write a readable starter instance to disk."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from kinby.instance.errors import InstanceExistsError
from kinby.instance.layout import (
    ENV_NAME,
    GITIGNORE_NAME,
    MANIFEST_NAME,
    MEMORY_DIR,
    PERMISSIONS_NAME,
    PROFILE_NAME,
    ROUTINES_DIR,
    SKILLS_DIR,
    STATE_DIR,
    SYSTEM_NAME,
    TOOLS_DIR,
    WORKSPACE_DIR,
)

PLACEHOLDER_MODEL = "provider:model"
README_NAME = "README.md"


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


def init_instance(directory: Path, model: str | None = None) -> Path:
    """Write a readable starter instance at *directory*."""
    directory = Path(directory)
    manifest = directory / MANIFEST_NAME
    if manifest.is_file():
        raise InstanceExistsError(f"instance already exists: {manifest}")
    directory.mkdir(parents=True, exist_ok=True)

    instance_id = _slugify(directory.name)
    main_model = model if model is not None else PLACEHOLDER_MODEL

    (directory / MANIFEST_NAME).write_text(
        (
            "# Instance manifest. Commit this file; keep secrets in the environment.\n"
            "# id stays the same if you move the directory. "
            "[models].main is the only required setting.\n"
            "\n"
            f"id = {_toml_string(instance_id)}\n"
            "\n"
            "[models]\n"
            f"main = {_toml_string(main_model)}\n"
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
    (directory / PERMISSIONS_NAME).write_text(
        "# What this instance is allowed to do. The permission gate is not implemented yet.\n",
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
    (directory / GITIGNORE_NAME).write_text(
        (
            "# Runtime state and local secrets stay off git.\n"
            f"{STATE_DIR}/\n"
            f"{ENV_NAME}\n"
        ),
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
        "Routines live here: a trigger, a prompt, and a destination.",
    )
    (directory / WORKSPACE_DIR).mkdir(exist_ok=True)
    (directory / STATE_DIR).mkdir(exist_ok=True)

    return directory.resolve()
