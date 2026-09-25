"""Command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shlex
import signal
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import date
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import ValidationError

from kinby.cli.client import ContractClient, format_error
from kinby.cli.contract_socket import TOKEN_VARIABLE, InsecureContractUrl, contract_client
from kinby.cli.hub_adopt import adopt_on_hub
from kinby.cli.hub_update import update_on_hub
from kinby.cli.repl import render_event, run_repl
from kinby.cli.routines import show_routines
from kinby.contracts import (
    INSTANCE_SCOPES,
    ROUTINE_RUN,
    STATS_GET,
    THREAD_CREATE,
    THREAD_LIST,
    THREAD_SUBSCRIBE,
    USAGE_GET,
    AccessToken,
    ApprovalRequested,
    ControlToken,
    ErrorCode,
    ErrorEnvelope,
    ModelCallMismatch,
    ReportedRun,
    RoutineName,
    RoutinePayload,
    RoutineRunCommand,
    StatsBucketSize,
    StatsGetCommand,
    StatsSummary,
    ThreadCreateCommand,
    ThreadListCommand,
    ThreadSubscribeCommand,
    TokenTotals,
    TurnFailed,
    TurnUsage,
    UsageGetCommand,
    is_turn_closing,
)
from kinby.core import Dispatcher, assemble_system_prompt, boot_instance, build_dispatcher
from kinby.core.clock import utc_today
from kinby.core.contract_server import ContractServer
from kinby.core.receiver import Receiver
from kinby.core.runtime_lock import InstanceBusyError, runtime_lock
from kinby.instance import (
    PLACEHOLDER_MODEL,
    FeedbackPolicy,
    Instance,
    InstanceExistsError,
    InstanceNotFoundError,
    ManifestError,
    Serve,
    discover_instance,
    init_instance,
    load_instance,
    parse_listen,
)
from kinby.instance.recap import load_recap_lens
from kinby.packages import PackageConfigError, inspect_installed_package
from kinby.packages.check import check_package
from kinby.plugins.core import core_tools
from kinby.plugins.registry import ToolRegistry
from kinby.plugins.skills import load_skills

if TYPE_CHECKING:
    # The hub pulls in the Docker SDK; only the hub command imports it at runtime.
    from kinby.hub import LifecycleRecovery


class _CurrentStderr:
    def write(self, message: str) -> int:
        return sys.stderr.write(message)

    def flush(self) -> None:
        sys.stderr.flush()


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
    if manifest.package is not None:
        print("package:")
        print(f"  id: {manifest.package.id}")
        print(f"  distribution: {manifest.package.distribution}")
        print(f"  template version: {manifest.package.version}")


def _print_turn_inputs(instance: Instance, today: date) -> None:
    skills, skill_warnings = load_skills(instance)
    sections = assemble_system_prompt(instance, skills, today)
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
        f"cache_read={usage.cache_read_tokens} "
        f"cache_creation={usage.cache_creation_tokens} "
        f"recap_input={usage.recap_input_tokens} "
        f"recap_output={usage.recap_output_tokens} total={usage.total}"
    )


def _delegated_run(reported: ReportedRun) -> str:
    run = reported.run
    line = (
        f"delegated run {reported.timestamp.isoformat()}: source={run.usage_source} "
        f"client={run.client} models={','.join(run.models)} outcome={run.outcome} "
        f"input={run.input_tokens} output={run.output_tokens} "
        f"cache_read={run.cache_read_tokens} cache_creation={run.cache_creation_tokens} "
        f"total={run.total} duration_ms={run.duration_ms} client_turns={run.client_turns}"
    )
    if run.resets_at is None:
        return line
    return f"{line} resets_at={run.resets_at.isoformat()}"


def _usage_command(args: argparse.Namespace) -> UsageGetCommand:
    return UsageGetCommand.model_validate({"since": args.since, "until": args.until})


def _stats_command(args: argparse.Namespace) -> StatsGetCommand:
    return StatsGetCommand.model_validate({"since": args.since, "until": args.until, "by": args.by})


def _range_command[Command](build: Callable[[], Command]) -> Command | None:
    try:
        return build()
    except ValidationError:
        print(
            "--since and --until must be ISO 8601 times with a timezone, "
            "for example 2026-08-28T12:00:00Z",
            file=sys.stderr,
        )
        return None


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
    dispatcher = build_dispatcher(
        instance.manifest.state_dir,
        price_overrides=instance.manifest.prices,
    )
    return _contract_client_for(dispatcher)


def _contract_client_for(dispatcher: Dispatcher) -> ContractClient:
    return ContractClient(dispatcher.dispatch, dispatcher.subscribe, INSTANCE_SCOPES)


async def _show_usage(client: ContractClient, command: UsageGetCommand) -> int:
    result = await client.call(USAGE_GET, command)
    if isinstance(result, ErrorEnvelope):
        print(format_error(result), file=sys.stderr)
        return 1
    for thread in result.threads:
        print(f"thread {thread.thread_id}: {_token_totals(thread)}")
        for turn in thread.turns:
            print(f"  turn {turn.turn_id}: {_turn_token_totals(turn)}")
            for reported in turn.delegated_runs:
                print(f"    {_delegated_run(reported)}")
    return 0


def _tool_call_counts(counts: dict[str, int]) -> str:
    return ",".join(f"{name}={counts[name]}" for name in sorted(counts))


def _stats_row(label: str, summary: StatsSummary) -> str:
    memory = summary.memory_calls
    return "\t".join(
        (
            label,
            str(summary.completed),
            str(summary.failed),
            str(summary.interrupted),
            str(summary.input_tokens),
            str(summary.output_tokens),
            str(summary.cache_read_tokens),
            str(summary.cache_creation_tokens),
            str(summary.recap_input_tokens),
            str(summary.recap_output_tokens),
            f"{summary.cost:.8f}" if summary.cost is not None else "unknown",
            _tool_call_counts(summary.tool_calls),
            str(memory.search),
            str(memory.open),
            str(memory.remember),
            str(memory.forget),
            str(summary.turns_without_memory),
            str(summary.approvals_requested),
            str(summary.denies.policy),
            str(summary.denies.user),
            str(summary.tool_duration.read_ms),
            str(summary.tool_duration.write_ms),
            _optional_mean(summary.mean_duration_seconds),
            str(summary.good_ratings),
            str(summary.bad_ratings),
            str(summary.navigation.turns),
            _optional_mean(summary.navigation.read_calls),
            _optional_mean(summary.navigation.duration_ms),
            _optional_mean(summary.navigation.tokens_before_first_write),
            _optional_mean(summary.navigation.repeat_opens),
            *(
                str(value)
                for use in summary.subscriptions
                for value in (
                    use.runs,
                    use.input_tokens,
                    use.output_tokens,
                    use.cache_read_tokens,
                    use.cache_creation_tokens,
                    use.duration_ms,
                )
            ),
        )
    )


def _optional_mean(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "unknown"


def _model_call_mismatch(mismatch: ModelCallMismatch) -> str:
    return (
        f"warning: Model call totals for turn {mismatch.turn_id} "
        f"on thread {mismatch.thread_id} do not match its closing totals."
    )


async def _show_stats(
    client: ContractClient,
    command: StatsGetCommand,
    state_dir: Path,
) -> int:
    result = await client.call(STATS_GET, command)
    if isinstance(result, ErrorEnvelope):
        print(format_error(result), file=sys.stderr)
        return 1
    print(
        "\t".join(
            (
                "bucket",
                "completed",
                "failed",
                "interrupted",
                "input",
                "output",
                "cache read",
                "cache creation",
                "recap input",
                "recap output",
                "cost",
                "tool calls",
                "memory search",
                "memory open",
                "remember",
                "forget",
                "without memory",
                "approvals",
                "policy denies",
                "user denies",
                "read tool ms",
                "write tool ms",
                "mean seconds",
                "good",
                "bad",
                "nav turns",
                "nav reads",
                "nav ms",
                "nav tokens",
                "nav repeats",
                *(
                    f"{use.usage_source} {column}"
                    for use in result.total.subscriptions
                    for column in (
                        "runs",
                        "input",
                        "output",
                        "cache read",
                        "cache creation",
                        "ms",
                    )
                ),
            )
        )
    )
    if result.unpriced_models:
        print(
            f"warning: unpriced models: {', '.join(result.unpriced_models)}",
            file=sys.stderr,
        )
    for mismatch in result.warnings:
        print(_model_call_mismatch(mismatch), file=sys.stderr)
    for bucket in result.buckets:
        print(_stats_row(bucket.start.isoformat(), bucket))
    print(_stats_row("total", result.total))
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "stats.json").write_text(
        f"{result.model_dump_json(indent=2)}\n",
        encoding="utf-8",
    )
    return 0


def _check_package(package_id: str) -> int:
    failures = check_package(package_id)
    for failure in failures:
        print(failure, file=sys.stderr)
    if failures:
        return 1
    print(f'Package "{package_id}" passed the check.')
    return 0


@asynccontextmanager
async def _instance_session(
    instance: Instance, model_override: str | None = None
) -> AsyncIterator[ContractClient]:
    runtime = await boot_instance(instance, model_override=model_override)
    client = _contract_client_for(runtime.dispatcher)
    try:
        yield client
    finally:
        await runtime.stop_after_running_routine()


def _repl_on_server(url: str, thread_id: UUID | None) -> int:
    token = os.environ.get(TOKEN_VARIABLE)
    if not token:
        print(
            f"Set {TOKEN_VARIABLE} to the token the contract server accepts.",
            file=sys.stderr,
        )
        return 1
    return asyncio.run(_run_on_server(url, ControlToken(token), thread_id))


async def _run_on_server(url: str, token: ControlToken, thread_id: UUID | None) -> int:
    """Open the REPL on a contract server, where no manifest says how often to ask for a rating."""
    async with contract_client(url, token) as client:
        opened = await _thread_for_session(client, thread_id)
        if isinstance(opened, ErrorEnvelope):
            print(format_error(opened), file=sys.stderr)
            return 1
        return await run_repl(
            client,
            opened,
            feedback=FeedbackPolicy.EVERY_TURN,
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )


async def _run_instance(
    instance: Instance,
    *,
    model_override: str | None = None,
    thread_id: UUID | None = None,
) -> int:
    async with _instance_session(instance, model_override) as client:
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


async def _serve_instance(instance: Instance) -> int:
    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()
    shutdown_signals = (signal.SIGINT, signal.SIGTERM)
    for shutdown_signal in shutdown_signals:
        loop.add_signal_handler(shutdown_signal, stopping.set)
    runtime = None
    receiver = None
    try:
        runtime = await boot_instance(instance)
        if instance.manifest.serve is not None:
            receiver = Receiver(
                instance.manifest.serve,
                runtime.scheduler,
                instance,
                ContractServer.from_environment(runtime.dispatcher),
            )
            address = await receiver.start()
            print(f"listen: {address.host}:{address.port}", flush=True)
        client = _contract_client_for(runtime.dispatcher)
        status = await show_routines(client, sys.stdout, sys.stderr)
        if status:
            return status
        await stopping.wait()
        return 0
    finally:
        # The runtime first: a turn it is finishing may still need an approval response,
        # and the hub may be waiting on the drain it asked for over the same server.
        if runtime is not None:
            await runtime.stop_interrupting_running_routine()
        if receiver is not None:
            await receiver.stop()
        for shutdown_signal in shutdown_signals:
            loop.remove_signal_handler(shutdown_signal)


async def _run_hub(
    directory: Path,
    source_directory: Path,
    docker_host_directory: Path,
    network: str,
    listen: Serve,
    web_app: Path | None,
) -> int:
    from docker.errors import DockerException

    from kinby.hub import HubContractServer, build_docker_hub
    from kinby.hub.service import HubAlreadyRunning

    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()
    shutdown_signals = (signal.SIGINT, signal.SIGTERM)
    for shutdown_signal in shutdown_signals:
        loop.add_signal_handler(shutdown_signal, stopping.set)
    server = None
    try:
        try:
            hub = await asyncio.to_thread(
                build_docker_hub,
                directory,
                source_directory,
                docker_host_directory,
                network=network,
            )
        except (DockerException, HubAlreadyRunning) as exc:
            print(f"Unable to start the Docker-backed hub: {exc}", file=sys.stderr)
            return 1
        print(f"hub id: {hub.registry.hub_id()}")
        print(f"directory: {hub.directory}")
        _announce(hub.access.issue())
        _announce_recovery(await hub.recover())
        server = HubContractServer(hub.dispatcher, hub.access, hub, web_app)
        address = await server.start(listen)
        print(f"listen: {address.host}:{address.port}")
        await stopping.wait()
        return 0
    finally:
        if server is not None:
            await server.stop()
        for shutdown_signal in shutdown_signals:
            loop.remove_signal_handler(shutdown_signal)


def _announce_recovery(recovery: LifecycleRecovery) -> None:
    """Print what this hub found for each managed instance, and what it does not own."""
    for instance in recovery.instances:
        print(f"recovered: {instance.instance_id} {instance.state.value}: {instance.detail}")
    for runtime_id in recovery.unknown_containers:
        print(f"unowned container: {runtime_id}")


def _announce(token: AccessToken | None) -> None:
    """Print a newly generated access token once. A hub that already has one prints nothing."""
    if token is None:
        return
    print("This hub's access token is shown once. Store it now:")
    # A container's stdout is a pipe, which Python buffers until the hub exits.
    print(f"access token: {token}", flush=True)


def _set_signal_alias(directory: Path, instance_id: str) -> int:
    """Adoption keeps a webhook URL working by claiming the hub's public signal path."""
    from kinby.hub import HubRegistry

    try:
        HubRegistry(directory).set_signal_alias(UUID(instance_id))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"signals: /signals/<routine> now reaches {instance_id}")
    return 0


def _rotate_access_token(directory: Path) -> int:
    from kinby.hub import HubAccess, HubRegistry

    print("The previous access token and its sessions are now closed:")
    print(f"access token: {HubAccess(HubRegistry(directory)).rotate()}")
    return 0


def _rotate_update_token(directory: Path) -> int:
    from kinby.hub import HubAccess, HubRegistry

    print("This token can only run instance updates. Any previous update token is now refused:")
    print(f"update token: {HubAccess(HubRegistry(directory)).rotate_update_token()}")
    return 0


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


async def _list_routines(instance: Instance) -> int:
    async with _instance_session(instance) as client:
        return await show_routines(client, sys.stdout, sys.stderr)


async def _run_routine(
    instance: Instance,
    name: RoutineName,
    payload_path: Path | None,
) -> int:
    try:
        payload = _read_routine_payload(payload_path) if payload_path is not None else None
    except (OSError, UnicodeDecodeError) as exc:
        print(f'Could not read payload "{payload_path}": {exc}', file=sys.stderr)
        return 1
    async with _instance_session(instance) as client:
        return await _run_routine_command(client, name, instance.path, payload)


async def _run_routine_command(
    client: ContractClient,
    name: RoutineName,
    instance_path: Path,
    payload: RoutinePayload | None,
) -> int:
    accepted = await client.call(ROUTINE_RUN, RoutineRunCommand(name=name, payload=payload))
    if isinstance(accepted, ErrorEnvelope):
        print(format_error(accepted), file=sys.stderr)
        return 1
    print(f"thread: {accepted.thread_id}")
    stream = await client.subscribe(
        THREAD_SUBSCRIBE, ThreadSubscribeCommand(thread_id=accepted.thread_id)
    )
    if isinstance(stream, ErrorEnvelope):
        print(format_error(stream), file=sys.stderr)
        return 1
    status = 0
    try:
        async for event in stream.items:
            if isinstance(event, ErrorEnvelope):
                print(format_error(event), file=sys.stderr)
                status = 1
                break
            if isinstance(event.payload, ApprovalRequested):
                print("Routine parked, waiting for approval.", file=sys.stderr)
                command = shlex.join(
                    (
                        "kinby",
                        "repl",
                        "--thread",
                        str(accepted.thread_id),
                        "--instance",
                        str(instance_path),
                    )
                )
                print(f"Resume with: {command}")
                status = 1
                break
            render_event(event, sys.stdout, sys.stderr)
            if isinstance(event.payload, TurnFailed):
                status = 1
            if is_turn_closing(event.payload):
                break
    finally:
        await stream.items.aclose()
    return status


def _read_routine_payload(path: Path) -> RoutinePayload:
    body = path.read_text(encoding="utf-8")
    try:
        json.loads(body)
    except json.JSONDecodeError:
        content_type = "text/plain"
    else:
        content_type = "application/json"
    return RoutinePayload(body=body, content_type=content_type)


def main(
    argv: list[str] | None = None,
    *,
    today: Callable[[], date] = utc_today,
) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    # `hub update` and `hub adopt` drive a hub over the network and take no hub directory.
    # argparse cannot let a subcommand stand where `hub` expects that directory, so each
    # parses alone.
    if arguments[:2] == ["hub", "update"]:
        return update_on_hub(arguments[2:])
    if arguments[:2] == ["hub", "adopt"]:
        return adopt_on_hub(arguments[2:])
    parser = argparse.ArgumentParser(prog="kinby")
    parser.set_defaults(verbose=False)
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
    init_parser.add_argument(
        "--package",
        dest="package_id",
        help="installed package whose template should seed the instance",
    )
    hub_parser = subparsers.add_parser(
        "hub",
        help="run the instance management hub",
        epilog=(
            "kinby hub update --help: update one instance on a running hub, as CI does. "
            "kinby hub adopt --help: preview or adopt an existing instance."
        ),
    )
    hub_parser.add_argument("directory", type=Path, help="hub state directory")
    hub_parser.add_argument(
        "--source",
        type=Path,
        default=Path.cwd(),
        help="kinby Git repository used to build images",
    )
    hub_parser.add_argument(
        "--docker-host-directory",
        type=Path,
        help="hub directory path as seen by the Docker host",
    )
    hub_parser.add_argument(
        "--network",
        default="kinby_private",
        help="private Docker network shared with the hub",
    )
    hub_parser.add_argument(
        "--listen",
        default="0.0.0.0:8080",
        help="host:port the hub's contract server listens on",
    )
    hub_parser.add_argument(
        "--web-app",
        type=Path,
        help="directory holding the built web app (default: the one the hub image carries)",
    )
    hub_subparsers = hub_parser.add_subparsers(dest="hub_command")
    hub_token_parser = hub_subparsers.add_parser(
        "token",
        help="manage the hub access token",
    )
    hub_token_parser.add_subparsers(dest="token_command", required=True).add_parser(
        "rotate",
        help="replace the access token and end open sessions",
    )
    hub_update_token_parser = hub_subparsers.add_parser(
        "update-token",
        help="manage the token that can only run instance updates",
    )
    hub_update_token_parser.add_subparsers(dest="token_command", required=True).add_parser(
        "rotate",
        help="issue the update token, refusing any previous one",
    )
    hub_signals_parser = hub_subparsers.add_parser(
        "signals",
        help="point the public /signals path at one managed instance",
    )
    hub_signals_parser.add_argument(
        "instance_id",
        help="hub instance id that keeps the established webhook URL",
    )
    package_parser = subparsers.add_parser("package", help="check installed packages")
    package_subparsers = package_parser.add_subparsers(dest="package_command")
    package_check_parser = package_subparsers.add_parser(
        "check",
        help="run kinby's install path against an installed package",
    )
    package_check_parser.add_argument("package", metavar="id", help="installed package id")
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
    repl_parser = subparsers.add_parser(
        "repl",
        help="open a REPL on an instance",
    )
    _add_instance_selector(repl_parser, "instance directory to open")
    repl_parser.add_argument(
        "--connect",
        help="contract server URL to drive instead of a local instance",
    )
    repl_parser.add_argument(
        "--model",
        help="override [models].main for this session",
    )
    repl_parser.add_argument(
        "--thread",
        help="resume this thread instead of creating one",
    )
    repl_parser.add_argument(
        "--verbose",
        action="store_true",
        help="show debug logs",
    )
    serve_parser = subparsers.add_parser(
        "serve",
        help="run scheduled routines without a REPL",
    )
    _add_instance_selector(serve_parser, "instance to serve")
    serve_parser.add_argument(
        "--verbose",
        action="store_true",
        help="show debug logs",
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
    routine_parser = subparsers.add_parser("routine", help="list and run routines")
    routine_subparsers = routine_parser.add_subparsers(dest="routine_command")
    routine_list = routine_subparsers.add_parser("list", help="show routine schedules and history")
    _add_instance_selector(routine_list, "instance whose routines to list")
    routine_run = routine_subparsers.add_parser("run", help="run a routine manually")
    routine_run.add_argument("name", help="routine name")
    routine_run.add_argument("--payload", type=Path, help="delivery payload file")
    _add_instance_selector(routine_run, "instance that owns the routine")
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
    stats_parser = subparsers.add_parser(
        "stats",
        help="show instance statistics",
    )
    _add_instance_selector(stats_parser, "instance whose statistics to show")
    stats_parser.add_argument(
        "--since",
        help="include turns closed at or after this ISO 8601 time with a timezone",
    )
    stats_parser.add_argument(
        "--until",
        help="include turns closed at or before this ISO 8601 time with a timezone",
    )
    stats_parser.add_argument(
        "--by",
        choices=[size.value for size in StatsBucketSize],
        default="day",
        help="group turns by UTC day or Monday-starting week",
    )
    args = parser.parse_args(arguments)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
        stream=_CurrentStderr(),
        force=True,
    )
    if args.version:
        print(f"kinby {version('kinby')}")
        return 0
    if args.command == "init":
        try:
            package = (
                inspect_installed_package(args.package_id) if args.package_id is not None else None
            )
            path = init_instance(Path(args.directory), model=args.model, package=package)
        except (InstanceExistsError, LookupError, TypeError, ValueError) as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"Created instance at {path}")
        return 0
    if args.command == "package" and args.package_command == "check":
        return _check_package(args.package)
    if args.command == "hub":
        if args.hub_command == "token":
            return _rotate_access_token(args.directory)
        if args.hub_command == "update-token":
            return _rotate_update_token(args.directory)
        if args.hub_command == "signals":
            return _set_signal_alias(args.directory, args.instance_id)
        try:
            listen = parse_listen(args.listen)
        except ValueError as exc:
            print(f"--listen {exc}", file=sys.stderr)
            return 1
        host_directory = args.docker_host_directory or args.directory
        return asyncio.run(
            _run_hub(
                args.directory,
                args.source,
                host_directory,
                args.network,
                listen,
                args.web_app,
            )
        )
    try:
        match args.command:
            case "instance" if args.instance_command == "show":
                instance = _load_selected_instance(args)
                _print_instance(instance)
                _print_turn_inputs(instance, today())
                return 0
            case "repl" if args.connect and (args.directory or args.instance_directory):
                print(
                    "--connect opens a REPL on a contract server, not on an instance directory",
                    file=sys.stderr,
                )
                return 1
            case "repl":
                try:
                    thread_id = UUID(args.thread) if args.thread else None
                except ValueError:
                    print(
                        "--thread must be a thread id from kinby thread list",
                        file=sys.stderr,
                    )
                    return 1
                if args.connect:
                    return _repl_on_server(args.connect, thread_id)
                instance = _load_selected_instance(args, model_override=args.model)
                _print_instance(instance)
                with runtime_lock(instance.manifest.state_dir):
                    return asyncio.run(
                        _run_instance(
                            instance,
                            model_override=args.model,
                            thread_id=thread_id,
                        )
                    )
            case "serve":
                instance = _load_selected_instance(args)
                _print_instance(instance)
                with runtime_lock(instance.manifest.state_dir):
                    return asyncio.run(_serve_instance(instance))
            case "thread" if args.thread_command in {"create", "list"}:
                client = _contract_client(_load_selected_instance(args))
                if args.thread_command == "create":
                    return asyncio.run(_create_thread(client, args.title))
                return asyncio.run(_list_threads(client))
            case "routine" if args.routine_command in {"list", "run"}:
                instance = _load_selected_instance(args)
                if args.routine_command == "run":
                    return asyncio.run(_run_routine(instance, RoutineName(args.name), args.payload))
                return asyncio.run(_list_routines(instance))
            case "usage":
                command = _range_command(lambda: _usage_command(args))
                if command is None:
                    return 1
                client = _contract_client(_load_selected_instance(args))
                return asyncio.run(_show_usage(client, command))
            case "stats":
                command = _range_command(lambda: _stats_command(args))
                if command is None:
                    return 1
                instance = _load_selected_instance(args)
                client = _contract_client(instance)
                return asyncio.run(_show_stats(client, command, instance.manifest.state_dir))
    except (
        InstanceNotFoundError,
        InstanceBusyError,
        InsecureContractUrl,
        ManifestError,
        PackageConfigError,
    ) as exc:
        print(exc, file=sys.stderr)
        return 1
    parser.print_help()
    return 0
