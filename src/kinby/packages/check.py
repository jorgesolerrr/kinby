"""Run kinby's own install path against one installed package and report every failure.

The package check needs no network and runs no routine or model. The hub's
candidate check runs it inside a prepared image, so CI and the hub agree.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.metadata import entry_points
from pathlib import Path
from tempfile import TemporaryDirectory

from kinby.contracts import Warning
from kinby.instance import Instance, InstanceExistsError, init_instance, load_instance
from kinby.packages import (
    PACKAGE_CONFIG_NAME,
    LoadedPackage,
    Package,
    PackageConfigError,
    installed_package,
    load_package,
    read_package_config,
    readable_template_files,
)
from kinby.plugins.registry import ToolRegistry
from kinby.plugins.routines import SharedCodeStep, load_routines, resolve_code_step
from kinby.plugins.skills import load_skills

_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_MARKDOWN_LINK = re.compile(r"\]\(([^)\s]+)\)")
_PLACEHOLDER_SECRET = "kinby-package-check"


def check_package(package_id: str, instance: Path | None = None) -> tuple[str, ...]:
    """Every failure kinby's install path finds in one installed package, one message each.

    With *instance*, the package.yaml of that existing instance is validated as well.
    """
    try:
        loaded = load_package(package_id)
    except (LookupError, TypeError) as exc:
        return (str(exc),)
    package = loaded.package
    template = Path(package.template).resolve()
    if not template.is_dir():
        return (
            f"Template is not a directory: {template}",
            *_secret_declarations(package),
            *_missing_executables(package),
        )
    failures = [
        *_unshipped_template_files(loaded, template),
        *_secret_declarations(package),
        *_secret_values(package, template),
        *_missing_executables(package),
        *_template_validation(package, template),
        *_config_failures(package, template),
        *_initialization_failures(loaded),
    ]
    if instance is not None:
        failures.extend(_config_failures(package, instance))
    return tuple(failures)


def _unshipped_template_files(loaded: LoadedPackage, template: Path) -> list[str]:
    distribution = loaded.distribution
    recorded = {
        Path(str(distribution.locate_file(file))).resolve() for file in distribution.files or ()
    }
    return [
        f"Template file {name} is not part of distribution {distribution.name}."
        for name in readable_template_files(template)
        if (template / name).resolve() not in recorded
    ]


def _secret_declarations(package: Package) -> Iterator[str]:
    seen: set[str] = set()
    for secret in package.required_secrets:
        if secret.name in seen:
            yield f'Required secret "{secret.name}" is declared more than once.'
            continue
        seen.add(secret.name)
        if _ENVIRONMENT_NAME.fullmatch(secret.name) is None:
            yield f'Required secret "{secret.name}" is not an environment variable name.'
        if not secret.label.strip():
            yield f'Required secret "{secret.name}" has no label.'
        if not secret.description.strip():
            yield f'Required secret "{secret.name}" has no description.'


def _missing_executables(package: Package) -> Iterator[str]:
    for executable in package.executables:
        if shutil.which(executable) is None:
            yield f'Executable "{executable}" is not on PATH.'


def _secret_values(package: Package, template: Path) -> Iterator[str]:
    """A line that assigns a declared secret, in a .env, shell, TOML or YAML style."""
    for name, body in readable_template_files(template).items():
        for secret in package.required_secrets:
            assignment = re.compile(
                rf"^\s*(?:export\s+)?[\"']?{re.escape(secret.name)}[\"']?\s*[:=][ \t]*"
                r"(?![\"']{2}|#)\S",
                re.MULTILINE,
            )
            if assignment.search(body):
                yield f'Template file {name} holds a value for the secret "{secret.name}".'


def _template_validation(package: Package, template: Path) -> Iterator[str]:
    if package.validate is None:
        return
    try:
        package.validate(template)
    except Exception as exc:
        # The validator is package code; the check reports whatever it raises.
        yield f"Template validation failed: {exc}"


def _config_failures(package: Package, directory: Path) -> Iterator[str]:
    if package.config is None:
        if (directory / PACKAGE_CONFIG_NAME).exists():
            yield f"{directory / PACKAGE_CONFIG_NAME}: the package declares no config validator."
        return
    try:
        read_package_config(package, directory)
    except PackageConfigError as exc:
        yield str(exc)


def _initialization_failures(loaded: LoadedPackage) -> list[str]:
    with TemporaryDirectory(prefix="kinby-package-check-") as temporary:
        try:
            path = init_instance(Path(temporary) / "instance", package=installed_package(loaded))
            instance = load_instance(path)
        except (InstanceExistsError, ValueError) as exc:
            return [f"The template does not initialize: {exc}"]
        return [
            *_routine_failures(instance, loaded.package),
            *_skill_failures(instance, loaded),
        ]


def _routine_failures(instance: Instance, package: Package) -> Iterator[str]:
    with _placeholder_secrets(package):
        routines, warnings = load_routines(instance)
    yield from _instance_warnings(instance, warnings)
    tools, _ = ToolRegistry(instance.path, defaults=instance.manifest.tools.defaults).refresh()
    for routine in routines:
        if isinstance(routine.code_step, SharedCodeStep):
            try:
                resolve_code_step(routine.code_step, tools)
            except ValueError as exc:
                yield f"{routine.source.relative_to(instance.path)}: {exc}"


@contextmanager
def _placeholder_secrets(package: Package) -> Iterator[None]:
    """A signal routine loads only when its secret is set, and the check holds no secrets."""
    unset = [secret.name for secret in package.required_secrets if secret.name not in os.environ]
    os.environ.update(dict.fromkeys(unset, _PLACEHOLDER_SECRET))
    try:
        yield
    finally:
        for name in unset:
            os.environ.pop(name, None)


def _instance_warnings(instance: Instance, warnings: tuple[Warning, ...]) -> Iterator[str]:
    for warning in warnings:
        source = Path(warning.sources[0])
        shown = (
            source.relative_to(instance.path) if source.is_relative_to(instance.path) else source
        )
        yield f"{shown}: {warning.message}"


def _skill_failures(instance: Instance, loaded: LoadedPackage) -> Iterator[str]:
    roots: list[Path] = []
    for entry in entry_points(group="kinby.skills"):
        if entry.dist is None or entry.dist.name != loaded.distribution.name:
            continue
        root = entry.load()
        if not isinstance(root, Path) or not root.is_dir():
            yield f'Skill entry point "{entry.value}" does not export a skill directory Path.'
            continue
        roots.append(root)
    if not roots:
        return
    skills, warnings = load_skills(instance)
    yield from (
        f"{warning.sources[0]}: {warning.message}"
        for warning in warnings
        if any(Path(source).is_relative_to(root) for source in warning.sources for root in roots)
    )
    for skill in skills:
        if not any(skill.source.is_relative_to(root) for root in roots):
            continue
        directory = skill.source.parent
        for target in _MARKDOWN_LINK.findall(skill.body):
            companion = target.split("#", 1)[0]
            if not companion or "://" in companion or companion.startswith("mailto:"):
                continue
            if not (directory / companion).exists():
                yield f'Skill "{skill.name}" links to {companion}, which is not in {directory}.'
