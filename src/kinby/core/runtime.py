"""Boot and stop one instance runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime

from kinby.contracts import (
    INSTANCE_DRAIN,
    DrainState,
    InstanceDrainCommand,
    InstanceDrainResult,
)
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
from kinby.packages import instance_package_config


class InstanceRuntime:
    """The live state and background work of one booted instance, stopped as a whole."""

    def __init__(self, dispatcher: ScheduledDispatcher, recap: RecapWriter | None) -> None:
        self.dispatcher = dispatcher
        self.recap = recap
        self._draining: asyncio.Task[DrainState] | None = None
        self._forced = False

    @property
    def scheduler(self) -> Scheduler:
        return self.dispatcher.scheduler

    async def drain(self, command: InstanceDrainCommand) -> InstanceDrainResult:
        """Take no new work, then wait for accepted work, interrupting it when forced.

        A second call escalates the drain already running instead of starting another.
        """
        if self._draining is not None and self._draining.done():
            return InstanceDrainResult(state=self._draining.result())
        self.scheduler.close()
        if command.force:
            self._forced = True
            await self.scheduler.interrupt_all()
        if self._draining is None:
            self._draining = asyncio.create_task(self._drain())
        # Shielded: the client that asked may drop its connection, the drain still finishes.
        return InstanceDrainResult(state=await asyncio.shield(self._draining))

    async def stop_after_running_routine(self) -> None:
        """Close a session's runtime without waiting on an approval nobody is left to answer."""
        self.scheduler.close()
        await self.scheduler.stop()
        await self._settle()

    async def stop_interrupting_running_routine(self) -> None:
        """Close a process's runtime, leaving a parked approval to resume at the next start."""
        self.scheduler.close()
        await self.scheduler.stop()
        await self.scheduler.interrupt()
        await self._settle()

    async def _drain(self) -> DrainState:
        await self.scheduler.wait_idle()
        if self.recap is not None:
            await self.recap.drain()
        return DrainState.INTERRUPTED if self._forced else DrainState.DRAINED

    async def _settle(self) -> None:
        await self.scheduler.drain()
        if self.recap is not None:
            await self.recap.drain()


async def boot_instance(
    instance: Instance,
    *,
    model_override: str | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> InstanceRuntime:
    """Start one runtime that owns an instance's turns and routine fires.

    An instance whose package.yaml fails its package's validator does not boot.
    """
    instance_package_config(instance)
    event_log = EventLog(instance.manifest.state_dir)
    turns = await turn_config(instance, event_log=event_log, model_override=model_override)
    if turns.recap is not None:
        await turns.recap.catch_up()
    dispatcher = build_dispatcher(
        instance.manifest.state_dir,
        event_log=event_log,
        turns=ScheduledTurnConfig(turns, SchedulerConfig(instance, clock)),
    )
    runtime = InstanceRuntime(dispatcher, turns.recap)
    dispatcher.register(INSTANCE_DRAIN, runtime.drain)
    dispatcher.scheduler.start()
    return runtime
