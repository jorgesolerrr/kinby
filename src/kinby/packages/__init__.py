"""Package descriptors, package config, and instance templates read from prepared images."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import Distribution, entry_points, version
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,  # noqa: TID251 - the base every package config validator extends
    ConfigDict,
    ValidationError,
    ValidationInfo,
)

from kinby.contracts import PackageDescription, SetupField, SetupFieldKind, SetupFieldType

if TYPE_CHECKING:
    from kinby.instance import Instance

PACKAGE_CONFIG_NAME = "package.yaml"

#: What every instance asks for, whatever it starts from. kinby writes these values itself.
BUILT_IN_FIELDS = (
    SetupField(
        name="model",
        label="Model",
        description="The model the instance calls, as provider:model, like openai:gpt-5.",
        kind=SetupFieldKind.CONFIG,
        type=SetupFieldType.TEXT,
        required=True,
    ),
    SetupField(
        name="api_key",
        label="API key",
        description="The key your model provider issued. The hub keeps it with the instance's "
        "secrets and never shows it again.",
        kind=SetupFieldKind.SECRET,
        type=SetupFieldType.TEXT,
        required=True,
    ),
)
#: Only a vanilla instance asks for it. A package keeps the behavior prompt it ships.
BEHAVIOR_PROMPT_FIELD = SetupField(
    name="behavior_prompt",
    label="Behavior prompt",
    description="Instructions the instance follows in every turn. Leave it empty to keep "
    "kinby's default.",
    kind=SetupFieldKind.CONFIG,
    type=SetupFieldType.MULTILINE,
    required=False,
)


class PackageConfigError(ValueError):
    """An instance's package.yaml is missing, unreadable, or fails its package's validator."""


class PackageConfig(BaseModel):
    """The base of a package's config validator. A key it does not declare is an error."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _declared_secret(name: str, info: ValidationInfo) -> str:
    if not isinstance(info.context, frozenset) or name not in info.context:
        raise ValueError(f'"{name}" is not a required secret this package declares.')
    return name


SecretName = Annotated[str, AfterValidator(_declared_secret)]
"""A config field that names a required secret's environment variable, never its value."""


@dataclass(frozen=True)
class RequiredSecret:
    """One environment secret an instance package requires during setup."""

    name: str
    label: str
    description: str


@dataclass(frozen=True)
class Package:
    """The descriptor one installed distribution exports through ``kinby.packages``."""

    display_name: str
    description: str
    icon: str
    template: Path
    required_secrets: tuple[RequiredSecret, ...] = ()
    validate: Callable[[Path], None] | None = None
    config: type[PackageConfig] | None = None
    executables: tuple[str, ...] = ()


@dataclass(frozen=True)
class LoadedPackage:
    """What one ``kinby.packages`` entry point exports, and the distribution that installed it."""

    id: str
    package: Package
    distribution: Distribution


@dataclass(frozen=True)
class PackageDescriptor:
    """Authoritative metadata exported by an installed package."""

    id: str
    display_name: str
    description: str
    icon: str
    distribution: str
    version: str
    required_secrets: tuple[RequiredSecret, ...] = ()


@dataclass(frozen=True)
class InstalledPackage:
    """A descriptor and the editable template files read from one image."""

    descriptor: PackageDescriptor
    files: dict[str, str]


def load_package(package_id: str) -> LoadedPackage:
    """Load one installed package through its public entry point."""
    matches = [entry for entry in entry_points(group="kinby.packages") if entry.name == package_id]
    if not matches:
        raise LookupError(f'Package "{package_id}" is not installed.')
    if len(matches) > 1:
        raise LookupError(f'More than one installed package uses id "{package_id}".')
    entry = matches[0]
    exported = entry.load()
    if not isinstance(exported, Package):
        raise TypeError(f'Package "{package_id}" did not export a Package descriptor.')
    if entry.dist is None:
        raise LookupError(f'Package "{package_id}" has no distribution metadata.')
    return LoadedPackage(id=package_id, package=exported, distribution=entry.dist)


def installed_package(loaded: LoadedPackage) -> InstalledPackage:
    """Describe a loaded package and read its template, as a management process receives it."""
    exported = loaded.package
    return InstalledPackage(
        descriptor=PackageDescriptor(
            id=loaded.id,
            display_name=exported.display_name,
            description=exported.description,
            icon=exported.icon,
            distribution=loaded.distribution.name,
            version=loaded.distribution.version,
            required_secrets=exported.required_secrets,
        ),
        files=readable_template_files(exported.template),
    )


def inspect_installed_package(package_id: str) -> InstalledPackage:
    """Read one installed package and validate its template, including its package.yaml."""
    loaded = load_package(package_id)
    exported = loaded.package
    template = Path(exported.template).resolve()
    if not template.is_dir():
        raise ValueError(f'Package "{package_id}" template is not a directory: {template}')
    if exported.validate is not None:
        exported.validate(template)
    read_package_config(exported, template)
    return installed_package(loaded)


def read_package_config(package: Package, directory: Path) -> PackageConfig | None:
    """Read *directory*'s package.yaml afresh and validate it with the package's validator.

    A package without a validator takes no package.yaml. One with a validator
    requires the file: nothing falls back to defaults.
    """
    if package.config is None:
        return None
    path = directory / PACKAGE_CONFIG_NAME
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PackageConfigError(f"{path}: the package requires this file.") from None
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise PackageConfigError(f"{path}: {exc}") from exc
    declared = frozenset(secret.name for secret in package.required_secrets)
    try:
        return package.config.model_validate(raw, context=declared)
    except ValidationError as exc:
        raise PackageConfigError(f"{path}: {_validation_message(exc)}") from exc


def instance_package_config(instance: Instance) -> PackageConfig | None:
    """The instance's package.yaml, read now and validated by its package. None when vanilla."""
    provenance = instance.manifest.package
    if provenance is None:
        return None
    try:
        package = load_package(provenance.id).package
    except (LookupError, TypeError) as exc:
        raise PackageConfigError(f"{instance.path / PACKAGE_CONFIG_NAME}: {exc}") from exc
    return read_package_config(package, instance.path)


def _validation_message(exc: ValidationError) -> str:
    messages = []
    for error in exc.errors():
        message = error["msg"].removeprefix("Value error, ")
        location = ".".join(str(part) for part in error["loc"])
        messages.append(f"{location}: {message}" if location else message)
    return "; ".join(messages)


def vanilla_description() -> PackageDescription:
    """What kinby's base image declares: the kinby installed in it, and the built-in fields."""
    return PackageDescription(
        display_name="Vanilla",
        description="kinby's built-in defaults, with no package.",
        icon="sparkles",
        version=version("kinby"),
        setup_fields=[*BUILT_IN_FIELDS, BEHAVIOR_PROMPT_FIELD],
    )


def package_description(package: InstalledPackage) -> PackageDescription:
    """A package's card and version, with the built-in fields before the secrets it requires."""
    descriptor = package.descriptor
    required = [
        SetupField(
            name=secret.name,
            label=secret.label,
            description=secret.description,
            kind=SetupFieldKind.SECRET,
            type=SetupFieldType.TEXT,
            required=True,
        )
        for secret in descriptor.required_secrets
    ]
    return PackageDescription(
        display_name=descriptor.display_name,
        description=descriptor.description,
        icon=descriptor.icon,
        version=descriptor.version,
        setup_fields=[*BUILT_IN_FIELDS, *required],
    )


def installed_package_from_json(body: str) -> InstalledPackage:
    """Parse package inspection output at the Docker boundary."""
    raw = json.loads(body)
    descriptor = raw["descriptor"]
    return InstalledPackage(
        descriptor=PackageDescriptor(
            id=descriptor["id"],
            display_name=descriptor["display_name"],
            description=descriptor["description"],
            icon=descriptor["icon"],
            distribution=descriptor["distribution"],
            version=descriptor["version"],
            required_secrets=tuple(
                RequiredSecret(
                    name=secret["name"],
                    label=secret["label"],
                    description=secret["description"],
                )
                for secret in descriptor.get("required_secrets", [])
            ),
        ),
        files=dict(raw["files"]),
    )


def package_json(package: InstalledPackage) -> str:
    """Serialize package inspection output for a management process."""
    descriptor = package.descriptor
    return json.dumps(
        {
            "descriptor": {
                "id": descriptor.id,
                "display_name": descriptor.display_name,
                "description": descriptor.description,
                "icon": descriptor.icon,
                "distribution": descriptor.distribution,
                "version": descriptor.version,
                "required_secrets": [
                    {
                        "name": secret.name,
                        "label": secret.label,
                        "description": secret.description,
                    }
                    for secret in descriptor.required_secrets
                ],
            },
            "files": package.files,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def readable_template_files(template: Path) -> dict[str, str]:
    """Read the text files in an installed package template."""
    root = Path(template).resolve()
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


__all__ = [
    "PACKAGE_CONFIG_NAME",
    "InstalledPackage",
    "LoadedPackage",
    "Package",
    "PackageConfig",
    "PackageConfigError",
    "PackageDescriptor",
    "RequiredSecret",
    "SecretName",
    "inspect_installed_package",
    "installed_package",
    "installed_package_from_json",
    "instance_package_config",
    "load_package",
    "package_description",
    "package_json",
    "read_package_config",
    "readable_template_files",
    "vanilla_description",
]
