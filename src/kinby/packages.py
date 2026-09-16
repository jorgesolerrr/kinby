"""Package descriptors and instance templates read from prepared images."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path


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


def inspect_installed_package(package_id: str) -> InstalledPackage:
    """Read one installed package through its public entry point."""
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
    template = Path(exported.template).resolve()
    if not template.is_dir():
        raise ValueError(f'Package "{package_id}" template is not a directory: {template}')
    if exported.validate is not None:
        exported.validate(template)
    return InstalledPackage(
        descriptor=PackageDescriptor(
            id=package_id,
            display_name=exported.display_name,
            description=exported.description,
            icon=exported.icon,
            distribution=entry.dist.name,
            version=entry.dist.version,
            required_secrets=exported.required_secrets,
        ),
        files=readable_template_files(template),
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


def main(argv: list[str] | None = None) -> int:
    """Write one installed descriptor and template as JSON for the hub."""
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("usage: python -m kinby.packages <package-id>", file=sys.stderr)
        return 2
    print(package_json(inspect_installed_package(arguments[0])))
    return 0


__all__ = [
    "InstalledPackage",
    "Package",
    "PackageDescriptor",
    "RequiredSecret",
    "inspect_installed_package",
    "installed_package_from_json",
    "package_json",
    "readable_template_files",
]


if __name__ == "__main__":
    raise SystemExit(main())
