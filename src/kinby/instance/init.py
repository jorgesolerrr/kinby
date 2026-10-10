"""Write a readable starter instance to disk."""

from __future__ import annotations

import re
import tomllib
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

import yaml

from kinby.contracts import SetupValue, TargetFile
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
from kinby.instance.recap import DEFAULT_RECAP_LENS
from kinby.instance.toml import TomlValue, toml_document
from kinby.packages import PACKAGE_CONFIG_NAME, InstalledPackage

PLACEHOLDER_MODEL = "provider:model"
README_NAME = "README.md"
_PROTECTED_TEMPLATE_ROOTS = {STATE_DIR, WORKSPACE_DIR}
#: A package offers its skills and tools from its own install, so its template's copies stay out.
_PACKAGE_REFERENCED_ROOTS = frozenset({SKILLS_DIR, TOOLS_DIR})
_FORBIDDEN_TEMPLATE_MANIFEST_KEYS = ("id", "persona_name", "state_dir", "package")


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


def _copied_template_path(name: str, *, referenced: frozenset[str]) -> Path | None:
    """Where a template file lands in the instance. None for one the instance does not copy.

    *referenced* names the folders a package offers from its own install instead.
    """
    relative = _relative_template_path(name)
    if name == MANIFEST_NAME or relative.parts[0] in referenced:
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


def _targeted(
    package: InstalledPackage,
    config: Mapping[str, SetupValue],
    file: TargetFile,
) -> dict[str, SetupValue]:
    """The values that land in *file*, by the dotted key each field targets."""
    targets = {field.name: field.target for field in package.descriptor.setup_fields}
    landed: dict[str, SetupValue] = {}
    for name, value in config.items():
        target = targets.get(name)
        if target is None:
            raise ValueError(f'Setup field "{name}" names no target to write its value at.')
        if target.file is file:
            landed[target.key] = value
    return landed


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
    try:
        toml_document(template)
    except TypeError as exc:
        raise ValueError("Template manifest cannot be serialized.") from exc
    return template


def _package_template_manifest(
    package: InstalledPackage,
    config: Mapping[str, SetupValue],
) -> dict[str, TomlValue]:
    return _template_manifest(
        package.files.get(MANIFEST_NAME, ""),
        _targeted(package, config, TargetFile.KINBY_TOML),
    )


def _validate_template(files: Mapping[str, str], *, referenced: frozenset[str]) -> None:
    for name in files:
        _copied_template_path(name, referenced=referenced)


def _validate_package_template(
    package: InstalledPackage,
    config: Mapping[str, SetupValue],
) -> None:
    _validate_template(package.files, referenced=_PACKAGE_REFERENCED_ROOTS)
    _package_template_manifest(package, config)


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


def _package_manifest(
    directory: Path,
    package: InstalledPackage,
    *,
    model: str,
    config: Mapping[str, SetupValue],
) -> None:
    base = _merged_manifest(directory, _package_template_manifest(package, config), model=model)
    descriptor = package.descriptor
    base["package"] = {
        "id": descriptor.id,
        "distribution": descriptor.distribution,
        "version": descriptor.version,
    }
    (directory / MANIFEST_NAME).write_text(toml_document(base), encoding="utf-8")


def _copy_template(
    directory: Path,
    files: Mapping[str, str],
    *,
    referenced: frozenset[str],
) -> None:
    for name, body in files.items():
        relative = _copied_template_path(name, referenced=referenced)
        if relative is None:
            continue
        destination = directory / relative
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(body, encoding="utf-8")
        except IsADirectoryError, NotADirectoryError, FileExistsError:
            raise ValueError(f'Template cannot copy "{name}".') from None


def _package_config(
    directory: Path,
    package: InstalledPackage,
    config: Mapping[str, SetupValue],
) -> None:
    """Write the values that land in package.yaml. A file they leave as it was keeps its comments.

    The package's validator runs later, over the file this writes.
    """
    landed = _targeted(package, config, TargetFile.PACKAGE_YAML)
    if not landed:
        return
    path = directory / PACKAGE_CONFIG_NAME
    document = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if document is None:
        document = {}
    if not isinstance(document, dict):
        raise ValueError(f"{PACKAGE_CONFIG_NAME} must be a mapping to take setup values.")
    before = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    for key, value in landed.items():
        _set_key(document, key, value)
    after = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    if after != before:
        path.write_text(after, encoding="utf-8")


def _write_starter_tree(directory: Path, model: str) -> None:
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
        "When kinby's packaged defaults are enabled, read the write-routine skill "
        "for the format.",
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
    _validate_template(files, referenced=frozenset())
    template = _template_manifest(files.get(MANIFEST_NAME, ""), settings)
    directory.mkdir(parents=True)
    _write_starter_tree(directory, model)
    _copy_template(directory, files, referenced=frozenset())
    manifest = _merged_manifest(directory, template, model=model)
    (directory / MANIFEST_NAME).write_text(toml_document(manifest), encoding="utf-8")
    return directory


def _publish_into_existing(source: Path, destination: Path) -> None:
    if any(destination.iterdir()):
        raise InstanceExistsError(f"instance directory is not empty: {destination}")
    for child in source.iterdir():
        target = destination / child.name
        if target.exists():
            raise InstanceExistsError(f"instance directory is not empty: {destination}")
        child.replace(target)


def _publish_directory(source: Path, destination: Path) -> None:
    if destination.exists():
        _publish_into_existing(source, destination)
    else:
        source.replace(destination)


def init_instance(
    directory: Path,
    model: str = PLACEHOLDER_MODEL,
    *,
    package: InstalledPackage | None = None,
    config: Mapping[str, SetupValue] | None = None,
) -> Path:
    """Write a readable starter instance at *directory*.

    *config* holds values for the package's configuration fields by name. Each lands at the
    target its field declares, in kinby.toml or package.yaml.
    """
    config = config or {}
    directory = Path(directory).resolve()
    manifest = directory / MANIFEST_NAME
    if manifest.is_file():
        raise InstanceExistsError(f"instance already exists: {manifest}")
    if directory.is_dir() and any(directory.iterdir()):
        raise InstanceExistsError(f"instance directory is not empty: {directory}")
    if package is not None:
        _validate_package_template(package, config)
    parent = directory.parent
    parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=parent, prefix=f".{directory.name}.creating-") as temporary:
        staging = Path(temporary) / directory.name
        staging.mkdir()
        _write_starter_tree(staging, model)
        if package is not None:
            _copy_template(staging, package.files, referenced=_PACKAGE_REFERENCED_ROOTS)
            _package_config(staging, package, config)
            _package_manifest(staging, package, model=model, config=config)
        _publish_directory(staging, directory)
    return directory.resolve()
