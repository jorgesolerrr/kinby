"""Schedule routine wakes from their files and canonical turn history."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime

from cronsim import CronSim

from kinby.contracts import (
    AcceptedResult,
    CronSchedule,
    RoutineFailureHandled,
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
    SignalSummary,
)
from kinby.core.errors import BudgetExceeded, InstanceBusy, ModelUnpriced, RoutineNotFound
from kinby.core.events import EventLog
from kinby.core.routine_history import RoutineHistory, routine_history
from kinby.core.turns import Turns
from kinby.instance import Instance
from kinby.plugins.routines import Routine, disable_routine, load_routines


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class SchedulerConfig:
    instance: Instance
    clock: Callable[[], datetime] = utc_now


@dataclass(frozen=True)
class ArmedRoutine:
    schedule: CronSchedule
    time: datetime


class Scheduler:
    def __init__(
        self,
        config: SchedulerConfig,
        log: EventLog,
        turns: Turns,
    ) -> None:
        self._instance = config.instance
        self._clock = config.clock
        self._started_at = self._clock()
        self._log = log
        self._turns = turns
        self._armed: dict[RoutineName, ArmedRoutine] = {}
        self._pass = asyncio.Lock()
        self._changed = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        routines, _ = load_routines(self._instance)
        history = routine_history(self._log.all_events()).routines
        self._armed = self._arm(routines, history)

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

    async def interrupt(self) -> None:
        await self._turns.interrupt_routine()

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

    def _arm(
        self, routines: Sequence[Routine], history: Mapping[RoutineName, RoutineHistory]
    ) -> dict[RoutineName, ArmedRoutine]:
        armed = {}
        for routine in routines:
            if not routine.enabled or not routine.schedule:
                continue
            current = self._armed.get(routine.name)
            if current is None or current.schedule != routine.schedule:
                last = history.get(routine.name, RoutineHistory()).last_run
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
        history = routine_history(self._log.all_events()).routines
        armed = self._arm(routines, history)
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
                    next_run=armed[routine.name].time if routine.name in armed else None,
                    signal=(
                        SignalSummary(
                            path=f"/signals/{routine.name}",
                            auth=routine.signal.auth,
                        )
                        if routine.signal is not None
                        else None
                    ),
                    pending=0,
                )
            )
        return RoutineListResult(routines=result, warnings=warnings)

    async def run(self, command: RoutineRunCommand) -> AcceptedResult:
        routines, _ = load_routines(self._instance)
        routine = next((r for r in routines if r.name == command.name), None)
        if routine is None:
            raise RoutineNotFound(f'Routine "{command.name}" was not found.')
        return await self._fire(routine, RoutineTrigger.MANUAL)

    async def _fire(self, routine: Routine, trigger: RoutineTrigger) -> AcceptedResult:
        now = self._clock()
        self._turns.require_available(RoutineOrigin(name=routine.name, trigger=trigger))
        accepted = await self._turns.wake_new_thread(
            f"{routine.name} · {now.isoformat()}",
            routine.prompt,
            RoutineOrigin(name=routine.name, trigger=trigger),
        )
        if routine.enabled and routine.schedule:
            self._armed[routine.name] = ArmedRoutine(
                routine.schedule, self._next(routine.schedule, now)
            )
        return accepted

    async def tick(self) -> None:
        await self._turns.wait_idle()
        async with self._pass:
            await self._handle_failures()
            routines, _ = load_routines(self._instance)
            history = routine_history(self._log.all_events()).routines
            self._armed = self._arm(routines, history)
            for routine in routines:
                armed = self._armed.get(routine.name)
                if armed is not None and armed.time <= self._clock():
                    trigger = (
                        RoutineTrigger.CATCH_UP
                        if armed.time < self._started_at
                        else RoutineTrigger.SCHEDULED
                    )
                    if self._turns.running():
                        return
                    try:
                        await self._fire(routine, trigger)
                    except InstanceBusy:
                        return
                    except BudgetExceeded, ModelUnpriced:
                        self._armed[routine.name] = ArmedRoutine(
                            armed.schedule, self._next(armed.schedule, self._clock())
                        )
                        continue
                    await self._turns.drain()
                    await self._handle_failures()

    async def _handle_failures(self) -> None:
        history = routine_history(self._log.all_events())
        routines, _ = load_routines(self._instance)
        by_name = {routine.name: routine for routine in routines}
        for failure in history.failures:
            notice = None
            routine = by_name.get(failure.name)
            if failure.count >= 10 and routine is not None and routine.enabled:
                disable_routine(routine)
                by_name.pop(failure.name)
                notice = RoutineNotice(
                    kind=RoutineNoticeKind.DISABLED,
                    message=f'Routine "{failure.name}" disabled after '
                    f"{failure.count} failed firings: {failure.reason}",
                    thread_id=failure.event.thread_id,
                    turn_id=failure.event.turn_id,
                )
            elif failure.count == 1:
                notice = RoutineNotice(
                    kind=RoutineNoticeKind.FIRST_FAILURE,
                    message=f'Routine "{failure.name}" failed: {failure.reason}',
                    thread_id=failure.event.thread_id,
                    turn_id=failure.event.turn_id,
                )
            await self._log.append(
                failure.event.thread_id,
                failure.event.turn_id,
                RoutineFailureHandled(name=failure.name, notice=notice),
            )

    async def drain(self) -> None:
        async with self._pass:
            await self._turns.drain()
            await self._handle_failures()
