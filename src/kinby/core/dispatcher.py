"""Put scope and payload checks on the one path every client call crosses."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable, Collection, Mapping
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast, overload
from uuid import UUID

from pydantic import ValidationError

from kinby.contracts import (
    CONFIG_HISTORY,
    CONTRACT_VERSION,
    INSTANCE_DRAIN,
    INSTANCE_PROBE,
    MANIFEST_GET,
    MANIFEST_SET,
    MEMORY_ADD,
    MEMORY_CORRECT,
    MEMORY_FORGET,
    MEMORY_LIST,
    MEMORY_OPEN,
    PACKAGE_CONFIG_GET,
    PACKAGE_CONFIG_SET,
    PERMISSIONS_GET,
    PERMISSIONS_SET,
    PROFILE_GET,
    PROFILE_SET,
    PROMPT_GET,
    PROMPT_SET,
    ROUTINE_DELETE,
    ROUTINE_LIST,
    ROUTINE_READ,
    ROUTINE_RENAME,
    ROUTINE_RUN,
    ROUTINE_SET_ENABLED,
    ROUTINE_WRITE,
    SKILL_CUSTOMIZE,
    SKILL_DELETE,
    SKILL_LIST,
    SKILL_READ,
    SKILL_WRITE,
    STATS_GET,
    STEP_RUN,
    THREAD_APPROVAL_RESPOND,
    THREAD_ARCHIVE,
    THREAD_CREATE,
    THREAD_LIST,
    THREAD_MODE_SET,
    THREAD_RENAME,
    THREAD_SUBSCRIBE,
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
    USAGE_GET,
    AcceptedResult,
    Capability,
    ContractModel,
    ErrorCode,
    ErrorEnvelope,
    Event,
    FileHash,
    InstanceProbeCommand,
    InstanceProbeResult,
    Method,
    PermissionMode,
    Scope,
    StatsGetCommand,
    StatsGetResult,
    StepResult,
    StepRunCommand,
    Stream,
    Subscription,
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
from kinby.core.errors import CoreError, TurnNotFound, TurnOpen
from kinby.core.events import EventLog
from kinby.core.memory import InstanceMemory
from kinby.core.pricing import price_map
from kinby.core.scheduler import Scheduler, SchedulerConfig
from kinby.core.snapshots import SnapshotStore, WorkspaceSnapshots
from kinby.core.stats import TurnRun, active_limits, plan_use, stats_buckets, stats_summary
from kinby.core.steps import run_step
from kinby.core.threads import ThreadRecord, ThreadStore, thread_list, thread_summary
from kinby.core.turn_metrics import TurnKey, turn_metrics
from kinby.core.turn_runner import LangGraphRunner
from kinby.core.turns import TurnPreparation, TurnRunner, Turns
from kinby.core.usage import TimeRange, usage_totals
from kinby.instance import Instance, ModelPrice
from kinby.instance.permissions import SHIPPED_POLICY, GatePolicy
from kinby.memory import GraphStore, RecapWriter

Handler = Callable[[ContractModel], Awaitable[ContractModel]]
SubscriptionHandler = Callable[[ContractModel], Awaitable[Stream[ContractModel]]]
_SUBSCRIPTION_FAILED = ErrorEnvelope(
    code=ErrorCode.INTERNAL,
    message="The subscription failed unexpectedly.",
    retryable=False,
)


async def _guarded(items: AsyncGenerator[ContractModel]) -> AsyncGenerator[ContractModel]:
    """Turn a failure mid-stream into a last item, so a subscriber always sees why it ended."""
    try:
        async with aclosing(items):
            async for item in items:
                yield item
    except Exception:
        yield _SUBSCRIPTION_FAILED


@dataclass(frozen=True)
class Route[RouteHandler]:
    scope: Scope
    command: type[ContractModel]
    handler: RouteHandler
    alternative_scope: Scope | None = None

    def admits(self, scopes: Collection[Scope]) -> bool:
        return self.scope in scopes or self.alternative_scope in scopes


@dataclass(frozen=True)
class TurnConfig:
    prepare_for_turn: Callable[[], TurnPreparation]
    permission_ceiling: Callable[[], PermissionMode]
    runner: TurnRunner
    recap: RecapWriter | None = None
    snapshots: SnapshotStore | None = None


@dataclass(frozen=True)
class ScheduledTurnConfig:
    turns: TurnConfig
    scheduler: SchedulerConfig


class Dispatcher:
    def __init__(self) -> None:
        self._routes: dict[str, Route[Handler]] = {}
        self._subscription_routes: dict[str, Route[SubscriptionHandler]] = {}

    def register[Command: ContractModel, Result: ContractModel](
        self,
        method: Method[Command, Result],
        handler: Callable[[Command], Awaitable[Result]],
    ) -> None:
        self._routes[method.name] = Route(
            method.scope,
            method.command,
            cast(Handler, handler),
            method.alternative_scope,
        )

    def register_subscription[Command: ContractModel, Item: ContractModel](
        self,
        subscription: Subscription[Command, Item],
        handler: Callable[[Command], Awaitable[Stream[Item]]],
    ) -> None:
        self._subscription_routes[subscription.name] = Route(
            subscription.scope,
            subscription.command,
            cast(SubscriptionHandler, handler),
        )

    @staticmethod
    def _validate_call[RouteHandler](
        routes: Mapping[str, Route[RouteHandler]],
        method: str,
        payload: Mapping[str, object],
        scopes: Collection[Scope],
    ) -> tuple[Route[RouteHandler], ContractModel] | ErrorEnvelope:
        route = routes.get(method)
        if route is None:
            return ErrorEnvelope(
                code=ErrorCode.NOT_FOUND,
                message=f'Method "{method}" was not found.',
                retryable=False,
            )
        if not route.admits(scopes):
            return ErrorEnvelope(
                code=ErrorCode.PERMISSION_DENIED,
                message=f'Missing required scope "{route.scope.value}".',
                retryable=False,
            )
        try:
            command = route.command.model_validate(payload)
        except ValidationError as exc:
            return ErrorEnvelope(
                code=ErrorCode.INVALID_ARGUMENT,
                message=f'Invalid payload for "{method}": {exc}',
                retryable=False,
            )
        return route, command

    async def dispatch(
        self,
        method: str,
        payload: Mapping[str, object],
        scopes: Collection[Scope],
    ) -> ContractModel:
        call = self._validate_call(self._routes, method, payload, scopes)
        if isinstance(call, ErrorEnvelope):
            return call
        route, command = call
        try:
            return await route.handler(command)
        except CoreError as exc:
            return exc.envelope()
        except Exception:
            return ErrorEnvelope(
                code=ErrorCode.INTERNAL,
                message="The method failed unexpectedly.",
                retryable=False,
            )

    def handles[Command: ContractModel, Result: ContractModel](
        self,
        method: Method[Command, Result],
    ) -> bool:
        return method.name in self._routes

    async def subscribe(
        self,
        method: str,
        payload: Mapping[str, object],
        scopes: Collection[Scope],
    ) -> Stream[ContractModel] | ErrorEnvelope:
        call = self._validate_call(self._subscription_routes, method, payload, scopes)
        if isinstance(call, ErrorEnvelope):
            return call
        route, command = call
        try:
            stream = await route.handler(command)
        except Exception:
            return _SUBSCRIPTION_FAILED
        return Stream(stream.head_sequence, _guarded(stream.items), _close=stream._close)


def instance_capabilities(dispatcher: Dispatcher) -> list[Capability]:
    """What a hub may ask of this instance: the socket, plus the lifecycle methods it serves."""
    return [Capability.WS, *([Capability.DRAIN] if dispatcher.handles(INSTANCE_DRAIN) else [])]


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
    price_overrides: Mapping[str, ModelPrice] | None = None,
    clock: Callable[[], datetime] = utc_now,
    booted_package_config: FileHash | None = None,
) -> ScheduledDispatcher: ...


@overload
def build_dispatcher(
    state_dir: Path,
    *,
    turns: TurnConfig | None = None,
    event_log: EventLog | None = None,
    permissions: Callable[[], GatePolicy] = lambda: SHIPPED_POLICY,
    price_overrides: Mapping[str, ModelPrice] | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> Dispatcher: ...


def build_dispatcher(
    state_dir: Path,
    *,
    event_log: EventLog | None = None,
    turns: TurnConfig | ScheduledTurnConfig | None = None,
    permissions: Callable[[], GatePolicy] = lambda: SHIPPED_POLICY,
    price_overrides: Mapping[str, ModelPrice] | None = None,
    clock: Callable[[], datetime] = utc_now,
    booted_package_config: FileHash | None = None,
) -> Dispatcher:
    """The instance's contract methods. *booted_package_config* is the hash of the package.yaml
    the instance validated at boot, which the probe compares with the file on disk.
    """
    store = ThreadStore(state_dir)
    event_log = event_log or EventLog(state_dir)
    prices = price_map(price_overrides)
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
        )
        if isinstance(turns, ScheduledTurnConfig):
            scheduler = Scheduler(turns.scheduler, event_log, store, turn_service)
    dispatcher = ScheduledDispatcher(scheduler) if scheduler is not None else Dispatcher()

    async def create_thread(command: ThreadCreateCommand) -> ThreadCreateResult:
        return store.create(command.title)

    async def list_threads(command: ThreadListCommand) -> ThreadListResult:
        return thread_list(store.threads(), event_log.all_events(), permissions(), command)

    def summary(thread: ThreadRecord) -> ThreadSummary:
        return thread_summary(thread, event_log.stored(thread.id), permissions())

    async def rename_thread(command: ThreadRenameCommand) -> ThreadSummary:
        return summary(store.rename(command.thread_id, command.title))

    async def archive_thread(command: ThreadArchiveCommand) -> ThreadSummary:
        return summary(store.archive(command.thread_id))

    async def unarchive_thread(command: ThreadUnarchiveCommand) -> ThreadSummary:
        return summary(store.unarchive(command.thread_id))

    async def get_usage(command: UsageGetCommand) -> UsageGetResult:
        return usage_totals(
            event_log.all_events(),
            TimeRange(command.since, command.until),
        )

    async def get_stats(command: StatsGetCommand) -> StatsGetResult:
        events = list(event_log.all_events())
        now = clock()
        metrics = turn_metrics(events, prices)
        time_range = TimeRange(command.since, command.until)
        records = [record for record in metrics.records if time_range.includes(record.closed_at)]
        runs = [
            TurnRun(record.origin, reported)
            for record in metrics.records
            for reported in record.delegated_runs
            if time_range.includes(reported.timestamp)
        ] + [
            TurnRun(None, reported)
            for reported in metrics.runs_outside_turns
            if time_range.includes(reported.timestamp)
        ]
        selected_turns = {TurnKey(record.thread_id, record.turn_id) for record in records}
        return StatsGetResult(
            records=records,
            buckets=stats_buckets(records, runs, command.by),
            total=stats_summary(records, runs),
            plan_use=plan_use(events, now),
            limits=active_limits(events, now),
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
        )

    async def rate_turn(command: ThreadTurnRateCommand) -> AcceptedResult:
        events = [
            event
            for event in event_log.stored(command.thread_id)
            if event.turn_id == command.turn_id
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

    async def subscribe_to_thread(command: ThreadSubscribeCommand) -> Stream[Event]:
        return await event_log.subscribe(command.thread_id, command.after_sequence)

    config = (
        InstanceConfig(turns.scheduler.instance, booted_package_config)
        if isinstance(turns, ScheduledTurnConfig)
        else None
    )

    async def probe(command: InstanceProbeCommand) -> InstanceProbeResult:
        return InstanceProbeResult(
            contract_version=CONTRACT_VERSION,
            capabilities=instance_capabilities(dispatcher),
            restart_reasons=config.restart_reasons() if config is not None else [],
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
    dispatcher.register_subscription(THREAD_SUBSCRIBE, subscribe_to_thread)
    if config is not None:
        dispatcher.register(PROMPT_GET, config.get_prompt)
        dispatcher.register(PROMPT_SET, config.set_prompt)
        dispatcher.register(MANIFEST_GET, config.get_manifest)
        dispatcher.register(MANIFEST_SET, config.set_manifest)
        dispatcher.register(PERMISSIONS_GET, config.get_permissions)
        dispatcher.register(PERMISSIONS_SET, config.set_permissions)
        dispatcher.register(PACKAGE_CONFIG_GET, config.get_package_config)
        dispatcher.register(PACKAGE_CONFIG_SET, config.set_package_config)
        dispatcher.register(CONFIG_HISTORY, config.history)
        dispatcher.register(PROFILE_GET, config.get_profile)
        dispatcher.register(PROFILE_SET, config.set_profile)
        dispatcher.register(SKILL_LIST, config.list_skills)
        dispatcher.register(SKILL_READ, config.read_skill)
        dispatcher.register(SKILL_WRITE, config.write_skill)
        dispatcher.register(SKILL_CUSTOMIZE, config.customize_skill)
        dispatcher.register(SKILL_DELETE, config.delete_skill)
        dispatcher.register(TOOL_LIST, config.list_tools)
        dispatcher.register(ROUTINE_READ, config.read_routine)
        dispatcher.register(ROUTINE_WRITE, config.write_routine)
        dispatcher.register(ROUTINE_SET_ENABLED, config.set_routine_enabled)
        dispatcher.register(ROUTINE_DELETE, config.delete_routine)
        dispatcher.register(ROUTINE_RENAME, config.rename_routine)
    if isinstance(turns, ScheduledTurnConfig) and turn_service is not None:
        step_instance = turns.scheduler.instance

        async def run_step_in_instance(command: StepRunCommand) -> StepResult:
            async with turn_service.step():
                return await run_step(command, step_instance, turn_service, event_log)

        dispatcher.register(STEP_RUN, run_step_in_instance)
    if isinstance(turns, ScheduledTurnConfig):
        memory = InstanceMemory(GraphStore(turns.scheduler.instance.path), turns.scheduler.clock)
        dispatcher.register(MEMORY_LIST, memory.list)
        dispatcher.register(MEMORY_OPEN, memory.open)
        dispatcher.register(MEMORY_ADD, memory.add)
        dispatcher.register(MEMORY_CORRECT, memory.correct)
        dispatcher.register(MEMORY_FORGET, memory.forget)
    if scheduler is not None:
        dispatcher.register(ROUTINE_LIST, scheduler.list)
        dispatcher.register(ROUTINE_RUN, scheduler.run)
    if turn_service is not None:
        dispatcher.register(THREAD_TURN_START, turn_service.start)
        dispatcher.register(THREAD_TURN_DIFF, turn_service.diff)
        dispatcher.register(THREAD_TURN_REVERT, turn_service.revert)
        dispatcher.register(THREAD_TURN_REVERT_PREVIEW, turn_service.preview_revert)
        dispatcher.register(THREAD_TURN_LIST, turn_service.list_turns)
        dispatcher.register(THREAD_TURN_TARGET_LIST, turn_service.list_targets)
        dispatcher.register(THREAD_MODE_SET, turn_service.set_mode)
        dispatcher.register(THREAD_TURN_INTERRUPT, turn_service.interrupt)
        dispatcher.register(THREAD_APPROVAL_RESPOND, turn_service.respond)
    return dispatcher


async def turn_config(
    instance: Instance,
    *,
    event_log: EventLog,
    model_override: str | None = None,
) -> TurnConfig:
    """Build model turns from an instance, reloading its model at each turn."""
    runner = LangGraphRunner(
        instance,
        event_log=event_log,
        model_override=model_override,
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
    )
