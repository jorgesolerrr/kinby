"""Put scope and payload checks on the one path every client call crosses."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable, Collection, Mapping
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path
from typing import cast, overload
from uuid import UUID

from pydantic import ValidationError

from kinby.contracts import (
    ROUTINE_LIST,
    ROUTINE_RUN,
    STATS_GET,
    THREAD_APPROVAL_RESPOND,
    THREAD_CREATE,
    THREAD_LIST,
    THREAD_MODE_SET,
    THREAD_SUBSCRIBE,
    THREAD_TURN_INTERRUPT,
    THREAD_TURN_RATE,
    THREAD_TURN_START,
    USAGE_GET,
    AcceptedResult,
    ContractModel,
    ErrorCode,
    ErrorEnvelope,
    Event,
    Method,
    PermissionMode,
    Scope,
    StatsGetCommand,
    StatsGetResult,
    Subscription,
    ThreadCreateCommand,
    ThreadCreateResult,
    ThreadListCommand,
    ThreadListResult,
    ThreadSubscribeCommand,
    ThreadTurnRateCommand,
    TurnRated,
    TurnStarted,
    UsageGetCommand,
    UsageGetResult,
    is_turn_closing,
)
from kinby.core.errors import CoreError, TurnNotFound, TurnOpen
from kinby.core.events import EventLog
from kinby.core.pricing import price_map
from kinby.core.scheduler import Scheduler, SchedulerConfig
from kinby.core.snapshots import SnapshotStore, WorkspaceSnapshots
from kinby.core.stats import stats_buckets
from kinby.core.threads import ThreadStore
from kinby.core.turn_metrics import TurnKey, turn_metrics
from kinby.core.turn_runner import LangGraphRunner
from kinby.core.turns import TurnPreparation, TurnRunner, Turns
from kinby.core.usage import TimeRange, usage_totals
from kinby.instance import Instance, ModelPrice
from kinby.memory import GraphStore, RecapWriter

Handler = Callable[[ContractModel], Awaitable[ContractModel]]
SubscriptionHandler = Callable[[ContractModel], AsyncGenerator[ContractModel]]


@dataclass(frozen=True)
class Route[RouteHandler]:
    scope: Scope
    command: type[ContractModel]
    handler: RouteHandler


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
        self._routes[method.name] = Route(method.scope, method.command, cast(Handler, handler))

    def register_subscription[Command: ContractModel, Item: ContractModel](
        self,
        subscription: Subscription[Command, Item],
        handler: Callable[[Command], AsyncGenerator[Item]],
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
        if route.scope not in scopes:
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
            return ErrorEnvelope(
                code=exc.code,
                message=str(exc),
                retryable=exc.retryable,
            )
        except Exception:
            return ErrorEnvelope(
                code=ErrorCode.INTERNAL,
                message="The method failed unexpectedly.",
                retryable=False,
            )

    async def subscribe(
        self,
        method: str,
        payload: Mapping[str, object],
        scopes: Collection[Scope],
    ) -> AsyncGenerator[ContractModel]:
        call = self._validate_call(self._subscription_routes, method, payload, scopes)
        if isinstance(call, ErrorEnvelope):
            yield call
            return
        route, command = call
        try:
            async with aclosing(route.handler(command)) as subscription:
                async for event in subscription:
                    yield event
        except Exception:
            yield ErrorEnvelope(
                code=ErrorCode.INTERNAL,
                message="The subscription failed unexpectedly.",
                retryable=False,
            )


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
    price_overrides: Mapping[str, ModelPrice] | None = None,
) -> ScheduledDispatcher: ...


@overload
def build_dispatcher(
    state_dir: Path,
    *,
    turns: TurnConfig | None = None,
    event_log: EventLog | None = None,
    price_overrides: Mapping[str, ModelPrice] | None = None,
) -> Dispatcher: ...


def build_dispatcher(
    state_dir: Path,
    *,
    event_log: EventLog | None = None,
    turns: TurnConfig | ScheduledTurnConfig | None = None,
    price_overrides: Mapping[str, ModelPrice] | None = None,
) -> Dispatcher:
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
        return store.list()

    async def get_usage(command: UsageGetCommand) -> UsageGetResult:
        return usage_totals(
            event_log.all_events(),
            TimeRange(command.since, command.until),
        )

    async def get_stats(command: StatsGetCommand) -> StatsGetResult:
        metrics = turn_metrics(event_log.all_events(), prices)
        time_range = TimeRange(command.since, command.until)
        records = [record for record in metrics.records if time_range.includes(record.closed_at)]
        selected_turns = {TurnKey(record.thread_id, record.turn_id) for record in records}
        return StatsGetResult(
            records=records,
            buckets=stats_buckets(records, command.by),
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
        return AcceptedResult(
            thread_id=event.thread_id,
            turn_id=event.turn_id,
            sequence=event.sequence,
        )

    def subscribe_to_thread(command: ThreadSubscribeCommand) -> AsyncGenerator[Event]:
        return event_log.subscribe(command.thread_id, command.after_sequence)

    dispatcher.register(THREAD_CREATE, create_thread)
    dispatcher.register(THREAD_LIST, list_threads)
    dispatcher.register(USAGE_GET, get_usage)
    dispatcher.register(STATS_GET, get_stats)
    dispatcher.register(THREAD_TURN_RATE, rate_turn)
    dispatcher.register_subscription(THREAD_SUBSCRIBE, subscribe_to_thread)
    if scheduler is not None:
        dispatcher.register(ROUTINE_LIST, scheduler.list)
        dispatcher.register(ROUTINE_RUN, scheduler.run)
    if turn_service is not None:
        dispatcher.register(THREAD_TURN_START, turn_service.start)
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
