"""Run one model turn and record its client-facing events."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import partial
from typing import Protocol
from uuid import UUID, uuid4

from kinby.contracts import (
    AcceptedResult,
    ApprovalRequested,
    ErrorCode,
    Event,
    ModePinned,
    Payload,
    PermissionMode,
    ThreadApprovalRespondCommand,
    ThreadModeSetCommand,
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
    PermissionDenied,
    ThreadBusy,
    ThreadNotFound,
    TurnInterruptedError,
)
from kinby.core.events import EventLog
from kinby.core.threads import ThreadStore
from kinby.instance import Budgets

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnRequest:
    thread_id: UUID
    turn_id: UUID
    message: str
    model: str
    permission_mode: PermissionMode


@dataclass(frozen=True)
class TurnPreparation:
    model: str
    default_mode: PermissionMode
    ceiling: PermissionMode
    budgets: Budgets = field(default_factory=Budgets)


@dataclass(frozen=True)
class TurnOutcome:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ParkedTurn:
    pass


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    DENY = "deny"


@dataclass(frozen=True)
class PendingApproval:
    event: Event
    request: ApprovalRequested


TurnResult = TurnOutcome | ParkedTurn
Emit = Callable[[Payload], Awaitable[Event]]


@dataclass(frozen=True)
class TurnContext:
    budgets: Budgets
    emit: Emit

    # Keep existing TurnRunner implementations that accept Emit protocol-compatible.
    async def __call__(self, payload: Payload) -> Event:
        return await self.emit(payload)


class TurnRunner(Protocol):
    async def restore(self, thread_id: UUID, turn_id: UUID) -> TurnRequest | None: ...
    async def run(self, turn: TurnRequest, context: TurnContext, /) -> TurnResult: ...
    async def resume(
        self,
        turn: TurnRequest,
        decision: ApprovalDecision,
        context: TurnContext,
        /,
    ) -> TurnResult: ...


class ClosedTurnHook(Protocol):
    def __call__(self, thread_id: UUID, turn_id: UUID) -> None: ...


@dataclass
class RunningTurn:
    request: TurnRequest
    task: asyncio.Task[None]
    interrupted: bool = False


class TurnClaim:
    pass


class InterruptedTurnClaim:
    pass


class Turns:
    def __init__(
        self,
        store: ThreadStore,
        log: EventLog,
        runner: TurnRunner,
        prepare_for_turn: Callable[[], TurnPreparation],
        permission_ceiling: Callable[[], PermissionMode],
        after_turn: ClosedTurnHook,
    ) -> None:
        self._store = store
        self._log = log
        self._runner = runner
        self._prepare_for_turn = prepare_for_turn
        self._permission_ceiling = permission_ceiling
        self._after_turn = after_turn
        self._running: dict[UUID, RunningTurn] = {}
        self._claims: dict[UUID, TurnClaim | InterruptedTurnClaim] = {}

    async def set_mode(self, command: ThreadModeSetCommand) -> AcceptedResult:
        self._require_thread(command.thread_id)
        events = self._log.stored(command.thread_id)
        running = self._running.get(command.thread_id)
        active = running is not None and not running.task.done()
        if command.thread_id in self._claims or active or _pending_approval(events) is not None:
            raise _thread_busy(command.thread_id)
        ceiling = self._permission_ceiling()
        if _exceeds_ceiling(command.mode, ceiling):
            raise PermissionDenied(
                f'Permission mode "{command.mode.value}" exceeds the instance ceiling '
                f'"{ceiling.value}".'
            )
        event = await self._log.append(command.thread_id, uuid4(), ModePinned(mode=command.mode))
        return AcceptedResult(
            thread_id=event.thread_id,
            turn_id=event.turn_id,
            sequence=event.sequence,
        )

    async def start(self, command: ThreadTurnStartCommand) -> AcceptedResult:
        self._require_thread(command.thread_id)
        events = self._log.stored(command.thread_id)
        pending = _pending_approval(events)
        running = self._running.get(command.thread_id)
        active = running is not None and not running.task.done()
        if command.thread_id in self._claims or active or pending is not None:
            raise _thread_busy(command.thread_id)

        claim = TurnClaim()
        self._claims[command.thread_id] = claim
        try:
            preparation = self._prepare_for_turn()
            turn = TurnRequest(
                thread_id=command.thread_id,
                turn_id=uuid4(),
                message=command.message,
                model=preparation.model,
                permission_mode=_permission_mode(
                    events,
                    preparation.default_mode,
                    preparation.ceiling,
                ),
            )
            started = await self._log.append(
                turn.thread_id,
                turn.turn_id,
                TurnStarted(
                    message=turn.message,
                    model=turn.model,
                    permission_mode=turn.permission_mode,
                ),
            )
        finally:
            self._release_claim(command.thread_id, claim)
        self._spawn(turn, self._run(turn, preparation.budgets))
        return AcceptedResult(
            thread_id=turn.thread_id,
            turn_id=turn.turn_id,
            sequence=started.sequence,
        )

    async def interrupt(self, command: ThreadTurnInterruptCommand) -> AcceptedResult:
        self._require_thread(command.thread_id)
        running = self._running.get(command.thread_id)
        claim: InterruptedTurnClaim
        if running is not None and not running.task.done() and not running.interrupted:
            claim = InterruptedTurnClaim()
            self._claims[command.thread_id] = claim
            running.interrupted = True
            running.task.cancel()
            turn_id = running.request.turn_id
        else:
            pending = _pending_approval(self._log.stored(command.thread_id))
            if pending is None:
                raise _no_active_turn(command.thread_id)
            turn_id = pending.event.turn_id
            if isinstance(self._claims.get(command.thread_id), InterruptedTurnClaim):
                raise _no_active_turn(command.thread_id)
            claim = InterruptedTurnClaim()
            self._claims[command.thread_id] = claim

        try:
            interrupted = await self._log.append(
                command.thread_id,
                turn_id,
                TurnInterrupted(),
            )
            self._schedule_after_turn(command.thread_id, turn_id)
        finally:
            self._release_claim(command.thread_id, claim)
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
        if command.thread_id in self._claims or (running is not None and not running.task.done()):
            raise _thread_busy(command.thread_id)
        claim = TurnClaim()
        self._claims[command.thread_id] = claim
        try:
            preparation = self._prepare_for_turn()
            turn = await self._runner.restore(pending.event.thread_id, pending.event.turn_id)
            if self._claims.get(command.thread_id) is not claim:
                raise _no_active_turn(command.thread_id)
            if turn is None:
                raise InvalidParkedTurn("The parked turn cannot resume after a runtime restart.")
            turn = replace(
                turn,
                permission_mode=_constrain_mode(
                    turn.permission_mode,
                    preparation.ceiling,
                ),
            )
            decision = (
                ApprovalDecision.APPROVE if command.answer == "yes" else ApprovalDecision.DENY
            )
            self._spawn(turn, self._resume(turn, decision, preparation.budgets))
        finally:
            self._release_claim(command.thread_id, claim)
        # respond appends no event, so approval.requested is the resume cursor.
        return AcceptedResult(
            thread_id=turn.thread_id,
            turn_id=turn.turn_id,
            sequence=pending.event.sequence,
        )

    def _require_thread(self, thread_id: UUID) -> None:
        if not self._store.exists(thread_id):
            raise ThreadNotFound(f'Thread "{thread_id}" was not found.')

    def _release_claim(
        self,
        thread_id: UUID,
        claim: TurnClaim | InterruptedTurnClaim,
    ) -> None:
        if self._claims.get(thread_id) is claim:
            del self._claims[thread_id]

    def _spawn(self, turn: TurnRequest, work: Coroutine[object, object, None]) -> None:
        task = asyncio.create_task(work)
        self._running[turn.thread_id] = RunningTurn(turn, task)
        task.add_done_callback(partial(self._forget_task, turn.thread_id))

    async def _run(self, turn: TurnRequest, budgets: Budgets) -> None:
        await self._finish(turn, budgets, lambda context: self._runner.run(turn, context))

    async def _resume(
        self,
        turn: TurnRequest,
        decision: ApprovalDecision,
        budgets: Budgets,
    ) -> None:
        await self._finish(
            turn,
            budgets,
            lambda context: self._runner.resume(turn, decision, context),
        )

    async def _finish(
        self,
        turn: TurnRequest,
        budgets: Budgets,
        run: Callable[[TurnContext], Awaitable[TurnResult]],
    ) -> None:
        async def emit(payload: Payload) -> Event:
            running = self._running.get(turn.thread_id)
            if running is None or running.request.turn_id != turn.turn_id or running.interrupted:
                raise TurnInterruptedError
            return await self._log.append(turn.thread_id, turn.turn_id, payload)

        try:
            outcome = await run(TurnContext(budgets, emit))
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
            self._schedule_after_turn(turn.thread_id, turn.turn_id)
            return

        await emit(TurnFailed(code=code, message=message))
        self._schedule_after_turn(turn.thread_id, turn.turn_id)

    def _schedule_after_turn(self, thread_id: UUID, turn_id: UUID) -> None:
        try:
            self._after_turn(thread_id, turn_id)
        except Exception:
            logger.exception("The turn recap could not be scheduled.")

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


def _permission_mode(
    events: Sequence[Event],
    default: PermissionMode,
    ceiling: PermissionMode,
) -> PermissionMode:
    mode = next(
        (event.payload.mode for event in reversed(events) if isinstance(event.payload, ModePinned)),
        default,
    )
    return _constrain_mode(mode, ceiling)


def _constrain_mode(mode: PermissionMode, ceiling: PermissionMode) -> PermissionMode:
    if _exceeds_ceiling(mode, ceiling):
        return ceiling
    return mode


def _exceeds_ceiling(mode: PermissionMode, ceiling: PermissionMode) -> bool:
    return _MODE_ORDER.index(mode) > _MODE_ORDER.index(ceiling)


def _thread_busy(thread_id: UUID) -> ThreadBusy:
    return ThreadBusy(f'Thread "{thread_id}" already has a running turn.')


def _no_active_turn(thread_id: UUID) -> NoActiveTurn:
    return NoActiveTurn(f'Thread "{thread_id}" has no active turn.')


_MODE_ORDER = (
    PermissionMode.READ_ONLY,
    PermissionMode.ASK,
    PermissionMode.AUTO,
    PermissionMode.FULL_ACCESS,
)
