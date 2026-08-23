"""Command-line interface."""

from __future__ import annotations

import argparse
import sys
from importlib.metadata import version
from pathlib import Path

from kinby.instance import (
    Instance,
    InstanceExistsError,
    InstanceNotFoundError,
    ManifestError,
    discover_instance,
    init_instance,
)


def _print_instance(instance: Instance) -> None:
    manifest = instance.manifest
    print(f"id: {manifest.id}")
    if manifest.persona_name is not None:
        print(f"persona name: {manifest.persona_name}")
    print(f"path: {instance.path}")
    print(f"matching rule: {instance.matching_rule}")
    print("models:")
    print(f"  main: {manifest.models.main}")
    print(f"  recap: {manifest.models.recap}")
    print(f"  embed: {manifest.models.embed or 'not configured'}")
    workspace_status = "present" if manifest.workspace.path.exists() else "missing"
    print(f"workspace: {manifest.workspace.path} ({workspace_status})")
    if manifest.workspace.source is not None:
        print(f"  source: {manifest.workspace.source}")
    print(f"state dir: {manifest.state_dir}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kinby")
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the kinby version and exit",
    )
    subparsers = parser.add_subparsers(dest="command")
    init_parser = subparsers.add_parser(
        "init",
        help="create a readable starter instance",
    )
    init_parser.add_argument("directory", help="instance directory to create")
    init_parser.add_argument(
        "--model",
        help="value for [models].main (a placeholder is used when omitted)",
    )
    instance_parser = subparsers.add_parser(
        "instance",
        help="inspect an instance",
    )
    instance_subparsers = instance_parser.add_subparsers(dest="instance_command")
    show_parser = instance_subparsers.add_parser(
        "show",
        help="show the resolved instance settings",
    )
    show_parser.add_argument(
        "directory",
        nargs="?",
        help="instance directory to inspect",
    )
    show_parser.add_argument(
        "--instance",
        dest="instance_directory",
        help="instance directory to inspect",
    )
    args = parser.parse_args(argv)
    if args.version:
        print(f"kinby {version('kinby')}")
        return 0
    if args.command == "init":
        try:
            path = init_instance(Path(args.directory), model=args.model)
        except InstanceExistsError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"Created instance at {path}")
        return 0
    if args.command == "instance" and args.instance_command == "show":
        try:
            explicit_directory = args.instance_directory or args.directory
            directory = Path(explicit_directory) if explicit_directory else None
            instance = discover_instance(directory)
        except (InstanceNotFoundError, ManifestError) as exc:
            print(exc, file=sys.stderr)
            return 1
        _print_instance(instance)
        return 0
    parser.print_help()
    return 0
