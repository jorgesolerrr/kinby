"""Render a thread's event stream as an interactive prompt."""

from __future__ import annotations

from contextlib import aclosing
from typing import TextIO
from uuid import UUID

from kinby.cli.client import ContractClient, format_error
from kinby.contracts import (
    THREAD_SUBSCRIBE,
    THREAD_TURN_START,
    ErrorCode,
    ErrorEnvelope,
    Event,
    EventType,
    ThreadSubscribeCommand,
    ThreadTurnStartCommand,
)

_TERMINAL_EVENTS = {
    EventType.TURN_COMPLETED,
    EventType.TURN_FAILED,
}


async def run_repl(
    client: ContractClient,
    thread_id: UUID,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    subscription = client.subscribe(THREAD_SUBSCRIBE, ThreadSubscribeCommand(thread_id=thread_id))
    async with aclosing(subscription):
        while True:
            stdout.write("> ")
            stdout.flush()
            message = stdin.readline()
            if message == "":
                return 0
            message = message.rstrip("\r\n")
            if not message:
                continue

            accepted = await client.call(
                THREAD_TURN_START,
                ThreadTurnStartCommand(thread_id=thread_id, message=message),
            )
            if isinstance(accepted, ErrorEnvelope):
                stderr.write(f"{format_error(accepted)}\n")
                stderr.flush()
                continue

            async for result in subscription:
                if isinstance(result, ErrorEnvelope):
                    stderr.write(f"{format_error(result)}\n")
                    stderr.flush()
                    return 1
                if result.turn_id != accepted.turn_id:
                    continue
                _render_event(result, stdout, stderr)
                if result.type in _TERMINAL_EVENTS:
                    break
            else:
                stderr.write("INTERNAL: The thread subscription ended before completion.\n")
                stderr.flush()
                return 1


def _render_event(event: Event, stdout: TextIO, stderr: TextIO) -> None:
    if event.type is EventType.MESSAGE_DELTA:
        text = event.payload.get("text")
        if isinstance(text, str):
            stdout.write(text)
            stdout.flush()
    elif event.type is EventType.TURN_COMPLETED:
        stdout.write("\n")
        stdout.flush()
    elif event.type is EventType.TURN_FAILED:
        code = event.payload.get("code", ErrorCode.INTERNAL.value)
        message = event.payload.get("message", "The model turn failed.")
        stderr.write(f"{code}: {message}\n")
        stderr.flush()
