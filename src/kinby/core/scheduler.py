"""Schedule routine wakes from their files and canonical turn history."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import uuid4

from cronsim import CronSim

from kinby.contracts import (
    AcceptedResult,
    ConfigActor,
    ConfigFile,
    CronSchedule,
    Delivery,
    NotificationRaised,
    RoutineFailureHandled,
    RoutineFailuresReset,
    RoutineListCommand,
    RoutineListResult,
    RoutineName,
    RoutineNotice,
    RoutineNoticeKind,
    RoutineOrigin,
    RoutineRunCommand,
    RoutineRunOutcome,
    RoutineSummary,
    RoutineTrigger,
    SignalReceived,
    SignalSummary,
    accepted,
)
from kinby.core.clock import utc_now
from kinby.core.errors import (
    BudgetExceeded,
    InstanceBusy,
    InstanceDraining,
    ModelUnpriced,
    RoutineNotFound,
)
from kinby.core.events import EventLog
from kinby.core.routine_history import RoutineHistory, routine_history
from kinby.core.threads import ThreadStore
from kinby.core.turns import Turns
from kinby.instance import Instance, ManifestError, inspect_instance
from kinby.instance.config_changes import recorded_change
from kinby.instance.layout import ROUTINES_DIR
from kinby.plugins.notify import pending_notifications, raise_recorded, raises_notifications
from kinby.plugins.routines import Routine, disable_routine, load_routine, load_routines

#: The failed firings in a row after which the failure policy disables a routine.
DISABLE_AFTER = 10


@dataclass(frozen=True)
class SchedulerConfig:
    instance: Instance
    clock: Callable[[], datetime] = utc_now


@dataclass(frozen=True)
class ArmedRoutine:
    schedule: CronSchedule
    time: datetime


@dataclass(frozen=True)
class DeliveryReceipt:
    accepted: AcceptedResult
    repeated: bool


class Scheduler:
    def __init__(
        self,
        config: SchedulerConfig,
        log: EventLog,
        store: ThreadStore,
        turns: Turns,
    ) -> None:
        self._instance = config.instance
        self._clock = config.clock
        self._started_at = self._clock()
        self._log = log
        self._store = store
        self._turns = turns
        self._armed: dict[RoutineName, ArmedRoutine] = {}
        self._pass = asyncio.Lock()
        self._changed = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        routines, _ = load_routines(self._instance)
        history = routine_history(self._log.all_events()).routines
        self._armed = self._arm(routines, catch_up_from=history)

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._work())

    def schedule(self) -> None:
        self._changed.set()

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None

    def close(self) -> None:
        """Take no new turns and fire no more routines. Work already accepted keeps going."""
        self._turns.close()

    async def interrupt(self) -> None:
        await self._turns.interrupt_routine()

    async def interrupt_all(self) -> None:
        """Interrupt every live turn and parked approval: only a force stop asks for this."""
        await self._turns.interrupt_all()

    async def receive(
        self,
        name: RoutineName,
        delivery: Delivery,
        trigger: RoutineTrigger,
    ) -> DeliveryReceipt:
        async with self._instance.routine_lock:
            if load_routine(self._instance, name) is None:
                raise RoutineNotFound(f'Routine "{name}" was not found.')
            return await self._receive(name, delivery, trigger)

    async def _receive(
        self,
        name: RoutineName,
        delivery: Delivery,
        trigger: RoutineTrigger,
    ) -> DeliveryReceipt:
        if delivery.delivery_id is not None:
            for event in self._log.all_events():
                payload = event.payload
                if (
                    isinstance(payload, SignalReceived)
                    and payload.origin.name == name
                    and payload.delivery.delivery_id == delivery.delivery_id
                ):
                    return DeliveryReceipt(accepted(event), repeated=True)
        origin = RoutineOrigin(
            name=name,
            trigger=trigger,
            delivery_id=delivery.delivery_id,
        )
        title_suffix = delivery.delivery_id or self._local_minute(delivery.received_at)
        thread = self._store.create(f"{name} · {title_suffix}")
        event = await self._log.append(
            thread.id,
            uuid4(),
            SignalReceived(origin=origin, delivery=delivery),
        )
        self.schedule()
        return DeliveryReceipt(accepted(event), repeated=False)

    async def _work(self) -> None:
        while True:
            self._changed.clear()
            try:
                await self.tick()
            except Exception:
                logging.getLogger(__name__).exception("The scheduler pass failed.")
                delay = 60.0
            else:
                delay = min(
                    (
                        max(0.0, (armed.time - self._clock()).total_seconds())
                        for armed in self._armed.values()
                    ),
                    default=60.0,
                )
                delay = min(delay, 60.0)
            with suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), timeout=delay)

    def _next(self, schedule: CronSchedule, after: datetime) -> datetime:
        zone = self._instance.manifest.routines.timezone
        return next(CronSim(schedule, after.astimezone(zone))).astimezone(UTC)

    def _local_minute(self, time: datetime) -> str:
        zone = self._instance.manifest.routines.timezone
        return f"{time.astimezone(zone):%Y-%m-%d %H:%M %Z}"

    def _arm(
        self,
        routines: Sequence[Routine],
        catch_up_from: Mapping[RoutineName, RoutineHistory],
    ) -> dict[RoutineName, ArmedRoutine]:
        armed = {}
        for routine in routines:
            if not routine.enabled or not routine.schedule:
                continue
            current = self._armed.get(routine.name)
            if current is None or current.schedule != routine.schedule:
                last = catch_up_from.get(routine.name, RoutineHistory()).last_run
                can_catch_up = (
                    last is not None
                    and last.outcome is not RoutineRunOutcome.INTERRUPTED
                    and routine.catch_up
                )
                after = last.started_at if can_catch_up else self._clock()
                current = ArmedRoutine(routine.schedule, self._next(routine.schedule, after))
            armed[routine.name] = current
        return armed

    async def list(self, command: RoutineListCommand) -> RoutineListResult:
        routines, warnings = load_routines(self._instance)
        await self._reset_failures(routines)
        history = routine_history(self._log.all_events()).routines
        self._armed = self._arm(routines, catch_up_from={})
        result = []
        for routine in routines:
            record = history.get(routine.name, RoutineHistory())
            last = record.last_run
            result.append(
                RoutineSummary(
                    name=routine.name,
                    description=routine.description,
                    schedule=routine.schedule,
                    enabled=routine.enabled,
                    mode=routine.mode,
                    last_run=last,
                    failure_count=record.failure_count,
                    last_failure=record.last_failure,
                    notices=record.notices,
                    next_run=(
                        self._armed[routine.name].time if routine.name in self._armed else None
                    ),
                    signal=(
                        SignalSummary(
                            path=f"/signals/{routine.name}",
                            auth=routine.signal.auth,
                        )
                        if routine.signal is not None
                        else None
                    ),
                    pending=len(record.pending),
                )
            )
        return RoutineListResult(routines=result, warnings=warnings)

    async def run(self, command: RoutineRunCommand) -> AcceptedResult:
        # Before the payload is persisted: a manual run is new work, whatever it carries.
        self._turns.require_admitting()
        routines, _ = load_routines(self._instance)
        routine = next((r for r in routines if r.name == command.name), None)
        if routine is None:
            raise RoutineNotFound(f'Routine "{command.name}" was not found.')
        await self._reset_failures([routine])
        if command.payload is not None:
            receipt = await self.receive(
                routine.name,
                Delivery(
                    headers={"content-type": command.payload.content_type},
                    content_type=command.payload.content_type,
                    body=command.payload.body,
                    received_at=self._clock(),
                ),
                RoutineTrigger.MANUAL,
            )
            return receipt.accepted
        return await self._fire(routine, RoutineTrigger.MANUAL)

    async def _fire(self, routine: Routine, trigger: RoutineTrigger) -> AcceptedResult:
        now = self._clock()
        origin = RoutineOrigin(
            name=routine.name, trigger=trigger, dreaming_stage=routine.is_dreaming_stage
        )
        self._turns.require_available(origin)
        accepted = await self._turns.wake_new_thread(
            f"{routine.name} · {self._local_minute(now)}", routine.prompt, origin
        )
        if routine.enabled and routine.schedule:
            self._armed[routine.name] = ArmedRoutine(
                routine.schedule, self._next(routine.schedule, now)
            )
        return accepted

    def _reread_manifest(self) -> None:
        """Take the routines timezone from kinby.toml as it is now, or keep the last valid one."""
        try:
            manifest = inspect_instance(
                self._instance.path, matching_rule=self._instance.matching_rule
            ).manifest
        except ManifestError:
            return
        if manifest.routines != self._instance.manifest.routines:
            # Every armed time was read in the old zone.
            self._armed = {}
        self._instance = replace(self._instance, manifest=manifest)

    async def tick(self) -> None:
        await self._turns.wait_idle()
        async with self._pass:
            self._reread_manifest()
            await self._handle_failures()
            await self._raise_notifications()
            routines, _ = load_routines(self._instance)
            histories = routine_history(self._log.all_events())
            history = histories.routines
            self._armed = self._arm(routines, catch_up_from={})
            disabled = {routine.name for routine in routines if not routine.enabled}
            pending = [
                item
                for record in history.values()
                for item in record.pending
                if item.origin.name not in disabled or item.origin.trigger is RoutineTrigger.MANUAL
            ]
            delivery = min(pending, key=lambda item: item.receipt_order) if pending else None
            due = [
                (armed.time, routine, armed)
                for routine in routines
                if (armed := self._armed.get(routine.name)) is not None
                and armed.time <= self._clock()
            ]
            scheduled = min(due, key=lambda item: item[0]) if due else None
            if delivery is None and scheduled is None:
                return
            if self._turns.holding():
                return
            if delivery is not None and (
                scheduled is None or delivery.delivery.received_at <= scheduled[0]
            ):
                stage = any(
                    routine.name == delivery.origin.name and routine.is_dreaming_stage
                    for routine in routines
                )
                origin = delivery.origin.model_copy(
                    update={"trigger": RoutineTrigger.SIGNAL, "dreaming_stage": stage}
                )
                firing = self._turns.wake_recorded(
                    delivery.thread_id,
                    delivery.turn_id,
                    delivery.delivery.body,
                    origin,
                )
                rearm = None
            elif scheduled is not None:
                _, routine, armed = scheduled
                trigger = (
                    RoutineTrigger.CATCH_UP
                    if armed.time < self._started_at
                    else RoutineTrigger.SCHEDULED
                )
                firing = self._fire(routine, trigger)
                rearm = routine.name, armed
            else:
                return
            try:
                await firing
            except InstanceBusy, InstanceDraining:
                return
            except BudgetExceeded, ModelUnpriced:
                if rearm is not None:
                    name, armed = rearm
                    self._armed[name] = ArmedRoutine(
                        armed.schedule, self._next(armed.schedule, self._clock())
                    )
                return
            await self._turns.drain()
            await self._handle_failures()
            await self._raise_notifications()

    async def _raise_notifications(self) -> None:
        """Raise each notification the hub has not answered, until one finds it out of reach."""
        for recorded in pending_notifications(self._log.all_events()):
            answer = await raise_recorded(recorded)
            if answer is None:
                return
            await self._log.append(recorded.thread_id, recorded.turn_id, answer)

    async def _reset_failures(self, routines: Sequence[Routine]) -> None:
        history = routine_history(self._log.all_events()).routines
        for routine in routines:
            notice = history.get(routine.name, RoutineHistory()).disable_notice
            if routine.enabled and notice is not None:
                await self._log.append(
                    notice.thread_id, notice.turn_id, RoutineFailuresReset(name=routine.name)
                )

    async def _handle_failures(self) -> None:
        routines, _ = load_routines(self._instance)
        await self._reset_failures(routines)
        history = routine_history(self._log.all_events())
        by_name = {routine.name: routine for routine in routines}
        for failure in history.failures:
            notice = notification = None
            routine = by_name.get(failure.name)
            if failure.count >= DISABLE_AFTER and routine is not None and routine.enabled:
                with recorded_change(
                    self._instance,
                    ConfigFile(f"{ROUTINES_DIR}/{failure.name}"),
                    ConfigActor.FAILURE_POLICY,
                    thread_id=failure.event.thread_id,
                    turn_id=failure.event.turn_id,
                ):
                    disable_routine(routine)
                by_name.pop(failure.name)
                notice = RoutineNotice(
                    kind=RoutineNoticeKind.DISABLED,
                    message=f'Routine "{failure.name}" disabled after '
                    f"{failure.count} failed firings: {failure.reason}",
                    thread_id=failure.event.thread_id,
                    turn_id=failure.event.turn_id,
                )
                notification = NotificationRaised(
                    source=failure.name,
                    title=f'Routine "{failure.name}" disabled',
                    text=f"It failed {failure.count} times in a row, so kinby turned it off.",
                )
            elif failure.count == 1:
                notice = RoutineNotice(
                    kind=RoutineNoticeKind.FIRST_FAILURE,
                    message=f'Routine "{failure.name}" failed: {failure.reason}',
                    thread_id=failure.event.thread_id,
                    turn_id=failure.event.turn_id,
                )
                notification = NotificationRaised(
                    source=failure.name,
                    title=f'Routine "{failure.name}" failed',
                    text=f"kinby disables it after {DISABLE_AFTER} failures in a row.",
                )
            await self._log.append(
                failure.event.thread_id,
                failure.event.turn_id,
                RoutineFailureHandled(name=failure.name, notice=notice),
            )
            if notification is not None and raises_notifications():
                await self._log.append(failure.event.thread_id, failure.event.turn_id, notification)

    async def drain(self) -> None:
        async with self._pass:
            await self._turns.drain()
            await self._handle_failures()

    async def wait_idle(self) -> None:
        """Wait for every live turn, including a resumed approval, to finish, then take no more
        approval answers. A parked approval stays parked.

        Holds no lock while it waits, so a force stop can still escalate a closed instance.
        """
        await self._turns.seal()
        await self.stop()
        await self.drain()
