"""Put scope and payload checks on the one path every client call crosses."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable, Collection, Mapping
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID

from pydantic import ValidationError

from kinby.contracts import (
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
from kinby.core.stats import stats_buckets
from kinby.core.threads import ThreadStore
from kinby.core.turn_metrics import turn_metrics
from kinby.core.turn_runner import LangGraphRunner
from kinby.core.turns import TurnPreparation, TurnRunner, Turns
from kinby.core.usage import TimeRange, usage_totals
from kinby.instance import Instance
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


def build_dispatcher(
    state_dir: Path,
    *,
    event_log: EventLog | None = None,
    turns: TurnConfig | None = None,
) -> Dispatcher:
    store = ThreadStore(state_dir)
    event_log = event_log or EventLog(state_dir)
    dispatcher = Dispatcher()

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
        records = [
            record
            for record in turn_metrics(event_log.all_events())
            if TimeRange(command.since, command.until).includes(record.closed_at)
        ]
        return StatsGetResult(
            records=records,
            buckets=stats_buckets(records, command.by),
            unpriced_models=[],
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
    if turns is not None:
        turn_service = Turns(
            store,
            event_log,
            turns.runner,
            turns.prepare_for_turn,
            turns.permission_ceiling,
            turns.recap.schedule if turns.recap is not None else _ignore_closed_turn,
        )
        dispatcher.register(THREAD_TURN_START, turn_service.start)
        dispatcher.register(THREAD_MODE_SET, turn_service.set_mode)
        dispatcher.register(THREAD_TURN_INTERRUPT, turn_service.interrupt)
        dispatcher.register(THREAD_APPROVAL_RESPOND, turn_service.respond)
    return dispatcher


def turn_config(
    instance: Instance,
    *,
    event_log: EventLog,
    model_override: str | None = None,
) -> TurnConfig:
    """Build model turns from an instance, reloading its model at each turn."""
    runner = LangGraphRunner(instance, model_override=model_override)
    recap = RecapWriter(
        event_log,
        GraphStore(instance.path),
        instance,
        model_override=model_override,
    )
    return TurnConfig(runner.prepare_for_turn, runner.permission_ceiling, runner, recap)


def _ignore_closed_turn(thread_id: UUID, turn_id: UUID) -> None:
    pass
