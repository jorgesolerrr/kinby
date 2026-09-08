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
    CompletionOutcome,
    ErrorCode,
    Event,
    ModelCompleted,
    ModePinned,
    Origin,
    Payload,
    PermissionMode,
    PromptVersion,
    RoutineOrigin,
    SystemPrompt,
    ThreadApprovalRespondCommand,
    ThreadModeSetCommand,
    ThreadTurnDiffCommand,
    ThreadTurnDiffResult,
    ThreadTurnInterruptCommand,
    ThreadTurnListCommand,
    ThreadTurnListResult,
    ThreadTurnRevertCommand,
    ThreadTurnRevertPreviewCommand,
    ThreadTurnRevertPreviewResult,
    ThreadTurnStartCommand,
    ThreadTurnTargetListCommand,
    ThreadTurnTargetListResult,
    TokenTotals,
    TreeId,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnStarted,
    TurnTarget,
    UserOrigin,
    WorkspaceReverted,
    accepted,
    is_turn_closing,
)
from kinby.core.budgets import DailyBudget, check_daily_budget
from kinby.core.errors import (
    ApprovalNotFound,
    CoreError,
    InstanceBusy,
    InvalidParkedTurn,
    NoActiveTurn,
    PermissionDenied,
    SnapshotUnavailable,
    ThreadBusy,
    ThreadNotFound,
    TurnInterruptedError,
    TurnNotFound,
)
from kinby.core.events import EventLog
from kinby.core.snapshots import (
    SnapshotBoundary,
    SnapshotError,
    SnapshotStore,
    snapshot_ref,
)
from kinby.core.threads import ThreadStore
from kinby.instance import Budgets
from kinby.instance.permissions import constrain_mode, exceeds_ceiling

logger = logging.getLogger(__name__)

# How long an interrupt lets a cancelled runner finish writing before it snapshots.
_SETTLE_SECONDS = 5.0


@dataclass(frozen=True)
class TurnRequest:
    thread_id: UUID
    turn_id: UUID
    message: str
    model: str
    permission_mode: PermissionMode
    origin: Origin = field(default_factory=UserOrigin)

    def prepare(self, system_prompt: SystemPrompt) -> PreparedTurnRequest:
        return PreparedTurnRequest(
            thread_id=self.thread_id,
            turn_id=self.turn_id,
            message=self.message,
            model=self.model,
            permission_mode=self.permission_mode,
            origin=self.origin,
            system_prompt=system_prompt,
        )


@dataclass(frozen=True, kw_only=True)
class PreparedTurnRequest(TurnRequest):
    system_prompt: SystemPrompt


@dataclass(frozen=True)
class TurnPreparation:
    model: str
    default_mode: PermissionMode
    ceiling: PermissionMode
    prompt_version: PromptVersion
    system_prompt: SystemPrompt
    daily_budget: DailyBudget | None = None
    budgets: Budgets = field(default_factory=Budgets)


@dataclass(frozen=True)
class TurnOutcome:
    input_tokens: int = 0
    output_tokens: int = 0
    outcome: CompletionOutcome = CompletionOutcome.WORK


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


def _no_token_usage() -> TokenTotals:
    return TokenTotals(input_tokens=0, output_tokens=0)


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
    async def restore(self, thread_id: UUID, turn_id: UUID) -> PreparedTurnRequest | None: ...
    async def run(self, turn: PreparedTurnRequest, context: TurnContext, /) -> TurnResult: ...
    async def resume(
        self,
        turn: PreparedTurnRequest,
        decision: ApprovalDecision,
        context: TurnContext,
        /,
    ) -> TurnResult: ...


class ClosedTurnHook(Protocol):
    def __call__(self, thread_id: UUID, turn_id: UUID) -> None: ...


@dataclass
class RunningTurn:
    request: PreparedTurnRequest
    task: asyncio.Task[None]
    interrupted: bool = False
    has_model_calls: bool = False
    usage: TokenTotals = field(default_factory=_no_token_usage)


@dataclass(frozen=True)
class TurnClaim:
    origin: Origin = field(default_factory=UserOrigin)


@dataclass(frozen=True)
class RevertibleSnapshots:
    """A recorded starting snapshot that can be restored."""

    before: TreeId


@dataclass(frozen=True)
class DiffableSnapshots:
    """Recorded starting and closing snapshots that can be compared."""

    before: TreeId
    after: TreeId


type RecordedSnapshots = RevertibleSnapshots | DiffableSnapshots


@dataclass(frozen=True)
class RevertClaim:
    """Held while a revert preview or restore accesses the shared workspace."""


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
        snapshots: SnapshotStore | None = None,
    ) -> None:
        self._store = store
        self._log = log
        self._runner = runner
        self._prepare_for_turn = prepare_for_turn
        self._permission_ceiling = permission_ceiling
        self._after_turn = after_turn
        self._snapshots = snapshots
        self._changed = asyncio.Event()
        self._running: dict[UUID, RunningTurn] = {}
        self._claims: dict[UUID, TurnClaim | InterruptedTurnClaim | RevertClaim] = {}

    def running(self) -> tuple[Origin, ...]:
        origins = {
            thread: running.request.origin
            for thread, running in self._running.items()
            if not running.task.done()
        }
        for thread, claim in self._claims.items():
            if isinstance(claim, TurnClaim):
                origins[thread] = claim.origin
        events_by_thread: dict[UUID, list[Event]] = {
            thread.id: [] for thread in self._store.list().threads
        }
        for event in self._log.all_events():
            if event.thread_id in events_by_thread:
                events_by_thread[event.thread_id].append(event)
        for thread_id, events in events_by_thread.items():
            pending = _pending_approval(events)
            if pending is not None:
                origins[thread_id] = _turn_origin(events, pending.event.turn_id)
        return tuple(origins.values())

    def require_available(self, origin: Origin) -> None:
        if any(isinstance(claim, RevertClaim) for claim in self._claims.values()):
            raise InstanceBusy("A revert is running. Wait for it to finish before starting a turn.")
        running = self.running()
        routine = next((item for item in running if isinstance(item, RoutineOrigin)), None)
        if routine is not None:
            raise InstanceBusy(f'Routine "{routine.name}" is running. Waiting for it to finish.')
        if running and isinstance(origin, RoutineOrigin):
            raise InstanceBusy(f'Routine "{origin.name}" is waiting for the running user turn.')

    async def wait_idle(self) -> None:
        while True:
            self._changed.clear()
            if not self.running():
                return
            await self._changed.wait()

    async def drain(self) -> None:
        tasks = [running.task for running in self._running.values()]
        if tasks:
            await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))

    async def interrupt_routine(self) -> None:
        for thread_id, running in tuple(self._running.items()):
            if not running.task.done() and isinstance(running.request.origin, RoutineOrigin):
                await self.interrupt(ThreadTurnInterruptCommand(thread_id=thread_id))
                return

    async def set_mode(self, command: ThreadModeSetCommand) -> AcceptedResult:
        self._require_thread(command.thread_id)
        events = self._log.stored(command.thread_id)
        running = self._running.get(command.thread_id)
        active = running is not None and not running.task.done()
        if command.thread_id in self._claims or active or _pending_approval(events) is not None:
            raise _thread_busy(command.thread_id)
        ceiling = self._permission_ceiling()
        if exceeds_ceiling(command.mode, ceiling):
            raise PermissionDenied(
                f'Permission mode "{command.mode.value}" exceeds the instance ceiling '
                f'"{ceiling.value}".'
            )
        event = await self._log.append(command.thread_id, uuid4(), ModePinned(mode=command.mode))
        return accepted(event)

    async def start(self, command: ThreadTurnStartCommand) -> AcceptedResult:
        return await self.wake(command.thread_id, command.message, UserOrigin())

    async def diff(self, command: ThreadTurnDiffCommand) -> ThreadTurnDiffResult:
        self._require_thread(command.thread_id)
        recorded = self._recorded_snapshots(command.thread_id, command.turn_id)
        if self._snapshots is None or not isinstance(recorded, DiffableSnapshots):
            raise _snapshot_unavailable(command.turn_id)
        try:
            difference = await self._snapshots.diff(recorded.before, recorded.after)
        except SnapshotError as exc:
            raise _snapshot_unavailable(command.turn_id) from exc
        return ThreadTurnDiffResult(
            turn_id=command.turn_id,
            before=recorded.before,
            after=recorded.after,
            files=difference.files,
            patch=difference.patch,
        )

    async def preview_revert(
        self,
        command: ThreadTurnRevertPreviewCommand,
    ) -> ThreadTurnRevertPreviewResult:
        self._require_thread(command.thread_id)
        claim = self._claim_revert(command.thread_id)
        try:
            recorded = self._recorded_snapshots(command.thread_id, command.turn_id)
            if self._snapshots is None or recorded is None:
                raise _snapshot_unavailable(command.turn_id)
            try:
                difference = await self._snapshots.preview_restore(recorded.before)
            except SnapshotError as exc:
                raise _snapshot_unavailable(command.turn_id) from exc
        finally:
            self._release_claim(command.thread_id, claim)
        return ThreadTurnRevertPreviewResult(
            turn_id=command.turn_id,
            files=difference.files,
        )

    async def revert(self, command: ThreadTurnRevertCommand) -> AcceptedResult:
        self._require_thread(command.thread_id)
        claim = self._claim_revert(command.thread_id)
        try:
            recorded = self._recorded_snapshots(command.thread_id, command.turn_id)
            snapshots = self._snapshots
            if snapshots is None or recorded is None:
                raise _snapshot_unavailable(command.turn_id)
            revert_id = uuid4()
            try:
                previous = await snapshots.capture(
                    snapshot_ref(command.thread_id, revert_id, SnapshotBoundary.BEFORE)
                )
                await snapshots.restore(recorded.before)
            except SnapshotError as exc:
                raise _snapshot_unavailable(command.turn_id) from exc
            # The work tree now holds the target's before tree, so that is what was
            # restored even when capturing the after ref fails. The revert stays
            # recorded either way, or it could not itself be reverted.
            captured = await self._capture(command.thread_id, revert_id, SnapshotBoundary.AFTER)
            restored = recorded.before if captured is None else captured
            event = await self._log.append(
                command.thread_id,
                revert_id,
                WorkspaceReverted(
                    target_turn_id=command.turn_id,
                    previous=previous,
                    restored=restored,
                ),
            )
            return accepted(event)
        finally:
            self._release_claim(command.thread_id, claim)

    def _claim_revert(self, thread_id: UUID) -> RevertClaim:
        # The workspace is shared by every thread, so a revert under any running
        # turn would change the files out from under it.
        if self.running() or self._claims:
            raise InstanceBusy("A turn is running. Wait for it to finish before reverting.")
        claim = RevertClaim()
        self._claims[thread_id] = claim
        return claim

    def _recorded_snapshots(self, thread_id: UUID, turn_id: UUID) -> RecordedSnapshots | None:
        """The snapshots recorded for a turn or a revert, or None if it started without one."""
        payloads = [
            event.payload for event in self._log.stored(thread_id) if event.turn_id == turn_id
        ]
        reverted = next(
            (payload for payload in payloads if isinstance(payload, WorkspaceReverted)),
            None,
        )
        if reverted is not None:
            return DiffableSnapshots(reverted.previous, reverted.restored)
        started = next(
            (payload for payload in payloads if isinstance(payload, TurnStarted)),
            None,
        )
        if started is None:
            raise TurnNotFound(f'Turn "{turn_id}" was not found on thread "{thread_id}".')
        closed = next((payload for payload in payloads if is_turn_closing(payload)), None)
        if closed is None:
            raise _thread_busy(thread_id)
        if started.snapshot is None:
            return None
        if closed.snapshot is None:
            return RevertibleSnapshots(started.snapshot)
        return DiffableSnapshots(started.snapshot, closed.snapshot)

    async def list_turns(self, command: ThreadTurnListCommand) -> ThreadTurnListResult:
        """Every turn on the thread, in order."""
        self._require_thread(command.thread_id)
        return ThreadTurnListResult(
            turn_ids=list(
                dict.fromkeys(
                    event.turn_id
                    for event in self._log.stored(command.thread_id)
                    if isinstance(event.payload, TurnStarted)
                )
            )
        )

    async def list_targets(
        self,
        command: ThreadTurnTargetListCommand,
    ) -> ThreadTurnTargetListResult:
        """Every turn target on the thread, in order, and whether each has closed."""
        self._require_thread(command.thread_id)
        closed: dict[UUID, bool] = {}
        for event in self._log.stored(command.thread_id):
            if isinstance(event.payload, TurnStarted):
                closed.setdefault(event.turn_id, False)
            elif isinstance(event.payload, WorkspaceReverted) or is_turn_closing(event.payload):
                closed[event.turn_id] = True
        return ThreadTurnTargetListResult(
            targets=[TurnTarget(turn_id=turn_id, closed=done) for turn_id, done in closed.items()]
        )

    async def wake(
        self,
        thread_id: UUID,
        message: str,
        origin: Origin,
    ) -> AcceptedResult:
        self._require_thread(thread_id)
        self.require_available(origin)
        events = self._log.stored(thread_id)
        pending = _pending_approval(events)
        running = self._running.get(thread_id)
        active = running is not None and not running.task.done()
        if thread_id in self._claims or active or pending is not None:
            raise _thread_busy(thread_id)

        return await self._wake(lambda: thread_id, message, origin, uuid4())

    async def wake_recorded(
        self,
        thread_id: UUID,
        turn_id: UUID,
        message: str,
        origin: RoutineOrigin,
    ) -> AcceptedResult:
        self._require_thread(thread_id)
        self.require_available(origin)
        return await self._wake(lambda: thread_id, message, origin, turn_id)

    async def wake_new_thread(self, title: str, message: str, origin: Origin) -> AcceptedResult:
        self.require_available(origin)
        return await self._wake(lambda: self._store.create(title).id, message, origin, uuid4())

    async def _wake(
        self,
        thread: Callable[[], UUID],
        message: str,
        origin: Origin,
        turn_id: UUID,
    ) -> AcceptedResult:
        preparation = self._prepare_for_turn()
        check_daily_budget(preparation.daily_budget)
        thread_id = thread()
        events = self._log.stored(thread_id)
        claim = TurnClaim(origin)
        self._claims[thread_id] = claim
        try:
            turn = PreparedTurnRequest(
                thread_id=thread_id,
                turn_id=turn_id,
                message=message,
                model=preparation.model,
                origin=origin,
                permission_mode=_permission_mode(
                    events,
                    preparation.default_mode,
                    preparation.ceiling,
                ),
                system_prompt=preparation.system_prompt,
            )
            snapshot = await self._capture(
                turn.thread_id,
                turn.turn_id,
                SnapshotBoundary.BEFORE,
            )
            started = await self._log.append(
                turn.thread_id,
                turn.turn_id,
                TurnStarted(
                    message=turn.message,
                    origin=turn.origin,
                    model=turn.model,
                    permission_mode=turn.permission_mode,
                    prompt_version=preparation.prompt_version,
                    snapshot=snapshot,
                ),
            )
        finally:
            self._release_claim(thread_id, claim)
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
            if running is not None:
                # The workspace only stops changing once the cancelled task is done,
                # but ADR 0008 keeps an interrupt from waiting on a runner that
                # suppresses cancellation, so the snapshot gives up its accuracy
                # rather than the command's return.
                await asyncio.wait((running.task,), timeout=_SETTLE_SECONDS)
            usage = (
                running.usage
                if running is not None
                else _recorded_model_usage(self._log.stored(command.thread_id), turn_id)
            )
            interrupted = await self._log.append(
                command.thread_id,
                turn_id,
                TurnInterrupted(
                    snapshot=await self._capture(
                        command.thread_id,
                        turn_id,
                        SnapshotBoundary.AFTER,
                    ),
                    **usage.model_dump(),
                ),
            )
            self._schedule_after_turn(command.thread_id, turn_id)
        finally:
            self._release_claim(command.thread_id, claim)
        if running is not None and self._running.get(command.thread_id) is running:
            del self._running[command.thread_id]
        return accepted(interrupted)

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
        origin = _turn_origin(events, pending.event.turn_id)
        # The parked turn itself owns the instance reservation.
        claim = TurnClaim(origin)
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
                permission_mode=constrain_mode(
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
        claim: TurnClaim | InterruptedTurnClaim | RevertClaim,
    ) -> None:
        if self._claims.get(thread_id) is claim:
            del self._claims[thread_id]
        self._changed.set()

    def _spawn(self, turn: PreparedTurnRequest, work: Coroutine[object, object, None]) -> None:
        task = asyncio.create_task(work)
        model_calls = _recorded_model_calls(self._log.stored(turn.thread_id), turn.turn_id)
        self._running[turn.thread_id] = RunningTurn(
            turn,
            task,
            has_model_calls=bool(model_calls),
            usage=_sum_model_calls(model_calls),
        )
        task.add_done_callback(partial(self._forget_task, turn.thread_id))

    def _owned_running_turn(self, turn: PreparedTurnRequest) -> RunningTurn | None:
        running = self._running.get(turn.thread_id)
        if running is None or running.request.turn_id != turn.turn_id or running.interrupted:
            return None
        return running

    async def _run(self, turn: PreparedTurnRequest, budgets: Budgets) -> None:
        await self._finish(turn, budgets, lambda context: self._runner.run(turn, context))

    async def _resume(
        self,
        turn: PreparedTurnRequest,
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
        turn: PreparedTurnRequest,
        budgets: Budgets,
        run: Callable[[TurnContext], Awaitable[TurnResult]],
    ) -> None:
        async def emit(payload: Payload) -> Event:
            running = self._owned_running_turn(turn)
            if running is None:
                raise TurnInterruptedError
            event = await self._log.append(turn.thread_id, turn.turn_id, payload)
            if isinstance(payload, ModelCompleted):
                running.has_model_calls = True
                running.usage = _sum_model_calls((running.usage, payload))
            return event

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
            running = self._owned_running_turn(turn)
            if running is None:
                return
            if outcome.outcome is CompletionOutcome.NO_WORK:
                usage = _no_token_usage()
            elif running.has_model_calls:
                usage = running.usage
            else:
                usage = TokenTotals(
                    input_tokens=outcome.input_tokens,
                    output_tokens=outcome.output_tokens,
                )
            await emit(
                TurnCompleted(
                    outcome=outcome.outcome,
                    snapshot=await self._capture(
                        turn.thread_id,
                        turn.turn_id,
                        SnapshotBoundary.AFTER,
                    ),
                    **usage.model_dump(),
                )
            )
            self._schedule_after_turn(turn.thread_id, turn.turn_id)
            return

        running = self._owned_running_turn(turn)
        if running is None:
            return
        usage = running.usage
        await emit(
            TurnFailed(
                code=code,
                message=message,
                snapshot=await self._capture(
                    turn.thread_id,
                    turn.turn_id,
                    SnapshotBoundary.AFTER,
                ),
                **usage.model_dump(),
            )
        )
        self._schedule_after_turn(turn.thread_id, turn.turn_id)

    async def _capture(
        self,
        thread_id: UUID,
        turn_id: UUID,
        boundary: SnapshotBoundary,
    ) -> TreeId | None:
        """Snapshot the workspace, or record nothing when the snapshot cannot be taken."""
        if self._snapshots is None:
            return None
        try:
            return await self._snapshots.capture(snapshot_ref(thread_id, turn_id, boundary))
        except SnapshotError:
            logger.warning("The workspace snapshot could not be taken.", exc_info=True)
            return None

    def _schedule_after_turn(self, thread_id: UUID, turn_id: UUID) -> None:
        try:
            self._after_turn(thread_id, turn_id)
        except Exception:
            logger.exception("The turn recap could not be scheduled.")

    def _forget_task(self, thread_id: UUID, task: asyncio.Task[None]) -> None:
        self._changed.set()
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


def _turn_origin(events: Sequence[Event], turn_id: UUID) -> Origin:
    return next(
        event.payload.origin
        for event in events
        if event.turn_id == turn_id and isinstance(event.payload, TurnStarted)
    )


def _sum_model_calls(calls: Sequence[TokenTotals]) -> TokenTotals:
    return TokenTotals(
        input_tokens=sum(call.input_tokens for call in calls),
        output_tokens=sum(call.output_tokens for call in calls),
        cache_read_tokens=sum(call.cache_read_tokens for call in calls),
        cache_creation_tokens=sum(call.cache_creation_tokens for call in calls),
    )


def _recorded_model_calls(events: Sequence[Event], turn_id: UUID) -> list[ModelCompleted]:
    return [
        event.payload
        for event in events
        if event.turn_id == turn_id and isinstance(event.payload, ModelCompleted)
    ]


def _recorded_model_usage(events: Sequence[Event], turn_id: UUID) -> TokenTotals:
    return _sum_model_calls(_recorded_model_calls(events, turn_id))


def _pending_approval(events: Sequence[Event]) -> PendingApproval | None:
    # Answering an approval appends nothing, so the resumed turn's own next event is
    # what ends the park. Events on any other turn leave it standing.
    for index, event in reversed(list(enumerate(events))):
        payload = event.payload
        if not isinstance(payload, ApprovalRequested):
            continue
        if any(later.turn_id == event.turn_id for later in events[index + 1 :]):
            return None
        return PendingApproval(event, payload)
    return None


def _permission_mode(
    events: Sequence[Event],
    default: PermissionMode,
    ceiling: PermissionMode,
) -> PermissionMode:
    mode = next(
        (event.payload.mode for event in reversed(events) if isinstance(event.payload, ModePinned)),
        default,
    )
    return constrain_mode(mode, ceiling)


def _thread_busy(thread_id: UUID) -> ThreadBusy:
    return ThreadBusy(f'Thread "{thread_id}" already has a running turn.')


def _snapshot_unavailable(turn_id: UUID) -> SnapshotUnavailable:
    return SnapshotUnavailable(f'Workspace snapshots are unavailable for turn "{turn_id}".')


def _no_active_turn(thread_id: UUID) -> NoActiveTurn:
    return NoActiveTurn(f'Thread "{thread_id}" has no active turn.')
