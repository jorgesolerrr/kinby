"""Write a readable starter instance to disk."""

from __future__ import annotations

import errno
import os
import re
import tomllib
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

import tomlkit

from kinby.contracts import SetupValue
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
    ROUTINE_FILE,
    ROUTINES_DIR,
    SKILLS_DIR,
    STATE_DIR,
    SYSTEM_NAME,
    TOOLS_DIR,
    WORKSPACE_DIR,
)
from kinby.instance.recap import DEFAULT_RECAP_LENS

PLACEHOLDER_MODEL = "provider:model"
README_NAME = "README.md"
#: The ``SYSTEM.md`` of an instance that starts from kinby's defaults.
DEFAULT_BEHAVIOR_PROMPT = (
    "<!-- SYSTEM.md is this instance's behavior prompt. "
    "Edit it to change how the agent acts. -->\n"
    "\n"
    "You are a personal AI teammate.\n"
)
#: The body of a new instance's consolidation routine: what that dreaming stage looks for.
DEFAULT_CONSOLIDATION_LENS = (
    "Look for lessons the episodes repeat: a mistake made twice, a fix that worked again, "
    "a preference the user restated. Look for areas of the work they share, such as a "
    "feature of a repository or a recurring errand. Prefer a few facts a later session "
    "would search for over many small ones."
)
#: The body of a new instance's reflection routine: what that dreaming stage looks for.
DEFAULT_REFLECTION_LENS = (
    "Look for how the user works: a workflow they repeat that a skill could hold, work they "
    "do on a schedule that a routine could do, a pattern a later search should find as a "
    "fact. Propose a few things the week clearly shows, through remember, skill_write or "
    "routine_write, rather than many guesses."
)
_PROTECTED_TEMPLATE_ROOTS = {STATE_DIR, WORKSPACE_DIR}
_FORBIDDEN_TEMPLATE_MANIFEST_KEYS = ("id", "persona_name", "state_dir")

type TomlValue = str | int | float | bool | list["TomlValue"] | dict[str, "TomlValue"]


def _slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return text.strip("-") or "instance"


def _is_toml_value(value: object) -> bool:
    if isinstance(value, dict):
        return all(_is_toml_value(item) for item in value.values())
    if isinstance(value, list):
        return all(not isinstance(item, dict) and _is_toml_value(item) for item in value)
    return isinstance(value, str | int | float)


def _write_readme(directory: Path, explanation: str) -> None:
    directory.mkdir(exist_ok=True)
    (directory / README_NAME).write_text(
        f"<!-- {explanation} -->\n",
        encoding="utf-8",
    )


def _merge(base: dict[str, TomlValue], override: dict[str, TomlValue]) -> None:
    for key, value in override.items():
        current = base.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            _merge(current, value)
        else:
            base[key] = value


def _relative_template_path(name: str) -> Path:
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError(f'Template path is invalid: "{name}".')
    return relative


def _copied_template_path(name: str) -> Path | None:
    """Where a template file lands in the instance. None for one the instance does not copy."""
    relative = _relative_template_path(name)
    if name == MANIFEST_NAME:
        return None
    if relative.parts[0] in _PROTECTED_TEMPLATE_ROOTS or name == ENV_NAME:
        raise ValueError(f'Template cannot copy "{name}".')
    if relative.parts[0] == MEMORY_DIR and name != f"{MEMORY_DIR}/{PROFILE_NAME}":
        raise ValueError(f'Template cannot copy "{name}".')
    return relative


def _set_key(document: dict[str, TomlValue], key: str, value: SetupValue) -> None:
    """Set a dotted key, making the tables on its way that do not exist yet."""
    *tables, last = key.split(".")
    for part in tables:
        table = document.setdefault(part, {})
        if not isinstance(table, dict):
            raise ValueError(f'Setup target "{key}" runs through "{part}", which is not a table.')
        document = table
    document[last] = value


def _template_manifest(body: str, settings: Mapping[str, SetupValue]) -> dict[str, TomlValue]:
    """A template's kinby.toml with *settings* set at their dotted keys, checked for merging."""
    template = cast(dict[str, TomlValue], tomllib.loads(body))
    for key, value in settings.items():
        _set_key(template, key, value)
    forbidden = [key for key in _FORBIDDEN_TEMPLATE_MANIFEST_KEYS if key in template]
    if forbidden:
        raise ValueError(f'Template cannot set "{forbidden[0]}".')
    models = template.get("models")
    if models is not None and not isinstance(models, dict):
        raise ValueError("Template [models] must be a table.")
    if not _is_toml_value(template):
        raise ValueError("Template manifest cannot hold an array of tables or a date.")
    return template


def _validate_template(files: Mapping[str, str]) -> None:
    for name in files:
        _copied_template_path(name)


def _merged_manifest(
    directory: Path,
    template: dict[str, TomlValue],
    *,
    model: str,
) -> dict[str, TomlValue]:
    """The starter manifest in *directory* with the template's merged over it, and the model."""
    base = cast(
        dict[str, TomlValue],
        tomllib.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8")),
    )
    _merge(base, template)
    models = base["models"]
    if not isinstance(models, dict):
        raise ValueError("Template [models] must be a table.")
    models["main"] = model
    return base


def _copy_template(directory: Path, files: Mapping[str, str]) -> None:
    for name, body in files.items():
        relative = _copied_template_path(name)
        if relative is None:
            continue
        destination = directory / relative
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(body, encoding="utf-8")
        except IsADirectoryError, NotADirectoryError, FileExistsError:
            raise ValueError(f'Template cannot copy "{name}".') from None


def _write_starter_tree(directory: Path, model: str) -> None:
    instance_id = _slugify(directory.name)
    (directory / MANIFEST_NAME).write_text(
        (
            "# Instance manifest. Commit this file; keep secrets in the environment.\n"
            "# id stays the same if you move the directory. "
            "[models].main is the only required setting.\n"
            "\n"
            f"id = {tomlkit.string(instance_id).as_string()}\n"
            "\n"
            "[models]\n"
            f"main = {tomlkit.string(model).as_string()}\n"
            "\n"
            "[memory]\n"
            f"recap = {tomlkit.string(RecapPolicy.EVERY_TURN).as_string()}\n"
            "\n"
            "[feedback]\n"
            f"ask = {tomlkit.string(FeedbackPolicy.EVERY_TURN).as_string()}\n"
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
    (directory / SYSTEM_NAME).write_text(DEFAULT_BEHAVIOR_PROMPT, encoding="utf-8")
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
            "# kinby always denies deleting the instance home and rewriting or force-pushing\n"
            "# Git history. Patterns here add to those.\n"
            "deny = []\n"
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
        "When kinby's defaults are enabled, read the write-routine skill "
        "for the format.",
    )
    (directory / ROUTINES_DIR / "consolidation").mkdir()
    (directory / ROUTINES_DIR / "consolidation" / ROUTINE_FILE).write_text(
        (
            "---\n"
            "description: Consolidate the episodes no consolidation has covered into cited facts.\n"
            "schedule: 0 3 * * *\n"
            "run: consolidate\n"
            "---\n"
            f"{DEFAULT_CONSOLIDATION_LENS}\n"
        ),
        encoding="utf-8",
    )
    (directory / ROUTINES_DIR / "reflection").mkdir()
    (directory / ROUTINES_DIR / "reflection" / ROUTINE_FILE).write_text(
        (
            "---\n"
            "description: Reflect on what consolidation learned this week and make proposals.\n"
            "schedule: 0 4 * * 0\n"
            "mode: ask\n"
            "run: reflect\n"
            "---\n"
            f"{DEFAULT_REFLECTION_LENS}\n"
        ),
        encoding="utf-8",
    )
    (directory / WORKSPACE_DIR).mkdir(exist_ok=True)
    (directory / STATE_DIR).mkdir(exist_ok=True)


def init_from_template(
    directory: Path,
    files: Mapping[str, str],
    *,
    model: str,
    settings: Mapping[str, SetupValue],
) -> Path:
    """Write an instance at *directory*, which must not exist, from a factory's instance template.

    Every file of the template lands over kinby's starter tree, and its kinby.toml merges into
    the starter manifest. *settings* are values for kinby.toml, by the dotted key each lands at.
    """
    directory = Path(directory).resolve()
    if directory.exists():
        raise InstanceExistsError(f"instance directory already exists: {directory}")
    _validate_template(files)
    template = _template_manifest(files.get(MANIFEST_NAME, ""), settings)
    directory.mkdir(parents=True)
    _write_starter_tree(directory, model)
    _copy_template(directory, files)
    manifest = _merged_manifest(directory, template, model=model)
    (directory / MANIFEST_NAME).write_text(tomlkit.dumps(manifest), encoding="utf-8")
    return directory


def _publish_into_existing(source: Path, destination: Path) -> None:
    if any(destination.iterdir()):
        raise InstanceExistsError(f"instance directory is not empty: {destination}")
    for child in source.iterdir():
        target = destination / child.name
        try:
            if child.is_dir():
                child.rename(target)
            else:
                # A hard link refuses a name that is taken; a rename would replace the file there.
                os.link(child, target)
        except OSError as exc:
            if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY, errno.ENOTDIR):
                raise
            raise InstanceExistsError(f"instance directory is not empty: {destination}") from None


def _publish_directory(source: Path, destination: Path) -> None:
    if destination.exists():
        _publish_into_existing(source, destination)
    else:
        source.replace(destination)


def init_instance(directory: Path, model: str = PLACEHOLDER_MODEL) -> Path:
    """Write a readable starter instance at *directory*."""
    directory = Path(directory).resolve()
    manifest = directory / MANIFEST_NAME
    if manifest.is_file():
        raise InstanceExistsError(f"instance already exists: {manifest}")
    if directory.is_dir() and any(directory.iterdir()):
        raise InstanceExistsError(f"instance directory is not empty: {directory}")
    parent = directory.parent
    parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=parent, prefix=f".{directory.name}.creating-") as temporary:
        staging = Path(temporary) / directory.name
        staging.mkdir()
        _write_starter_tree(staging, model)
        _publish_directory(staging, directory)
    return directory.resolve()
