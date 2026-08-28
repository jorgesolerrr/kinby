"""Run one model turn and record its client-facing events."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Protocol
from uuid import UUID, uuid4

from kinby.contracts import (
    AcceptedResult,
    ApprovalRequested,
    ErrorCode,
    Event,
    Payload,
    ThreadApprovalRespondCommand,
    ThreadTurnInterruptCommand,
    ThreadTurnStartCommand,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnStarted,
)
from kinby.core.errors import (
    ApprovalNotFound,
    CoreError,
    InvalidParkedTurn,
    NoActiveTurn,
    ThreadBusy,
    ThreadNotFound,
    TurnInterruptedError,
)
from kinby.core.events import EventLog
from kinby.core.threads import ThreadStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnRequest:
    thread_id: UUID
    turn_id: UUID
    message: str


@dataclass(frozen=True)
class TurnOutcome:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ParkedTurn:
    pass


@dataclass(frozen=True)
class PendingApproval:
    event: Event
    request: ApprovalRequested


TurnResult = TurnOutcome | ParkedTurn
Emit = Callable[[Payload], Awaitable[Event]]


class TurnRunner(Protocol):
    async def run(self, turn: TurnRequest, emit: Emit) -> TurnResult: ...
    async def resume(self, turn: TurnRequest, answer: str, emit: Emit) -> TurnResult: ...


@dataclass
class RunningTurn:
    request: TurnRequest
    task: asyncio.Task[None]
    interrupted: bool = False


class Turns:
    def __init__(
        self,
        store: ThreadStore,
        log: EventLog,
        runner: TurnRunner,
        model: str,
    ) -> None:
        self._store = store
        self._log = log
        self._runner = runner
        self._model = model
        self._running: dict[UUID, RunningTurn] = {}
        self._starting: set[UUID] = set()

    async def start(self, command: ThreadTurnStartCommand) -> AcceptedResult:
        self._require_thread(command.thread_id)
        pending = _pending_approval(self._log.stored(command.thread_id))
        running = self._running.get(command.thread_id)
        active = running is not None and not running.task.done()
        if command.thread_id in self._starting or active or pending is not None:
            raise _thread_busy(command.thread_id)

        self._starting.add(command.thread_id)
        turn = TurnRequest(
            thread_id=command.thread_id,
            turn_id=uuid4(),
            message=command.message,
        )
        try:
            started = await self._log.append(
                turn.thread_id,
                turn.turn_id,
                TurnStarted(message=turn.message, model=self._model),
            )
        finally:
            self._starting.remove(command.thread_id)
        self._spawn(turn, self._run(turn))
        return AcceptedResult(
            thread_id=turn.thread_id,
            turn_id=turn.turn_id,
            sequence=started.sequence,
        )

    async def interrupt(self, command: ThreadTurnInterruptCommand) -> AcceptedResult:
        self._require_thread(command.thread_id)
        running = self._running.get(command.thread_id)
        if running is not None and not running.task.done() and not running.interrupted:
            running.interrupted = True
            running.task.cancel()
            turn_id = running.request.turn_id
        else:
            pending = _pending_approval(self._log.stored(command.thread_id))
            if pending is None:
                raise _no_active_turn(command.thread_id)
            turn_id = pending.event.turn_id

        interrupted = await self._log.append(
            command.thread_id,
            turn_id,
            TurnInterrupted(),
        )
        if running is not None and self._running.get(command.thread_id) is running:
            del self._running[command.thread_id]
        return AcceptedResult(
            thread_id=interrupted.thread_id,
            turn_id=interrupted.turn_id,
            sequence=interrupted.sequence,
        )

    async def respond(self, command: ThreadApprovalRespondCommand) -> AcceptedResult:
        self._require_thread(command.thread_id)
        events = self._log.stored(command.thread_id)
        pending = _pending_approval(events)
        known = any(
            isinstance(event.payload, ApprovalRequested)
            and event.payload.approval_id == command.approval_id
            for event in events
        )
        if pending is None and known:
            raise _no_active_turn(command.thread_id)
        if pending is None or pending.request.approval_id != command.approval_id:
            raise ApprovalNotFound(f'Approval "{command.approval_id}" was not found.')
        running = self._running.get(command.thread_id)
        if running is not None and not running.task.done():
            raise _thread_busy(command.thread_id)
        turn = _restore_parked_turn(events, pending)
        self._spawn(turn, self._resume(turn, command.answer))
        # respond appends no event, so approval.requested is the resume cursor.
        return AcceptedResult(
            thread_id=turn.thread_id,
            turn_id=turn.turn_id,
            sequence=pending.event.sequence,
        )

    def _require_thread(self, thread_id: UUID) -> None:
        if not self._store.exists(thread_id):
            raise ThreadNotFound(f'Thread "{thread_id}" was not found.')

    def _spawn(self, turn: TurnRequest, work: Coroutine[object, object, None]) -> None:
        task = asyncio.create_task(work)
        self._running[turn.thread_id] = RunningTurn(turn, task)
        task.add_done_callback(partial(self._forget_task, turn.thread_id))

    async def _run(self, turn: TurnRequest) -> None:
        await self._finish(turn, lambda emit: self._runner.run(turn, emit))

    async def _resume(self, turn: TurnRequest, answer: str) -> None:
        await self._finish(turn, lambda emit: self._runner.resume(turn, answer, emit))

    async def _finish(
        self,
        turn: TurnRequest,
        run: Callable[[Emit], Awaitable[TurnResult]],
    ) -> None:
        async def emit(payload: Payload) -> Event:
            running = self._running.get(turn.thread_id)
            if running is None or running.request.turn_id != turn.turn_id or running.interrupted:
                raise TurnInterruptedError
            return await self._log.append(turn.thread_id, turn.turn_id, payload)

        try:
            outcome = await run(emit)
        except CoreError as exc:
            code = exc.code
            message = str(exc)
        except Exception:
            logger.exception("The model turn failed.")
            code = ErrorCode.INTERNAL
            message = "The model turn failed unexpectedly."
        else:
            if isinstance(outcome, ParkedTurn):
                return
            await emit(
                TurnCompleted(
                    input_tokens=outcome.input_tokens,
                    output_tokens=outcome.output_tokens,
                )
            )
            return

        await emit(TurnFailed(code=code, message=message))

    def _forget_task(self, thread_id: UUID, task: asyncio.Task[None]) -> None:
        running = self._running.get(thread_id)
        if running is not None and running.task is task:
            del self._running[thread_id]
        if not task.cancelled() and (exception := task.exception()) is not None:
            task.get_loop().call_exception_handler(
                {
                    "message": "A turn task failed outside the event stream.",
                    "exception": exception,
                    "task": task,
                }
            )


def _pending_approval(events: Sequence[Event]) -> PendingApproval | None:
    if not events or not isinstance(events[-1].payload, ApprovalRequested):
        return None
    return PendingApproval(events[-1], events[-1].payload)


def _restore_parked_turn(
    events: Sequence[Event],
    pending: PendingApproval,
) -> TurnRequest:
    started = next(
        (
            event.payload
            for event in reversed(events)
            if event.turn_id == pending.event.turn_id and isinstance(event.payload, TurnStarted)
        ),
        None,
    )
    if started is None:
        raise InvalidParkedTurn("The parked turn is missing turn.started.")
    return TurnRequest(
        thread_id=pending.event.thread_id,
        turn_id=pending.event.turn_id,
        message=started.message,
    )


def _thread_busy(thread_id: UUID) -> ThreadBusy:
    return ThreadBusy(f'Thread "{thread_id}" already has a running turn.')


def _no_active_turn(thread_id: UUID) -> NoActiveTurn:
    return NoActiveTurn(f'Thread "{thread_id}" has no active turn.')
