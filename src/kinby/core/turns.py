"""Run one model turn and record its client-facing events."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Protocol
from uuid import UUID, uuid4

from kinby.contracts import (
    AcceptedResult,
    ErrorCode,
    Event,
    Payload,
    ThreadTurnInterruptCommand,
    ThreadTurnStartCommand,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnStarted,
)
from kinby.core.errors import CoreError, NoActiveTurn, ThreadBusy, ThreadNotFound
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


Emit = Callable[[Payload], Awaitable[Event]]


class TurnRunner(Protocol):
    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome: ...


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
        if not self._store.exists(command.thread_id):
            raise ThreadNotFound(f'Thread "{command.thread_id}" was not found.')
        running_turn = self._running.get(command.thread_id)
        running = running_turn is not None and not running_turn.task.done()
        if command.thread_id in self._starting or running:
            raise ThreadBusy(f'Thread "{command.thread_id}" already has a running turn.')

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
        task = asyncio.create_task(self._run(turn))
        self._running[turn.thread_id] = RunningTurn(turn, task)
        task.add_done_callback(partial(self._forget_task, turn.thread_id))
        return AcceptedResult(
            thread_id=turn.thread_id,
            turn_id=turn.turn_id,
            sequence=started.sequence,
        )

    async def interrupt(self, command: ThreadTurnInterruptCommand) -> AcceptedResult:
        if not self._store.exists(command.thread_id):
            raise ThreadNotFound(f'Thread "{command.thread_id}" was not found.')
        running = self._running.get(command.thread_id)
        if running is None or running.task.done() or running.interrupted:
            raise NoActiveTurn(f'Thread "{command.thread_id}" has no active turn.')
        # Keep the turn busy until its terminal event is recorded.
        running.interrupted = True
        running.task.cancel()
        interrupted = await self._log.append(
            command.thread_id,
            running.request.turn_id,
            TurnInterrupted(),
        )
        if self._running.get(command.thread_id) is running:
            del self._running[command.thread_id]
        return AcceptedResult(
            thread_id=interrupted.thread_id,
            turn_id=interrupted.turn_id,
            sequence=interrupted.sequence,
        )

    async def _run(self, turn: TurnRequest) -> None:
        async def emit(payload: Payload) -> Event:
            running = self._running.get(turn.thread_id)
            if running is None or running.request.turn_id != turn.turn_id or running.interrupted:
                raise asyncio.CancelledError
            return await self._log.append(turn.thread_id, turn.turn_id, payload)

        try:
            outcome = await self._runner.run(turn, emit)
        except CoreError as exc:
            code = exc.code
            message = str(exc)
        except Exception:
            logger.exception("The model turn failed.")
            code = ErrorCode.INTERNAL
            message = "The model turn failed unexpectedly."
        else:
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
