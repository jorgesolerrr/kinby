"""Render a thread's event stream as an interactive prompt."""

from __future__ import annotations

from contextlib import aclosing
from typing import TextIO
from uuid import UUID

from kinby.cli.client import UNEXPECTED_RESULT_ERROR, ContractClient, format_error
from kinby.contracts import AcceptedResult, ErrorCode, ErrorEnvelope, Event, EventType

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
    subscription = client.subscribe(
        "thread.subscribe",
        {"thread_id": thread_id, "after_sequence": 0},
    )
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
                "thread.turn.start",
                {"thread_id": thread_id, "message": message},
            )
            if isinstance(accepted, ErrorEnvelope):
                stderr.write(f"{format_error(accepted)}\n")
                stderr.flush()
                continue
            if not isinstance(accepted, AcceptedResult):
                stderr.write(f"{UNEXPECTED_RESULT_ERROR}\n")
                stderr.flush()
                return 1

            async for result in subscription:
                if isinstance(result, ErrorEnvelope):
                    stderr.write(f"{format_error(result)}\n")
                    stderr.flush()
                    return 1
                if not isinstance(result, Event) or result.turn_id != accepted.turn_id:
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
