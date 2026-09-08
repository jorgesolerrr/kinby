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
from kinby.cli.routines import show_routines, show_startup_routines, watch_routine_notices
from kinby.contracts import (
    THREAD_APPROVAL_RESPOND,
    THREAD_MODE_SET,
    THREAD_SUBSCRIBE,
    THREAD_TURN_DIFF,
    THREAD_TURN_INTERRUPT,
    THREAD_TURN_LIST,
    THREAD_TURN_RATE,
    THREAD_TURN_REVERT,
    THREAD_TURN_START,
    AcceptedResult,
    ApprovalRequested,
    ErrorCode,
    ErrorEnvelope,
    Event,
    GateOutcome,
    MemoryRecapped,
    MessageDelta,
    PermissionMode,
    RoutineListResult,
    RoutineRunOutcome,
    ThreadApprovalRespondCommand,
    ThreadModeSetCommand,
    ThreadSubscribeCommand,
    ThreadTurnDiffCommand,
    ThreadTurnDiffResult,
    ThreadTurnInterruptCommand,
    ThreadTurnListCommand,
    ThreadTurnRateCommand,
    ThreadTurnRevertCommand,
    ThreadTurnStartCommand,
    ToolCall,
    ToolGated,
    ToolResult,
    TurnClosingPayload,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnRated,
    TurnVerdict,
    Warning,
    gate_denial_source,
    is_turn_closing,
)
from kinby.instance import FeedbackPolicy


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
    feedback: FeedbackPolicy,
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    routines = await show_startup_routines(client, stdout, stderr)
    notices = asyncio.create_task(watch_routine_notices(client, stdout))
    try:
        return await _run_repl(
            client,
            thread_id,
            routines=routines,
            feedback=feedback,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )
    finally:
        notices.cancel()
        with suppress(asyncio.CancelledError):
            await notices


async def _run_repl(
    client: ContractClient,
    thread_id: UUID,
    *,
    routines: RoutineListResult | None,
    feedback: FeedbackPolicy,
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
        parked_turn_id = _parked_routine_turn(routines, thread_id)
        if parked_turn_id is not None:
            closing = await _resume_parked_turn(
                client,
                subscription,
                thread_id,
                parked_turn_id,
                repl_io,
            )
            if closing is None:
                return 1
        while True:
            repl_io.stdout.write("> ")
            repl_io.stdout.flush()
            message = await repl_io.stdin.readline()
            if message == "":
                return 0
            message = message.rstrip("\r\n")
            if not message:
                continue
            command, _, argument = message.partition(" ")
            if command == "/routines":
                await show_routines(client, stdout, stderr)
                continue
            if command == "/diff":
                difference = await _diff_turn(client, thread_id, argument.strip())
                if isinstance(difference, ErrorEnvelope):
                    _render_error(difference, stderr)
                    continue
                _render_diff(difference, stdout)
                continue
            if command == "/revert":
                await _revert_turn(client, thread_id, argument.strip(), repl_io)
                continue
            if command == "/mode":
                try:
                    mode = PermissionMode(argument)
                except ValueError:
                    choices = ", ".join(candidate.value for candidate in PermissionMode)
                    _render_error(
                        ErrorEnvelope(
                            code=ErrorCode.INVALID_ARGUMENT,
                            message=f"Permission mode must be one of: {choices}.",
                            retryable=False,
                        ),
                        repl_io.stderr,
                    )
                    continue
                result = await client.call(
                    THREAD_MODE_SET,
                    ThreadModeSetCommand(thread_id=thread_id, mode=mode),
                )
                if isinstance(result, ErrorEnvelope):
                    _render_error(result, stderr)
                    continue
                repl_io.stdout.write(f"Permission mode set to {mode.value}.\n")
                repl_io.stdout.flush()
                continue

            accepted = await client.call(
                THREAD_TURN_START,
                ThreadTurnStartCommand(thread_id=thread_id, message=message),
            )
            if isinstance(accepted, ErrorEnvelope) and accepted.code is ErrorCode.INSTANCE_BUSY:
                _render_error(accepted, stderr)
                while (
                    isinstance(accepted, ErrorEnvelope) and accepted.code is ErrorCode.INSTANCE_BUSY
                ):
                    await asyncio.sleep(1)
                    accepted = await client.call(
                        THREAD_TURN_START,
                        ThreadTurnStartCommand(thread_id=thread_id, message=message),
                    )
            if isinstance(accepted, ErrorEnvelope):
                _render_error(accepted, stderr)
                continue

            interrupter = _InterruptOnSigint(client, thread_id)
            async with interrupter:
                closing = await _render_turn(
                    client,
                    subscription,
                    accepted.turn_id,
                    interrupter.requested,
                    repl_io,
                )
            interrupted = interrupter.result
            if isinstance(interrupted, ErrorEnvelope):
                _render_error(interrupted, stderr)
            if closing is None:
                return 1
            if isinstance(closing, TurnCompleted) and feedback is FeedbackPolicy.EVERY_TURN:
                await _rate_turn(client, thread_id, accepted.turn_id, repl_io)


async def _resolve_turn_id(
    client: ContractClient,
    thread_id: UUID,
    argument: str,
) -> UUID | ErrorEnvelope:
    if argument:
        try:
            return UUID(argument)
        except ValueError:
            pass
        if len(argument) < 8:
            return ErrorEnvelope(
                code=ErrorCode.INVALID_ARGUMENT,
                message="A turn id prefix must contain at least eight characters.",
                retryable=False,
            )
    listed = await client.call(THREAD_TURN_LIST, ThreadTurnListCommand(thread_id=thread_id))
    if isinstance(listed, ErrorEnvelope):
        return listed
    if not argument:
        closed = [turn.turn_id for turn in listed.turns if turn.closed]
        if closed:
            return closed[-1]
        return ErrorEnvelope(
            code=ErrorCode.NOT_FOUND,
            message="No closed turn was found on this thread.",
            retryable=False,
        )
    matches = [turn.turn_id for turn in listed.turns if str(turn.turn_id).startswith(argument)]
    if not matches:
        return ErrorEnvelope(
            code=ErrorCode.NOT_FOUND,
            message=f'Turn id prefix "{argument}" was not found on this thread.',
            retryable=False,
        )
    if len(matches) > 1:
        return ErrorEnvelope(
            code=ErrorCode.INVALID_ARGUMENT,
            message=f'Turn id prefix "{argument}" matches more than one turn.',
            retryable=False,
        )
    return matches[0]


async def _diff_turn(
    client: ContractClient,
    thread_id: UUID,
    argument: str,
) -> ThreadTurnDiffResult | ErrorEnvelope:
    target = await _resolve_turn_id(client, thread_id, argument)
    if isinstance(target, ErrorEnvelope):
        return target
    return await client.call(
        THREAD_TURN_DIFF,
        ThreadTurnDiffCommand(thread_id=thread_id, turn_id=target),
    )


async def _revert_turn(
    client: ContractClient,
    thread_id: UUID,
    argument: str,
    repl_io: _ReplIO,
) -> None:
    difference = await _diff_turn(client, thread_id, argument)
    if isinstance(difference, ErrorEnvelope):
        _render_error(difference, repl_io.stderr)
        return
    _render_files(difference, repl_io.stdout)
    repl_io.stdout.write(
        f"Revert {len(difference.files)} files to before {difference.turn_id}? [y/N] "
    )
    repl_io.stdout.flush()
    answer = (await repl_io.stdin.readline()).rstrip("\r\n")
    if answer != "y":
        return
    result = await client.call(
        THREAD_TURN_REVERT,
        ThreadTurnRevertCommand(thread_id=thread_id, turn_id=difference.turn_id),
    )
    if isinstance(result, ErrorEnvelope):
        _render_error(result, repl_io.stderr)
        return
    repl_io.stdout.write(f"Workspace reverted to before {difference.turn_id}.\n")
    repl_io.stdout.flush()


def _render_files(difference: ThreadTurnDiffResult, stdout: TextIO) -> None:
    for change in difference.files:
        stdout.write(
            f"{change.status.value} {change.path} +{change.additions} -{change.deletions}\n"
        )
    stdout.flush()


def _render_diff(difference: ThreadTurnDiffResult, stdout: TextIO) -> None:
    _render_files(difference, stdout)
    if difference.patch:
        stdout.write(difference.patch)
        if not difference.patch.endswith("\n"):
            stdout.write("\n")
    stdout.flush()


def _parked_routine_turn(routines: RoutineListResult | None, thread_id: UUID) -> UUID | None:
    if routines is None:
        return None
    for routine in routines.routines:
        last = routine.last_run
        if (
            last is not None
            and last.thread_id == thread_id
            and last.outcome is RoutineRunOutcome.PARKED
        ):
            return last.turn_id
    return None


async def _resume_parked_turn(
    client: ContractClient,
    subscription: AsyncGenerator[Event | ErrorEnvelope],
    thread_id: UUID,
    turn_id: UUID,
    repl_io: _ReplIO,
) -> TurnClosingPayload | None:
    async for result in subscription:
        if isinstance(result, ErrorEnvelope):
            _render_error(result, repl_io.stderr)
            return None
        if result.turn_id != turn_id or not isinstance(result.payload, ApprovalRequested):
            continue
        interrupter = _InterruptOnSigint(client, thread_id)
        async with interrupter:
            if not await _answer_approval(client, result, interrupter.requested, repl_io):
                return None
            closing = await _render_turn(
                client,
                subscription,
                turn_id,
                interrupter.requested,
                repl_io,
            )
        if isinstance(interrupter.result, ErrorEnvelope):
            _render_error(interrupter.result, repl_io.stderr)
        return closing
    return None


def _render_error(error: ErrorEnvelope, stderr: TextIO) -> None:
    stderr.write(f"{format_error(error)}\n")
    stderr.flush()


async def _render_turn(
    client: ContractClient,
    subscription: AsyncGenerator[Event | ErrorEnvelope],
    turn_id: UUID,
    interrupted: asyncio.Event,
    repl_io: _ReplIO,
) -> TurnClosingPayload | None:
    denied_calls: set[str] = set()
    async for result in subscription:
        if isinstance(result, ErrorEnvelope):
            _render_error(result, repl_io.stderr)
            return None
        if result.turn_id != turn_id:
            continue
        if isinstance(result.payload, ApprovalRequested):
            if not await _answer_approval(client, result, interrupted, repl_io):
                return None
        else:
            if isinstance(result.payload, ToolGated) and result.payload.action is GateOutcome.DENY:
                denied_calls.add(result.payload.call_id)
            if not (
                isinstance(result.payload, ToolResult) and result.payload.call_id in denied_calls
            ):
                render_event(result, repl_io.stdout, repl_io.stderr)
        if is_turn_closing(result.payload):
            return result.payload
    repl_io.stderr.write("INTERNAL: The thread subscription ended before completion.\n")
    repl_io.stderr.flush()
    return None


async def _rate_turn(
    client: ContractClient,
    thread_id: UUID,
    turn_id: UUID,
    repl_io: _ReplIO,
) -> None:
    repl_io.stdout.write("Rate this turn [g]ood/[b]ad/[enter to skip]: ")
    repl_io.stdout.flush()
    answer = (await repl_io.stdin.readline()).rstrip("\r\n")
    if answer not in {"g", "b"}:
        return
    repl_io.stdout.write("Reason (optional): ")
    repl_io.stdout.flush()
    reason = (await repl_io.stdin.readline()).rstrip("\r\n") or None
    result = await client.call(
        THREAD_TURN_RATE,
        ThreadTurnRateCommand(
            thread_id=thread_id,
            turn_id=turn_id,
            verdict=TurnVerdict.GOOD if answer == "g" else TurnVerdict.BAD,
            reason=reason,
        ),
    )
    if isinstance(result, ErrorEnvelope):
        _render_error(result, repl_io.stderr)


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


def render_event(event: Event, stdout: TextIO, stderr: TextIO) -> None:
    match event.payload:
        case MessageDelta(text=text):
            stdout.write(text)
            stdout.flush()
        case ToolCall(name=name, arguments=arguments):
            stdout.write(f"[tool.call] {name} {json.dumps(arguments, sort_keys=True)}\n")
            stdout.flush()
        case (
            ToolGated(
                name=name,
                action=GateOutcome.DENY,
            ) as gate
        ):
            stdout.write(f"[tool.gated] {name} denied by {gate_denial_source(gate)}\n")
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
        case MemoryRecapped() | TurnRated():
            pass
