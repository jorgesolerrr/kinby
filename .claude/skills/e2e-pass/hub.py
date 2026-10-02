"""The hub calls an e2e pass makes, run inside the playground's hub container by `hub.sh`.

    hub.py instances                           list the active instances
    hub.py update <revision> [<instance-id>]   update instances not yet on that revision
    hub.py secrets <instance-id>               NAME=value lines on stdin, then recreate

It talks to the hub on loopback with the access token in KINBY_TOKEN, and never prints
a secret value.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID

from pydantic import SecretStr

from kinby.cli.client import ContractClient, format_error
from kinby.cli.contract_socket import TOKEN_VARIABLE, contract_client
from kinby.cli.hub_update import follow_operation
from kinby.contracts import (
    INSTANCE_LIST,
    INSTANCE_RECREATE,
    INSTANCE_SECRETS_SET,
    INSTANCE_UPDATE,
    AccessToken,
    ContractModel,
    ErrorEnvelope,
    InstanceListCommand,
    InstanceRecreateCommand,
    InstanceSecretsSetCommand,
    InstanceSummary,
    InstanceUpdateCommand,
    LifecycleOperationResult,
    Method,
    OperationGetCommand,
)

HUB_URL = "ws://localhost:8080/ws"


async def _operate[Command: ContractModel](
    client: ContractClient,
    method: Method[Command, LifecycleOperationResult],
    command: Command,
) -> int:
    """Start one lifecycle operation and follow it to its end. Only success returns zero."""
    accepted = await client.call(method, command)
    if isinstance(accepted, ErrorEnvelope):
        print(format_error(accepted), file=sys.stderr)
        return 1
    return await follow_operation(client, OperationGetCommand(operation_id=accepted.operation_id))


async def _instances(client: ContractClient) -> list[InstanceSummary]:
    listed = await client.call(INSTANCE_LIST, InstanceListCommand())
    if isinstance(listed, ErrorEnvelope):
        raise SystemExit(format_error(listed))
    return listed.instances


async def show_instances(client: ContractClient) -> int:
    for instance in await _instances(client):
        print(
            instance.instance_id,
            instance.persona_name or instance.manifest_id,
            instance.intended_state.value,
            instance.source_revision[:12],
        )
    return 0


async def update(client: ContractClient, revision: str, chosen: list[UUID]) -> int:
    """Update each chosen instance, or every active one, that runs another revision."""
    failures = 0
    instances = await _instances(client)
    for unknown in set(chosen) - {instance.instance_id for instance in instances}:
        print(f"{unknown}: no active instance has this id", file=sys.stderr)
        failures += 1
    for instance in instances:
        name = instance.persona_name or instance.manifest_id
        if chosen and instance.instance_id not in chosen:
            continue
        if instance.source_revision == revision:
            print(f"{instance.instance_id} {name}: already on {revision[:12]}")
            continue
        print(f"{instance.instance_id} {name}: updating to {revision[:12]}", flush=True)
        command = InstanceUpdateCommand(instance_id=instance.instance_id, revision=revision)
        failures += await _operate(client, INSTANCE_UPDATE, command)
    return 1 if failures else 0


def _read_secrets(lines: list[str]) -> dict[str, SecretStr]:
    """Parse NAME=value lines. Blank lines and comments are skipped."""
    secrets = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or not value:
            raise SystemExit(f"Expected NAME=value, got a line for {name!r}.")
        secrets[name] = SecretStr(value)
    if not secrets:
        raise SystemExit("No NAME=value lines on stdin.")
    return secrets


async def seed_secrets(client: ContractClient, instance_id: UUID) -> int:
    """Replace the named secrets, then recreate the container so it reads them."""
    secrets = _read_secrets(sys.stdin.readlines())
    print(f"{instance_id}: replacing {', '.join(sorted(secrets))}", flush=True)
    command = InstanceSecretsSetCommand(instance_id=instance_id, secrets=secrets)
    if await _operate(client, INSTANCE_SECRETS_SET, command):
        return 1
    recreate = InstanceRecreateCommand(instance_id=instance_id)
    return await _operate(client, INSTANCE_RECREATE, recreate)


async def _main(args: argparse.Namespace, token: AccessToken) -> int:
    async with contract_client(HUB_URL, token) as client:
        match args.command:
            case "instances":
                return await show_instances(client)
            case "update":
                return await update(client, args.revision, args.instance_ids)
            case _:
                return await seed_secrets(client, args.instance_id)


def main() -> int:
    parser = argparse.ArgumentParser(prog="hub.py", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("instances", help="list the active instances")
    update_parser = commands.add_parser("update", help="update instances to a revision")
    update_parser.add_argument("revision", help="full commit sha of the hub's checkout")
    update_parser.add_argument("instance_ids", type=UUID, nargs="*", help="default: all")
    secrets_parser = commands.add_parser("secrets", help="seed secrets from stdin")
    secrets_parser.add_argument("instance_id", type=UUID)
    args = parser.parse_args()
    token = os.environ.get(TOKEN_VARIABLE)
    if not token:
        print(f"Set {TOKEN_VARIABLE} to the hub's access token.", file=sys.stderr)
        return 1
    return asyncio.run(_main(args, AccessToken(token)))


if __name__ == "__main__":
    sys.exit(main())
