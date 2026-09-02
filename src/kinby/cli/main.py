"""Command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from importlib.metadata import version
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from kinby.cli.client import ContractClient, format_error
from kinby.cli.repl import run_repl
from kinby.contracts import (
    THREAD_CREATE,
    THREAD_LIST,
    USAGE_GET,
    ErrorCode,
    ErrorEnvelope,
    Scope,
    ThreadCreateCommand,
    ThreadListCommand,
    TokenTotals,
    TurnUsage,
    UsageGetCommand,
)
from kinby.core import assemble_system_prompt, build_dispatcher, turn_config
from kinby.core.events import EventLog
from kinby.instance import (
    PLACEHOLDER_MODEL,
    Instance,
    InstanceExistsError,
    InstanceNotFoundError,
    ManifestError,
    discover_instance,
    init_instance,
    load_instance,
)
from kinby.instance.recap import load_recap_lens
from kinby.plugins.core import core_tools
from kinby.plugins.registry import ToolRegistry
from kinby.plugins.skills import load_skills


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


def _print_turn_inputs(instance: Instance) -> None:
    skills, skill_warnings = load_skills(instance)
    sections = assemble_system_prompt(instance, skills, date.today())
    registry = ToolRegistry(instance.path, defaults=instance.manifest.tools.defaults)
    discovered_tools, tool_warnings = registry.refresh()
    tools, core_tool_warnings = discovered_tools.with_core(*core_tools(instance, skills))

    print("tools:")
    for tool in tools.tools:
        access = "write" if tool.write else "read"
        print(f"  {tool.name} ({access}): {tool.source}")
    print("skills:")
    for skill in skills:
        print(f"  {skill.name}: {skill.source}")
    print("prompt sections:")
    for section in sections:
        print(f"  {section.name}: {section.source} ({len(section.text)} characters)")
    recap_lens = load_recap_lens(instance.path)
    if recap_lens.uses_default:
        print(f"recap prompt: {recap_lens.path} (missing, using kinby default)")
    else:
        print(f"recap prompt: {recap_lens.path} ({len(recap_lens.text)} characters)")
    print("warnings:")
    for warning in (*tool_warnings, *core_tool_warnings, *skill_warnings):
        print(f"  {', '.join(warning.sources)}: {warning.message}")


def _token_totals(usage: TokenTotals) -> str:
    return f"input={usage.input_tokens} output={usage.output_tokens} total={usage.total}"


def _turn_token_totals(usage: TurnUsage) -> str:
    return (
        f"input={usage.input_tokens} output={usage.output_tokens} "
        f"recap_input={usage.recap_input_tokens} "
        f"recap_output={usage.recap_output_tokens} total={usage.total}"
    )


def _usage_command(args: argparse.Namespace) -> UsageGetCommand:
    return UsageGetCommand.model_validate({"since": args.since, "until": args.until})


def _load_selected_instance(
    args: argparse.Namespace,
    *,
    model_override: str | None = None,
) -> Instance:
    explicit_directory = args.instance_directory or args.directory
    if explicit_directory:
        return load_instance(Path(explicit_directory), model_override=model_override)
    return discover_instance(model_override=model_override)


def _contract_client(instance: Instance) -> ContractClient:
    dispatcher = build_dispatcher(instance.manifest.state_dir)
    return ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))


async def _show_usage(client: ContractClient, command: UsageGetCommand) -> int:
    result = await client.call(USAGE_GET, command)
    if isinstance(result, ErrorEnvelope):
        print(format_error(result), file=sys.stderr)
        return 1
    for thread in result.threads:
        print(f"thread {thread.thread_id}: {_token_totals(thread)}")
        for turn in thread.turns:
            print(f"  turn {turn.turn_id}: {_turn_token_totals(turn)}")
    return 0


async def _run_instance(
    instance: Instance,
    *,
    model_override: str | None = None,
    thread_id: UUID | None = None,
) -> int:
    event_log = EventLog(instance.manifest.state_dir)
    turns = turn_config(instance, event_log=event_log, model_override=model_override)
    if turns.recap is not None:
        await turns.recap.catch_up()
    dispatcher = build_dispatcher(
        instance.manifest.state_dir,
        event_log=event_log,
        turns=turns,
    )
    client = ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope))
    opened = await _thread_for_session(client, thread_id)
    if isinstance(opened, ErrorEnvelope):
        print(format_error(opened), file=sys.stderr)
        return 1
    return await run_repl(
        client,
        opened,
        feedback=instance.manifest.feedback.ask,
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


async def _thread_for_session(
    client: ContractClient,
    thread_id: UUID | None,
) -> UUID | ErrorEnvelope:
    if thread_id is None:
        created = await client.call(THREAD_CREATE, ThreadCreateCommand())
        if isinstance(created, ErrorEnvelope):
            return created
        return created.id
    listed = await client.call(THREAD_LIST, ThreadListCommand())
    if isinstance(listed, ErrorEnvelope):
        return listed
    if thread_id not in {thread.id for thread in listed.threads}:
        return ErrorEnvelope(
            code=ErrorCode.NOT_FOUND,
            message=f'Thread "{thread_id}" was not found.',
            retryable=False,
        )
    return thread_id


async def _create_thread(client: ContractClient, title: str | None) -> int:
    created = await client.call(THREAD_CREATE, ThreadCreateCommand(title=title))
    if isinstance(created, ErrorEnvelope):
        print(format_error(created), file=sys.stderr)
        return 1
    print(f"id: {created.id}")
    print(f"created at: {created.created_at.isoformat()}")
    return 0


async def _list_threads(client: ContractClient) -> int:
    listed = await client.call(THREAD_LIST, ThreadListCommand())
    if isinstance(listed, ErrorEnvelope):
        print(format_error(listed), file=sys.stderr)
        return 1
    for thread in listed.threads:
        print(f"{thread.id}\t{thread.created_at.isoformat()}\t{thread.title or ''}")
    return 0


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
        default=PLACEHOLDER_MODEL,
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
    run_parser.add_argument(
        "--thread",
        help="resume this thread instead of creating one",
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
    usage_parser = subparsers.add_parser(
        "usage",
        help="show token totals",
    )
    _add_instance_selector(usage_parser, "instance whose usage to show")
    usage_parser.add_argument(
        "--since",
        help="include events at or after this ISO 8601 time with a timezone",
    )
    usage_parser.add_argument(
        "--until",
        help="include events at or before this ISO 8601 time with a timezone",
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
    try:
        match args.command:
            case "instance" if args.instance_command == "show":
                instance = _load_selected_instance(args)
                _print_instance(instance)
                _print_turn_inputs(instance)
                return 0
            case "run":
                instance = _load_selected_instance(args, model_override=args.model)
                _print_instance(instance)
                try:
                    thread_id = UUID(args.thread) if args.thread else None
                except ValueError:
                    print(
                        "--thread must be a thread id from kinby thread list",
                        file=sys.stderr,
                    )
                    return 1
                return asyncio.run(
                    _run_instance(
                        instance,
                        model_override=args.model,
                        thread_id=thread_id,
                    )
                )
            case "thread" if args.thread_command in {"create", "list"}:
                client = _contract_client(_load_selected_instance(args))
                if args.thread_command == "create":
                    return asyncio.run(_create_thread(client, args.title))
                return asyncio.run(_list_threads(client))
            case "usage":
                try:
                    command = _usage_command(args)
                except ValidationError:
                    print(
                        "--since and --until must be ISO 8601 times with a timezone, "
                        "for example 2026-08-28T12:00:00Z",
                        file=sys.stderr,
                    )
                    return 1
                client = _contract_client(_load_selected_instance(args))
                return asyncio.run(_show_usage(client, command))
    except (InstanceNotFoundError, ManifestError) as exc:
        print(exc, file=sys.stderr)
        return 1
    parser.print_help()
    return 0
