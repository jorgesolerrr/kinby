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

from kinby.contracts import (
    SetupField,
    SetupFieldKind,
    SetupFieldType,
    SetupValue,
    TargetFile,
    Warning,
)
from kinby.instance import Instance, InstanceExistsError, init_instance, load_instance
from kinby.instance.permissions import PermissionsError
from kinby.packages import (
    BUILT_IN_FIELDS,
    PACKAGE_CONFIG_NAME,
    LoadedPackage,
    Package,
    PackageConfigError,
    installed_package,
    load_package,
    package_fields,
    read_package_config,
    readable_template_files,
    resolved_values,
    secret_names,
    value_problem,
)
from kinby.plugins.errors import exception_message
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
    declarations = list(_field_declarations(package))
    if not template.is_dir():
        return (
            f"Template is not a directory: {template}",
            *declarations,
            *_missing_executables(package),
        )
    template_config = list(_config_failures(package, template))
    # Defaults land only where the declarations and the template's own config hold,
    # so one mistake is reported once.
    defaults = {} if declarations or template_config else _defaults_at_targets(package)
    failures = [
        *_unshipped_template_files(loaded, template),
        *declarations,
        *_secret_values(package, template),
        *_missing_executables(package),
        *_template_validation(package, template),
        *template_config,
        *_initialization_failures(loaded, defaults),
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


def _field_declarations(package: Package) -> Iterator[str]:
    built_in = {field.name: field for field in BUILT_IN_FIELDS}
    seen: set[str] = set()
    for field in package.declared_fields():
        if field.name in seen:
            yield f'Setup field "{field.name}" is declared more than once.'
            continue
        seen.add(field.name)
        yield from _field_failures(package, field)
        overridden = built_in.get(field.name)
        if overridden is None:
            yield from _target_failures(package, field)
        else:
            yield from _override_failures(field, overridden)


def _field_failures(package: Package, field: SetupField) -> Iterator[str]:
    name = field.name
    secret = field.kind is SetupFieldKind.SECRET
    if secret and _ENVIRONMENT_NAME.fullmatch(name) is None:
        yield f'Secret field "{name}" is not an environment variable name.'
    if not field.label.strip():
        yield f'Setup field "{name}" has no label.'
    if not field.description.strip():
        yield f'Setup field "{name}" has no description.'
    if field.choices is not None and field.type is not SetupFieldType.CHOICE:
        yield f'Setup field "{name}" has choices, but it is not a choice field.'
    if field.type is SetupFieldType.CHOICE and not field.choices:
        yield f'Choice field "{name}" offers no choices.'
    if secret and field.type not in (SetupFieldType.TEXT, SetupFieldType.MULTILINE):
        yield f'Secret field "{name}" is {field.type}. A secret is text or multiline.'
    if field.default is None:
        return
    if secret:
        yield f'Secret field "{name}" has a default. A secret never ships in a package.'
    elif (problem := value_problem(field, field.default)) is not None:
        yield f'Setup field "{name}" has a default that {problem}.'


def _target_failures(package: Package, field: SetupField) -> Iterator[str]:
    name = field.name
    if field.kind is SetupFieldKind.SECRET:
        if field.target is not None:
            yield f'Secret field "{name}" has a target. A secret lands in the instance secrets.'
    elif field.target is None:
        yield f'Configuration field "{name}" names no target.'
    elif field.target.file is TargetFile.PACKAGE_YAML and package.config is None:
        yield (
            f'Setup field "{name}" lands in package.yaml, '
            "but the package declares no config validator."
        )


def _override_failures(field: SetupField, built_in: SetupField) -> Iterator[str]:
    if (field.kind, field.type) != (built_in.kind, built_in.type):
        yield (
            f'Setup field "{field.name}" overrides a built-in field, so it keeps its kind and type.'
        )
    if field.target is not None:
        yield (
            f'Setup field "{field.name}" overrides a built-in field, which kinby writes itself, '
            "so it takes no target."
        )


def _defaults_at_targets(package: Package) -> dict[str, SetupValue]:
    """The defaults initialization writes at their targets when the user changes nothing."""
    targeted = [field for field in package_fields(package.declared_fields()) if field.target]
    return resolved_values(targeted, {})


def _missing_executables(package: Package) -> Iterator[str]:
    for executable in package.executables:
        if shutil.which(executable) is None:
            yield f'Executable "{executable}" is not on PATH.'


def _secret_values(package: Package, template: Path) -> Iterator[str]:
    """A line that assigns a declared secret, in a .env, shell, TOML or YAML style."""
    secrets = secret_names(package.declared_fields())
    for name, body in readable_template_files(template).items():
        for secret in sorted(secrets):
            assignment = re.compile(
                rf"^\s*(?:export\s+)?[\"']?{re.escape(secret)}[\"']?\s*[:=][ \t]*"
                r"(?![\"']{2}|#)\S",
                re.MULTILINE,
            )
            if assignment.search(body):
                yield f'Template file {name} holds a value for the secret "{secret}".'


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


def _initialization_failures(loaded: LoadedPackage, defaults: dict[str, SetupValue]) -> list[str]:
    with TemporaryDirectory(prefix="kinby-package-check-") as temporary:
        try:
            path = init_instance(
                Path(temporary) / "instance",
                package=installed_package(loaded),
                config=defaults,
            )
            instance = load_instance(path)
        except (InstanceExistsError, ValueError) as exc:
            return [f"The template does not initialize: {exc}"]
        return [
            *_written_config_failures(loaded.package, instance, defaults),
            *_routine_failures(instance, loaded.package),
            *_skill_failures(instance, loaded),
        ]


def _written_config_failures(
    package: Package,
    instance: Instance,
    defaults: dict[str, SetupValue],
) -> Iterator[str]:
    """The package.yaml a new instance gets once each default lands at its target."""
    in_package_yaml = any(
        field.target is not None and field.target.file is TargetFile.PACKAGE_YAML
        for field in package.declared_fields()
        if field.name in defaults
    )
    if in_package_yaml:
        yield from _config_failures(package, instance.path)


def _routine_failures(instance: Instance, package: Package) -> Iterator[str]:
    with _placeholder_secrets(package):
        try:
            routines, warnings = load_routines(instance)
        except PermissionsError as exc:
            # load_permissions runs before the per-routine handler, so a bad
            # permissions.toml would otherwise abort the whole check.
            yield str(exc)
            return
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
    unset = sorted(secret_names(package.declared_fields()) - os.environ.keys())
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
        try:
            root = entry.load()
        except Exception as exc:
            yield f'Skill entry point "{entry.value}" failed to load: {exception_message(exc)}'
            continue
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
