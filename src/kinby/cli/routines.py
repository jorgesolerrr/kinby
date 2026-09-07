"""Render the routine contract for terminal clients."""

import asyncio
from typing import TextIO

from kinby.cli.client import ContractClient, format_error
from kinby.contracts import (
    ROUTINE_LIST,
    ErrorCode,
    ErrorEnvelope,
    RoutineListCommand,
    RoutineListResult,
    RoutineNoticeKind,
    RoutineRunOutcome,
    RoutineSummary,
)


def render_routines(result: RoutineListResult, stdout: TextIO) -> None:
    stdout.write("name\tschedule\tenabled\tlast run\tnext run\tsignal\tauth\tpending\n")
    for routine in result.routines:
        state = "enabled" if routine.enabled else "disabled"
        next_run = routine.next_run.isoformat() if routine.next_run else "none"
        last = routine.last_run
        last_run = (
            f"{last.started_at.isoformat()} {last.outcome} {last.first_line}" if last else "none"
        )
        path = routine.signal.path if routine.signal is not None else "none"
        auth = routine.signal.auth if routine.signal is not None else "none"
        stdout.write(
            f"{routine.name}\t{routine.schedule or 'none'}\t{state}\t"
            f"{last_run}\t{next_run}\t{path}\t{auth}\t{routine.pending}\n"
        )
        render_routine_status(routine, stdout)
    for warning in result.warnings:
        stdout.write(f"warning: {', '.join(warning.sources)}: {warning.message}\n")
    stdout.flush()


def render_startup_routines(result: RoutineListResult, stdout: TextIO) -> None:
    for routine in result.routines:
        if routine.last_run is None:
            pending = f", {routine.pending} pending" if routine.signal is not None else ""
            stdout.write(f'Routine "{routine.name}": never ran{pending}.\n')
            continue
        last = routine.last_run
        if last.outcome is RoutineRunOutcome.PARKED:
            details = f"{last.started_at.isoformat()}, parked on thread {last.thread_id}"
        else:
            details = f"{last.started_at.isoformat()}, {last.outcome}"
        if last.first_line:
            details = f"{details}, {last.first_line}"
        if routine.signal is not None:
            details = f"{details}, {routine.pending} pending"
        stdout.write(f'Routine "{routine.name}": {details}\n')
        render_routine_status(routine, stdout)
    stdout.flush()


def render_routine_status(routine: RoutineSummary, stdout: TextIO) -> None:
    if routine.last_failure is not None:
        stdout.write(f"  Last failure: {routine.last_failure}\n")
    for notice in routine.notices:
        stdout.write(f"  {notice.message}\n")
        if notice.kind is RoutineNoticeKind.DISABLED:
            stdout.write(
                f"  Re-enable it by setting enabled = true in routines/{routine.name}/ROUTINE.md.\n"
            )


async def show_startup_routines(
    client: ContractClient, stdout: TextIO, stderr: TextIO
) -> RoutineListResult | None:
    result = await client.call(ROUTINE_LIST, RoutineListCommand())
    if isinstance(result, ErrorEnvelope):
        if result.code is not ErrorCode.NOT_FOUND:
            stderr.write(f"{format_error(result)}\n")
            stderr.flush()
        return None
    render_startup_routines(result, stdout)
    return result


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
