"""`kinby hub adopt`: preview or run one adoption on a hub, and follow the handoff.

The operator runs this with the hub's access token. The preview prints what the hub
found as JSON and exits non-zero while a finding blocks the handoff.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from kinby.cli.client import format_error
from kinby.cli.contract_socket import TOKEN_VARIABLE, InsecureContractUrl, contract_client
from kinby.cli.hub_update import follow_operation
from kinby.contracts import (
    INSTANCE_ADOPT,
    INSTANCE_ADOPT_PREVIEW,
    AccessToken,
    ErrorEnvelope,
    InstanceAdoptCommand,
    InstanceAdoptPreviewCommand,
    OperationGetCommand,
    PackageSelection,
)


def adopt_on_hub(argv: list[str]) -> int:
    """Parse the arguments after ``kinby hub adopt``, then preview or run the adoption."""
    parser = argparse.ArgumentParser(
        prog="kinby hub adopt",
        description=f"Preview or adopt an existing instance. Reads {TOKEN_VARIABLE}.",
    )
    parser.add_argument(
        "--connect", required=True, metavar="URL", help="the hub's contract URL, ending /ws"
    )
    parser.add_argument("path", type=Path, help="the instance directory as the hub sees it")
    parser.add_argument("runtime_id", help="the container that runs the instance now")
    parser.add_argument("--preview", action="store_true", help="print the preflight and stop")
    parser.add_argument(
        "--relinquished", action="store_true", help="the previous manager has let go"
    )
    parser.add_argument(
        "--acknowledge-interrupting-stop",
        action="store_true",
        help="a runtime that cannot drain may be interrupted",
    )
    parser.add_argument(
        "--claim-signals", action="store_true", help="keep the established webhook URL here"
    )
    parser.add_argument(
        "--package",
        type=Path,
        metavar="FILE",
        help="JSON package selection the manifest's [package] names",
    )
    args = parser.parse_args(argv)
    try:
        package = (
            PackageSelection.model_validate_json(args.package.read_text(encoding="utf-8"))
            if args.package is not None
            else None
        )
    except (OSError, ValidationError) as exc:
        parser.error(f"--package: {exc}")
    command = InstanceAdoptCommand(
        path=args.path,
        runtime_id=args.runtime_id,
        relinquished=args.relinquished,
        acknowledge_interrupting_stop=args.acknowledge_interrupting_stop,
        claim_signals=args.claim_signals,
        package=package,
    )
    token = os.environ.get(TOKEN_VARIABLE)
    if not token:
        print(f"Set {TOKEN_VARIABLE} to the hub's access token.", file=sys.stderr)
        return 1
    try:
        return asyncio.run(_adopt(args.connect, AccessToken(token), command, preview=args.preview))
    except InsecureContractUrl as exc:
        print(exc, file=sys.stderr)
        return 1


async def _adopt(
    url: str,
    token: AccessToken,
    command: InstanceAdoptCommand,
    *,
    preview: bool,
) -> int:
    async with contract_client(url, token) as client:
        if preview:
            previewed = await client.call(
                INSTANCE_ADOPT_PREVIEW,
                InstanceAdoptPreviewCommand.model_validate(
                    command.model_dump(exclude={"claim_signals"})
                ),
            )
            if isinstance(previewed, ErrorEnvelope):
                print(format_error(previewed), file=sys.stderr)
                return 1
            print(previewed.model_dump_json(indent=2))
            return 1 if any(finding.blocking for finding in previewed.findings) else 0
        accepted = await client.call(INSTANCE_ADOPT, command)
        if isinstance(accepted, ErrorEnvelope):
            print(format_error(accepted), file=sys.stderr)
            return 1
        return await follow_operation(
            client, OperationGetCommand(operation_id=accepted.operation_id)
        )
