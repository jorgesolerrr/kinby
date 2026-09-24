"""`kinby hub update`: run one instance update on a hub and follow it until it ends.

CI runs this with the update token, which reaches `instance.update` and `operation.get` only.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID

from pydantic import ValidationError

from kinby.cli.client import ContractClient, format_error
from kinby.cli.contract_socket import TOKEN_VARIABLE, InsecureContractUrl, contract_client
from kinby.contracts import (
    INSTANCE_UPDATE,
    OPERATION_GET,
    ErrorCode,
    ErrorEnvelope,
    InstanceUpdateCommand,
    OperationGetCommand,
    OperationState,
    PackagePin,
    UpdateToken,
)

#: How often the command reads the operation. An update builds an image, so it takes minutes.
POLL_SECONDS = 1.0


def update_on_hub(argv: list[str]) -> int:
    """Parse the arguments after ``kinby hub update``, then run the update they name."""
    parser = argparse.ArgumentParser(
        prog="kinby hub update",
        description=f"Update one hub instance and follow the update. Reads {TOKEN_VARIABLE}.",
    )
    parser.add_argument(
        "--connect", required=True, metavar="URL", help="the hub's contract URL, ending /ws"
    )
    parser.add_argument("instance_id", type=UUID, help="hub instance id to update")
    parser.add_argument(
        "--revision", required=True, metavar="SHA", help="kinby revision the image builds"
    )
    parser.add_argument(
        "--package", metavar="ID", help="the instance's package, which --package-commit moves"
    )
    parser.add_argument(
        "--package-commit", metavar="SHA", help="git commit to move the instance's package to"
    )
    args = parser.parse_args(argv)
    if (args.package is None) != (args.package_commit is None):
        parser.error("--package and --package-commit go together")
    try:
        command = InstanceUpdateCommand(
            instance_id=args.instance_id,
            revision=args.revision,
            package=(
                PackagePin(id=args.package, sha=args.package_commit)
                if args.package is not None
                else None
            ),
        )
    except ValidationError as exc:
        parser.error(
            "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in exc.errors()
            )
        )
    token = os.environ.get(TOKEN_VARIABLE)
    if not token:
        print(f"Set {TOKEN_VARIABLE} to the hub's update token.", file=sys.stderr)
        return 1
    try:
        return asyncio.run(_update(args.connect, UpdateToken(token), command))
    except InsecureContractUrl as exc:
        print(exc, file=sys.stderr)
        return 1


async def _update(url: str, token: UpdateToken, command: InstanceUpdateCommand) -> int:
    async with contract_client(url, token) as client:
        accepted = await client.call(INSTANCE_UPDATE, command)
        if isinstance(accepted, ErrorEnvelope):
            print(format_error(accepted), file=sys.stderr)
            return 1
        return await _follow(client, OperationGetCommand(operation_id=accepted.operation_id))


async def _follow(client: ContractClient, command: OperationGetCommand) -> int:
    """Print each step once, as it appears, then the outcome. Only success exits zero."""
    printed = 0
    while True:
        operation = await client.call(OPERATION_GET, command)
        if isinstance(operation, ErrorEnvelope):
            # The hub keeps the update running when the socket drops. The next poll
            # reads the same operation once the client has reconnected.
            if operation.code is ErrorCode.CONNECTION_LOST:
                await asyncio.sleep(POLL_SECONDS)
                continue
            print(format_error(operation), file=sys.stderr)
            return 1
        for step in operation.steps[printed:]:
            print(f"{step.name}: {step.detail}", flush=True)
        printed = len(operation.steps)
        match operation.state:
            case OperationState.SUCCEEDED:
                print(f"succeeded: {operation.detail}")
                return 0
            case OperationState.FAILED:
                print(f"failed: {operation.detail}", file=sys.stderr)
                return 1
        await asyncio.sleep(POLL_SECONDS)
