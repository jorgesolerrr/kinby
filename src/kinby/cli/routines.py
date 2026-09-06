"""Render the routine contract for terminal clients."""

import asyncio
from typing import TextIO

from kinby.cli.client import ContractClient, format_error
from kinby.contracts import ROUTINE_LIST, ErrorEnvelope, RoutineListCommand, RoutineListResult


def render_routines(result: RoutineListResult, stdout: TextIO) -> None:
    for routine in result.routines:
        state = "enabled" if routine.enabled else "disabled"
        next_run = routine.next_run.isoformat() if routine.next_run else "none"
        last = routine.last_run
        last_run = (
            f"{last.started_at.isoformat()} {last.outcome} {last.first_line}" if last else "none"
        )
        stdout.write(
            f"{routine.name}\t{routine.description}\t{state}\t"
            f"schedule={routine.schedule or 'none'}\tmode={routine.mode.value}\t"
            f"failures={routine.failure_count}\tnext={next_run}\tlast={last_run}\n"
        )
        if routine.last_failure is not None:
            stdout.write(f"  Last failure: {routine.last_failure}\n")
        for notice in routine.notices:
            stdout.write(f"  {notice.message}\n")
    for warning in result.warnings:
        stdout.write(f"warning: {', '.join(warning.sources)}: {warning.message}\n")
    stdout.flush()


async def show_routines(client: ContractClient, stdout: TextIO, stderr: TextIO) -> int:
    result = await client.call(ROUTINE_LIST, RoutineListCommand())
    if isinstance(result, ErrorEnvelope):
        stderr.write(f"{format_error(result)}\n")
        return 1
    render_routines(result, stdout)
    return 0


async def watch_routine_notices(client: ContractClient, stdout: TextIO) -> None:
    result = await client.call(ROUTINE_LIST, RoutineListCommand())
    if isinstance(result, ErrorEnvelope):
        return
    seen = {
        (notice.thread_id, notice.turn_id, notice.kind)
        for routine in result.routines
        for notice in routine.notices
    }
    while True:
        await asyncio.sleep(1)
        result = await client.call(ROUTINE_LIST, RoutineListCommand())
        if isinstance(result, ErrorEnvelope):
            return
        for routine in result.routines:
            for notice in routine.notices:
                key = (notice.thread_id, notice.turn_id, notice.kind)
                if key not in seen:
                    stdout.write(f"\n{notice.message}\n")
                    stdout.flush()
                    seen.add(key)
