"""Render a thread's event stream as an interactive prompt."""

from __future__ import annotations

import asyncio
import json
import signal
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing, suppress
from dataclasses import dataclass
from threading import Thread
from types import FrameType, TracebackType
from typing import TextIO
from uuid import UUID

from kinby.cli.client import ContractClient, format_error
from kinby.contracts import (
    THREAD_APPROVAL_RESPOND,
    THREAD_SUBSCRIBE,
    THREAD_TURN_INTERRUPT,
    THREAD_TURN_START,
    AcceptedResult,
    ApprovalRequested,
    ErrorEnvelope,
    Event,
    EventType,
    MessageDelta,
    ThreadApprovalRespondCommand,
    ThreadSubscribeCommand,
    ThreadTurnInterruptCommand,
    ThreadTurnStartCommand,
    ToolCall,
    ToolResult,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    Warning,
)

_TERMINAL_EVENTS = {
    EventType.TURN_COMPLETED,
    EventType.TURN_FAILED,
    EventType.TURN_INTERRUPTED,
}


class _AsyncInput:
    def __init__(self, stdin: TextIO) -> None:
        self._stdin = stdin
        self._loop = asyncio.get_running_loop()
        self._lines: asyncio.Queue[str] = asyncio.Queue()
        Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        while True:
            line = self._stdin.readline()
            try:
                self._loop.call_soon_threadsafe(self._lines.put_nowait, line)
            except RuntimeError:
                return
            if line == "":
                return

    async def readline(self) -> str:
        return await self._lines.get()


@dataclass(frozen=True)
class _ReplIO:
    stdin: _AsyncInput
    stdout: TextIO
    stderr: TextIO


class _InterruptOnSigint:
    def __init__(self, client: ContractClient, thread_id: UUID) -> None:
        self._client = client
        self._thread_id = thread_id
        self._loop = asyncio.get_running_loop()
        self._task: asyncio.Task[AcceptedResult | ErrorEnvelope] | None = None
        self._requested = asyncio.Event()
        self._result: AcceptedResult | ErrorEnvelope | None = None
        self._active = False
        self._previous_handler: int | Callable[[int, FrameType | None], object] | None = None

    async def __aenter__(self) -> _InterruptOnSigint:
        self._active = True
        self._previous_handler = signal.signal(signal.SIGINT, self._request)
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._active = False
        if self._previous_handler is not None:
            signal.signal(signal.SIGINT, self._previous_handler)
        if self._task is not None:
            self._result = await self._task

    def _request(self, _signal_number: int, _frame: FrameType | None) -> None:
        self._loop.call_soon_threadsafe(self._start)

    def _start(self) -> None:
        if self._active and self._task is None:
            self._requested.set()
            self._task = asyncio.create_task(
                self._client.call(
                    THREAD_TURN_INTERRUPT,
                    ThreadTurnInterruptCommand(thread_id=self._thread_id),
                )
            )

    @property
    def result(self) -> AcceptedResult | ErrorEnvelope | None:
        return self._result

    @property
    def requested(self) -> asyncio.Event:
        return self._requested


async def run_repl(
    client: ContractClient,
    thread_id: UUID,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    repl_io = _ReplIO(_AsyncInput(stdin), stdout, stderr)
    subscription = client.subscribe(
        THREAD_SUBSCRIBE,
        ThreadSubscribeCommand(thread_id=thread_id),
    )
    async with aclosing(subscription):
        while True:
            repl_io.stdout.write("> ")
            repl_io.stdout.flush()
            message = await repl_io.stdin.readline()
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
                _render_error(accepted, stderr)
                continue

            interrupter = _InterruptOnSigint(client, thread_id)
            async with interrupter:
                rendered = await _render_turn(
                    client,
                    subscription,
                    accepted.turn_id,
                    interrupter.requested,
                    repl_io,
                )
            interrupted = interrupter.result
            if isinstance(interrupted, ErrorEnvelope):
                _render_error(interrupted, stderr)
            if not rendered:
                return 1


def _render_error(error: ErrorEnvelope, stderr: TextIO) -> None:
    stderr.write(f"{format_error(error)}\n")
    stderr.flush()


async def _render_turn(
    client: ContractClient,
    subscription: AsyncGenerator[Event | ErrorEnvelope],
    turn_id: UUID,
    interrupted: asyncio.Event,
    repl_io: _ReplIO,
) -> bool:
    async for result in subscription:
        if isinstance(result, ErrorEnvelope):
            _render_error(result, repl_io.stderr)
            return False
        if result.turn_id != turn_id:
            continue
        if isinstance(result.payload, ApprovalRequested):
            if not await _answer_approval(client, result, interrupted, repl_io):
                return False
        else:
            _render_event(result, repl_io.stdout, repl_io.stderr)
        if result.type in _TERMINAL_EVENTS:
            return True
    repl_io.stderr.write("INTERNAL: The thread subscription ended before completion.\n")
    repl_io.stderr.flush()
    return False


async def _answer_approval(
    client: ContractClient,
    event: Event,
    interrupted: asyncio.Event,
    repl_io: _ReplIO,
) -> bool:
    approval = event.payload
    if not isinstance(approval, ApprovalRequested):
        return False
    arguments = json.dumps(approval.arguments, sort_keys=True)
    repl_io.stdout.write(
        f'Approve {approval.name} {arguments} under rule "{approval.rule}"? [yes/no] '
    )
    repl_io.stdout.flush()
    answer = asyncio.create_task(repl_io.stdin.readline())
    interruption = asyncio.create_task(interrupted.wait())
    await asyncio.wait((answer, interruption), return_when=asyncio.FIRST_COMPLETED)
    if interrupted.is_set():
        answer.cancel()
        with suppress(asyncio.CancelledError):
            await answer
        return True
    interruption.cancel()
    with suppress(asyncio.CancelledError):
        await interruption
    result = await client.call(
        THREAD_APPROVAL_RESPOND,
        ThreadApprovalRespondCommand(
            thread_id=event.thread_id,
            approval_id=approval.approval_id,
            answer=answer.result().rstrip("\r\n"),
        ),
    )
    if isinstance(result, ErrorEnvelope):
        _render_error(result, repl_io.stderr)
        return False
    return True


def _render_event(event: Event, stdout: TextIO, stderr: TextIO) -> None:
    match event.payload:
        case MessageDelta(text=text):
            stdout.write(text)
            stdout.flush()
        case ToolCall(name=name, arguments=arguments):
            stdout.write(f"[tool.call] {name} {json.dumps(arguments, sort_keys=True)}\n")
            stdout.flush()
        case ToolResult(name=name, output=output, error=error):
            status = "error" if error else "ok"
            stdout.write(f"[tool.result] {name} ({status}): {output}\n")
            stdout.flush()
        case Warning(sources=sources, message=message):
            stderr.write(f"[warning] {', '.join(sources)}: {message}\n")
            stderr.flush()
        case TurnCompleted():
            stdout.write("\n")
            stdout.flush()
        case TurnFailed(code=code, message=message):
            stderr.write(f"{code.value}: {message}\n")
            stderr.flush()
        case TurnInterrupted():
            stdout.write("(interrupted)\n")
            stdout.flush()
