"""Command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import sys
from importlib.metadata import version
from pathlib import Path

from kinby.cli.client import ContractClient
from kinby.contracts import (
    ErrorEnvelope,
    Scope,
    ThreadCreateResult,
    ThreadListResult,
)
from kinby.core import build_dispatcher
from kinby.instance import (
    Instance,
    InstanceExistsError,
    InstanceNotFoundError,
    ManifestError,
    discover_instance,
    init_instance,
)


def _add_instance_selector(parser: argparse.ArgumentParser, help_text: str) -> None:
    parser.add_argument(
        "directory",
        nargs="?",
        help=help_text,
    )
    parser.add_argument(
        "--instance",
        dest="instance_directory",
        help=help_text,
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
    instructions = manifest.workspace.conventions.instructions
    skills = manifest.workspace.conventions.skills
    if instructions or skills:
        print("conventions:")
        if instructions:
            print("  instructions:")
            for path in instructions:
                print(f"    {path}")
        if skills:
            print("  skills:")
            for path in skills:
                print(f"    {path}")
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
    _add_instance_selector(show_parser, "instance directory to inspect")
    run_parser = subparsers.add_parser(
        "run",
        help="run an instance",
    )
    _add_instance_selector(run_parser, "instance directory to run")
    run_parser.add_argument(
        "--model",
        help="override [models].main for this session",
    )
    thread_parser = subparsers.add_parser(
        "thread",
        help="create and list threads",
    )
    thread_subparsers = thread_parser.add_subparsers(dest="thread_command")
    thread_create_parser = thread_subparsers.add_parser(
        "create",
        help="create a thread",
    )
    _add_instance_selector(thread_create_parser, "instance that owns the thread")
    thread_create_parser.add_argument("--title", help="optional thread title")
    thread_list_parser = thread_subparsers.add_parser(
        "list",
        help="list threads",
    )
    _add_instance_selector(thread_list_parser, "instance whose threads to list")
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
    show_instance = args.command == "instance" and args.instance_command == "show"
    thread_command = args.command == "thread" and args.thread_command in {"create", "list"}
    if show_instance or args.command == "run" or thread_command:
        try:
            explicit_directory = args.instance_directory or args.directory
            directory = Path(explicit_directory) if explicit_directory else None
            model_override = args.model if args.command == "run" else None
            instance = discover_instance(directory, model_override=model_override)
        except (InstanceNotFoundError, ManifestError) as exc:
            print(exc, file=sys.stderr)
            return 1
        if thread_command:
            dispatcher = build_dispatcher(instance.manifest.state_dir)
            client = ContractClient(dispatcher.dispatch, set(Scope))
            method = f"thread.{args.thread_command}"
            payload = {"title": args.title} if args.thread_command == "create" else {}
            result = asyncio.run(client.call(method, payload))
            if isinstance(result, ErrorEnvelope):
                print(f"{result.code.value}: {result.message}", file=sys.stderr)
                return 1
            if isinstance(result, ThreadCreateResult):
                print(f"id: {result.id}")
                print(f"created at: {result.created_at.isoformat()}")
                return 0
            if isinstance(result, ThreadListResult):
                for thread in result.threads:
                    print(f"{thread.id}\t{thread.created_at.isoformat()}\t{thread.title or ''}")
                return 0
            print("INTERNAL: The method returned an unexpected result.", file=sys.stderr)
            return 1
        _print_instance(instance)
        if args.command == "run":
            print("The agent loop is not yet available.")
            return 1
        return 0
    parser.print_help()
    return 0
