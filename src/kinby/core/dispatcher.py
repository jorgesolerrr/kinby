"""The instance's contract methods."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import overload
from uuid import UUID, uuid4

from kinby.contracts import (
    CONFIG_HISTORY,
    CONTRACT_VERSION,
    INSTANCE_PROBE,
    MANIFEST_GET,
    MANIFEST_SET,
    MEMORY_ADD,
    MEMORY_CORRECT,
    MEMORY_FORGET,
    MEMORY_LIST,
    MEMORY_OPEN,
    PERMISSIONS_GET,
    PERMISSIONS_SET,
    PROFILE_GET,
    PROFILE_SET,
    PROMPT_GET,
    PROMPT_SET,
    ROUTINE_DELETE,
    ROUTINE_GRANT_LIST,
    ROUTINE_GRANT_REVOKE,
    ROUTINE_LIST,
    ROUTINE_READ,
    ROUTINE_RENAME,
    ROUTINE_RUN,
    ROUTINE_SET_ENABLED,
    ROUTINE_WRITE,
    RUN_STOPPED,
    SKILL_CUSTOMIZE,
    SKILL_DELETE,
    SKILL_LIST,
    SKILL_READ,
    SKILL_WRITE,
    STATS_GET,
    STEP_RUN,
    THREAD_APPROVAL_RESPOND,
    THREAD_ARCHIVE,
    THREAD_ASK,
    THREAD_CREATE,
    THREAD_LIST,
    THREAD_MODE_SET,
    THREAD_RENAME,
    THREAD_SUBSCRIBE,
    THREAD_TAKEOVER_RESUME,
    THREAD_TURN_DIFF,
    THREAD_TURN_INTERRUPT,
    THREAD_TURN_LIST,
    THREAD_TURN_RATE,
    THREAD_TURN_REVERT,
    THREAD_TURN_REVERT_PREVIEW,
    THREAD_TURN_START,
    THREAD_TURN_TARGET_LIST,
    THREAD_UNARCHIVE,
    TOOL_LIST,
    TOOL_READ_ONLY_SET,
    USAGE_GET,
    VIEW_DELETE,
    VIEW_GET,
    VIEW_LIST,
    VIEW_SUBSCRIBE,
    AcceptedResult,
    Event,
    EventType,
    InstanceProbeCommand,
    InstanceProbeResult,
    NodeId,
    PermissionMode,
    RunStop,
    RunStopped,
    StatsGetCommand,
    StatsGetResult,
    StepResult,
    StepRunCommand,
    Stream,
    ThreadArchiveCommand,
    ThreadCreateCommand,
    ThreadCreateResult,
    ThreadListCommand,
    ThreadListResult,
    ThreadRenameCommand,
    ThreadSubscribeCommand,
    ThreadSummary,
    ThreadTurnRateCommand,
    ThreadUnarchiveCommand,
    TurnRated,
    TurnStarted,
    UsageGetCommand,
    UsageGetResult,
    accepted,
    is_turn_closing,
)
from kinby.core.clock import utc_now
from kinby.core.config import InstanceConfig
from kinby.core.errors import TurnNotFound, TurnOpen
from kinby.core.events import EventLog
from kinby.core.held_results import HeldResults
from kinby.core.memory import InstanceMemory
from kinby.core.models import count_tokens
from kinby.core.pricing import SHIPPED_PRICES
from kinby.core.routing import Dispatcher, instance_capabilities
from kinby.core.scheduler import Scheduler, SchedulerConfig
from kinby.core.snapshots import SnapshotStore, WorkspaceSnapshots
from kinby.core.stats import (
    TurnRun,
    active_limits,
    plan_use,
    run_tokens,
    stats_buckets,
    stats_summary,
)
from kinby.core.steps import run_step
from kinby.core.threads import ThreadRecord, ThreadStore, thread_list, thread_summary
from kinby.core.turn_metrics import TurnKey, TurnMetricsResult, metrics_events, turn_metrics
from kinby.core.turns import TurnPreparation, TurnRunner, Turns
from kinby.core.usage import TimeRange, usage_totals
from kinby.core.views import InstanceViews
from kinby.instance import Instance, ModelPrice
from kinby.instance.permissions import SHIPPED_POLICY, GatePolicy
from kinby.memory import GraphStore, RecapWriter
from kinby.plugins.browser import BrowserService
from kinby.plugins.hub_browser import HubBrowser


@dataclass(frozen=True)
class TurnConfig:
    prepare_for_turn: Callable[[], TurnPreparation]
    permission_ceiling: Callable[[], PermissionMode]
    runner: TurnRunner
    recap: RecapWriter | None = None
    snapshots: SnapshotStore | None = None
    #: The browser the runner's turns use, which a turn ended on its takeover closes.
    browser: BrowserService | None = None


@dataclass(frozen=True)
class ScheduledTurnConfig:
    turns: TurnConfig
    scheduler: SchedulerConfig


class ScheduledDispatcher(Dispatcher):
    def __init__(self, scheduler: Scheduler) -> None:
        super().__init__()
        self._scheduler = scheduler

    @property
    def scheduler(self) -> Scheduler:
        return self._scheduler


@overload
def build_dispatcher(
    state_dir: Path,
    *,
    turns: ScheduledTurnConfig,
    event_log: EventLog | None = None,
    permissions: Callable[[], GatePolicy] = lambda: SHIPPED_POLICY,
    prices: Callable[[], Mapping[str, ModelPrice]] = lambda: SHIPPED_PRICES,
    facts: Callable[[], Collection[NodeId]] = frozenset,
    clock: Callable[[], datetime] = utc_now,
) -> ScheduledDispatcher: ...


@overload
def build_dispatcher(
    state_dir: Path,
    *,
    turns: TurnConfig | None = None,
    event_log: EventLog | None = None,
    permissions: Callable[[], GatePolicy] = lambda: SHIPPED_POLICY,
    prices: Callable[[], Mapping[str, ModelPrice]] = lambda: SHIPPED_PRICES,
    facts: Callable[[], Collection[NodeId]] = frozenset,
    clock: Callable[[], datetime] = utc_now,
) -> Dispatcher: ...


def build_dispatcher(
    state_dir: Path,
    *,
    event_log: EventLog | None = None,
    turns: TurnConfig | ScheduledTurnConfig | None = None,
    permissions: Callable[[], GatePolicy] = lambda: SHIPPED_POLICY,
    prices: Callable[[], Mapping[str, ModelPrice]] = lambda: SHIPPED_PRICES,
    facts: Callable[[], Collection[NodeId]] = frozenset,
    clock: Callable[[], datetime] = utc_now,
) -> Dispatcher:
    """The instance's contract methods."""
    store = ThreadStore(state_dir)
    event_log = event_log or EventLog(state_dir)
    scheduler = None
    turn_service = None
    turn_settings = turns.turns if isinstance(turns, ScheduledTurnConfig) else turns
    if turn_settings is not None:

        def after_turn(thread_id: UUID, turn_id: UUID) -> None:
            if turn_settings.recap is not None:
                turn_settings.recap.schedule(thread_id, turn_id)
            if scheduler is not None:
                scheduler.schedule()

        turn_service = Turns(
            store,
            event_log,
            turn_settings.runner,
            turn_settings.prepare_for_turn,
            turn_settings.permission_ceiling,
            after_turn,
            turn_settings.snapshots,
            turn_settings.browser,
        )
        if isinstance(turns, ScheduledTurnConfig):
            scheduler = Scheduler(turns.scheduler, event_log, store, turn_service)
    dispatcher = ScheduledDispatcher(scheduler) if scheduler is not None else Dispatcher()

    async def create_thread(command: ThreadCreateCommand) -> ThreadCreateResult:
        return store.create(command.title)

    async def list_threads(command: ThreadListCommand) -> ThreadListResult:
        threads, policy = store.threads(), permissions()

        def listed() -> ThreadListResult:
            with event_log.reader() as reader:
                return thread_list(threads, reader, policy, command)

        return await asyncio.to_thread(listed)

    def summary(thread: ThreadRecord) -> ThreadSummary:
        events = event_log.thread_events(thread.id)
        return thread_summary(
            thread, events, events[-1].timestamp if events else None, permissions()
        )

    async def rename_thread(command: ThreadRenameCommand) -> ThreadSummary:
        return summary(store.rename(command.thread_id, command.title))

    async def archive_thread(command: ThreadArchiveCommand) -> ThreadSummary:
        return summary(store.archive(command.thread_id))

    async def unarchive_thread(command: ThreadUnarchiveCommand) -> ThreadSummary:
        return summary(store.unarchive(command.thread_id))

    async def get_usage(command: UsageGetCommand) -> UsageGetResult:
        time_range = TimeRange(command.since, command.until)

        def totals() -> UsageGetResult:
            with event_log.reader() as reader:
                return usage_totals(metrics_events(reader, command.since), time_range)

        return await asyncio.to_thread(totals)

    async def get_stats(command: StatsGetCommand) -> StatsGetResult:
        now = clock()
        time_range = TimeRange(command.since, command.until)
        current_prices = prices()

        def read() -> tuple[TurnMetricsResult, list[Event]]:
            with event_log.reader() as reader:
                metrics = turn_metrics(metrics_events(reader, command.since), current_prices)
                return metrics, [*reader.events_of({EventType.RUN_DELEGATED})]

        metrics, delegated = await asyncio.to_thread(read)
        records = [record for record in metrics.records if time_range.includes(record.closed_at)]
        runs = [
            TurnRun(record.origin, reported)
            for record in metrics.records
            for reported in record.delegated_runs
            if time_range.includes(reported.timestamp)
        ]
        step_runs = [
            step_run
            for step_run in metrics.step_runs
            if time_range.includes(step_run.reported.timestamp)
        ]
        runs += [TurnRun(None, step_run.reported) for step_run in step_runs]
        selected_turns = {TurnKey(record.thread_id, record.turn_id) for record in records}
        return StatsGetResult(
            records=records,
            buckets=stats_buckets(records, runs, command.by),
            total=stats_summary(records, runs),
            plan_use=plan_use(delegated, now),
            limits=active_limits(delegated, now),
            unpriced_models=sorted(
                {
                    model
                    for record in records
                    for model in metrics.unpriced_models_by_turn.get(
                        TurnKey(record.thread_id, record.turn_id),
                        (),
                    )
                }
            ),
            warnings=[
                mismatch
                for mismatch in metrics.warnings
                if TurnKey(mismatch.thread_id, mismatch.turn_id) in selected_turns
            ],
            runs=run_tokens(
                records,
                step_runs,
                (recap for recap in metrics.run_recaps if time_range.includes(recap.timestamp)),
                (recall for recall in metrics.run_recalls if time_range.includes(recall.timestamp)),
                facts(),
            ),
        )

    async def rate_turn(command: ThreadTurnRateCommand) -> AcceptedResult:
        events = [
            event
            for event in event_log.turn_events(command.turn_id)
            if event.thread_id == command.thread_id
        ]
        if not any(isinstance(event.payload, TurnStarted) for event in events):
            raise TurnNotFound(
                f'Turn "{command.turn_id}" was not found on thread "{command.thread_id}".'
            )
        if not any(is_turn_closing(event.payload) for event in events):
            raise TurnOpen(f'Turn "{command.turn_id}" is still open.')
        event = await event_log.append(
            command.thread_id,
            command.turn_id,
            TurnRated(verdict=command.verdict, reason=command.reason),
        )
        return accepted(event)

    async def record_run_stop(command: RunStop) -> AcceptedResult:
        """Append the stop under its run once, however often the hub tells it."""
        recorded = next(
            (
                event
                for event in event_log.thread_events(command.run_id)
                if isinstance(event.payload, RunStopped)
                and event.payload.stop.generation == command.generation
            ),
            None,
        )
        if recorded is None:
            recorded = await event_log.append(command.run_id, uuid4(), RunStopped(stop=command))
            if turn_settings is not None and turn_settings.recap is not None:
                turn_settings.recap.schedule_run_stop(command.run_id, command.generation)
        return accepted(recorded)

    async def subscribe_to_thread(command: ThreadSubscribeCommand) -> Stream[Event]:
        return await event_log.subscribe(command.thread_id, command.after_sequence)

    config = (
        InstanceConfig(turns.scheduler.instance) if isinstance(turns, ScheduledTurnConfig) else None
    )

    async def probe(command: InstanceProbeCommand) -> InstanceProbeResult:
        return InstanceProbeResult(
            contract_version=CONTRACT_VERSION,
            capabilities=instance_capabilities(dispatcher),
        )

    dispatcher.register(THREAD_CREATE, create_thread)
    dispatcher.register(THREAD_LIST, list_threads)
    dispatcher.register(THREAD_RENAME, rename_thread)
    dispatcher.register(THREAD_ARCHIVE, archive_thread)
    dispatcher.register(THREAD_UNARCHIVE, unarchive_thread)
    dispatcher.register(USAGE_GET, get_usage)
    dispatcher.register(STATS_GET, get_stats)
    dispatcher.register(INSTANCE_PROBE, probe)
    dispatcher.register(THREAD_TURN_RATE, rate_turn)
    dispatcher.register(RUN_STOPPED, record_run_stop)
    dispatcher.register_subscription(THREAD_SUBSCRIBE, subscribe_to_thread)
    if config is not None:
        dispatcher.register(PROMPT_GET, config.get_prompt)
        dispatcher.register(PROMPT_SET, config.set_prompt)
        dispatcher.register(MANIFEST_GET, config.get_manifest)
        dispatcher.register(MANIFEST_SET, config.set_manifest)
        dispatcher.register(PERMISSIONS_GET, config.get_permissions)
        dispatcher.register(PERMISSIONS_SET, config.set_permissions)
        dispatcher.register(CONFIG_HISTORY, config.history)
        dispatcher.register(PROFILE_GET, config.get_profile)
        dispatcher.register(PROFILE_SET, config.set_profile)
        dispatcher.register(SKILL_LIST, config.list_skills)
        dispatcher.register(SKILL_READ, config.read_skill)
        dispatcher.register(SKILL_WRITE, config.write_skill)
        dispatcher.register(SKILL_CUSTOMIZE, config.customize_skill)
        dispatcher.register(SKILL_DELETE, config.delete_skill)
        dispatcher.register(TOOL_LIST, config.list_tools)
        dispatcher.register(TOOL_READ_ONLY_SET, config.set_tool_read_only)
        dispatcher.register(ROUTINE_READ, config.read_routine)
        dispatcher.register(ROUTINE_WRITE, config.write_routine)
        dispatcher.register(ROUTINE_SET_ENABLED, config.set_routine_enabled)
        dispatcher.register(ROUTINE_DELETE, config.delete_routine)
        dispatcher.register(ROUTINE_RENAME, config.rename_routine)
        dispatcher.register(ROUTINE_GRANT_LIST, config.list_routine_grants)
        dispatcher.register(ROUTINE_GRANT_REVOKE, config.revoke_routine_grant)
    if isinstance(turns, ScheduledTurnConfig) and turn_service is not None:
        step_instance = turns.scheduler.instance
        held_results = HeldResults(state_dir / "step-results")

        async def run_step_in_instance(command: StepRunCommand) -> StepResult:
            async def run() -> StepResult:
                async with turn_service.step():
                    return await run_step(command, step_instance, turn_service, event_log)

            return await held_results.answer(command, run)

        dispatcher.register(STEP_RUN, run_step_in_instance)
    if isinstance(turns, ScheduledTurnConfig):
        memory = InstanceMemory(GraphStore(turns.scheduler.instance.path), turns.scheduler.clock)
        dispatcher.register(MEMORY_LIST, memory.list)
        dispatcher.register(MEMORY_OPEN, memory.open)
        dispatcher.register(MEMORY_ADD, memory.add)
        dispatcher.register(MEMORY_CORRECT, memory.correct)
        dispatcher.register(MEMORY_FORGET, memory.forget)
        views = InstanceViews(turns.scheduler.instance)
        dispatcher.register(VIEW_LIST, views.list)
        dispatcher.register(VIEW_GET, views.get)
        dispatcher.register_subscription(VIEW_SUBSCRIBE, views.subscribe)
        dispatcher.register(VIEW_DELETE, views.delete)
    if scheduler is not None:
        dispatcher.register(ROUTINE_LIST, scheduler.list)
        dispatcher.register(ROUTINE_RUN, scheduler.run)
    if turn_service is not None:
        dispatcher.register(THREAD_TURN_START, turn_service.start)
        dispatcher.register(THREAD_ASK, turn_service.ask)
        dispatcher.register(THREAD_TURN_DIFF, turn_service.diff)
        dispatcher.register(THREAD_TURN_REVERT, turn_service.revert)
        dispatcher.register(THREAD_TURN_REVERT_PREVIEW, turn_service.preview_revert)
        dispatcher.register(THREAD_TURN_LIST, turn_service.list_turns)
        dispatcher.register(THREAD_TURN_TARGET_LIST, turn_service.list_targets)
        dispatcher.register(THREAD_MODE_SET, turn_service.set_mode)
        dispatcher.register(THREAD_TURN_INTERRUPT, turn_service.interrupt)
        dispatcher.register(THREAD_APPROVAL_RESPOND, turn_service.respond)
        dispatcher.register(THREAD_TAKEOVER_RESUME, turn_service.resume_takeover)
    return dispatcher


async def turn_config(
    instance: Instance,
    *,
    event_log: EventLog,
    model_override: str | None = None,
) -> TurnConfig:
    """Build model turns from an instance, reloading its model at each turn."""
    # LangGraph loads where an instance builds its turns, so `kinby hub` goes without it.
    from kinby.core.turn_runner import LangGraphRunner

    browser = HubBrowser()
    runner = LangGraphRunner(
        instance,
        event_log=event_log,
        model_override=model_override,
        tokenizer=count_tokens,
        browser=browser,
    )
    recap = RecapWriter(
        event_log,
        GraphStore(instance.path),
        instance,
        model_override=model_override,
    )
    snapshots = await WorkspaceSnapshots.open(
        instance.manifest.state_dir,
        instance.manifest.workspace.path,
        enabled=instance.manifest.workspace.snapshots,
    )
    return TurnConfig(
        runner.prepare_for_turn,
        runner.permission_ceiling,
        runner,
        recap,
        snapshots,
        browser,
    )
