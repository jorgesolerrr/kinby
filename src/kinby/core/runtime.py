"""Boot and stop one instance runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from kinby.core.clock import utc_now
from kinby.core.dispatcher import (
    ScheduledDispatcher,
    ScheduledTurnConfig,
    build_dispatcher,
    turn_config,
)
from kinby.core.events import EventLog
from kinby.core.scheduler import Scheduler, SchedulerConfig
from kinby.instance import Instance
from kinby.memory import RecapWriter


@dataclass(frozen=True)
class InstanceRuntime:
    dispatcher: ScheduledDispatcher
    recap: RecapWriter | None

    @property
    def scheduler(self) -> Scheduler:
        return self.dispatcher.scheduler

    async def stop_after_running_routine(self) -> None:
        await self.scheduler.stop()
        await self._drain()

    async def stop_interrupting_running_routine(self) -> None:
        await self.scheduler.stop()
        await self.scheduler.interrupt()
        await self._drain()

    async def _drain(self) -> None:
        await self.scheduler.drain()
        if self.recap is not None:
            await self.recap.drain()


async def boot_instance(
    instance: Instance,
    *,
    model_override: str | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> InstanceRuntime:
    """Start one runtime that owns an instance's turns and routine fires."""
    event_log = EventLog(instance.manifest.state_dir)
    turns = await turn_config(instance, event_log=event_log, model_override=model_override)
    if turns.recap is not None:
        await turns.recap.catch_up()
    dispatcher = build_dispatcher(
        instance.manifest.state_dir,
        event_log=event_log,
        turns=ScheduledTurnConfig(turns, SchedulerConfig(instance, clock)),
    )
    dispatcher.scheduler.start()
    return InstanceRuntime(dispatcher, turns.recap)
