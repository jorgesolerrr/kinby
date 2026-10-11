import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from kinby.contracts import (
    AcceptedResult,
    CompletionOutcome,
    RoutineName,
    RoutineOrigin,
    RoutineTrigger,
    Scope,
    ThreadCreateResult,
    ThreadListResult,
    ThreadSummary,
    UserOrigin,
)
from kinby.core.dispatcher import (
    ScheduledDispatcher,
    ScheduledTurnConfig,
    TurnConfig,
    build_dispatcher,
)
from kinby.core.scheduler import SchedulerConfig
from kinby.core.turns import Emit, ParkedTurn, PreparedTurnRequest, TurnOutcome
from tests.helpers import fixed_permission_ceiling, fixed_turn_preparation
from tests.test_routines import instance_at, routine_file
from tests.test_scheduler import FakeClock
from tests.test_thread_status import ScriptedRunner


class RoutineRunner(ScriptedRunner):
    """Run each turn as its message says, and find no work in a message of "nothing new"."""

    async def run(self, turn: PreparedTurnRequest, emit: Emit) -> TurnOutcome | ParkedTurn:
        if turn.message == "nothing new":
            return TurnOutcome(outcome=CompletionOutcome.NO_WORK)
        return await super().run(turn, emit)


async def _setup(tmp_path: Path, *routines: str) -> tuple[ScheduledDispatcher, FakeClock]:
    """A dispatcher over an instance with one routine per message, named after it."""
    instance = instance_at(tmp_path)
    for message in routines:
        name = message.replace(" ", "-")
        routine_file(instance, "description: Scripted\nschedule: 0 9 * * *", message, name=name)
    clock = FakeClock(datetime(2026, 9, 6, 8, 59, tzinfo=UTC))
    dispatcher = build_dispatcher(
        instance.manifest.state_dir,
        turns=ScheduledTurnConfig(
            TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, RoutineRunner()),
            SchedulerConfig(instance, clock),
        ),
    )
    await dispatcher.scheduler.tick()
    return dispatcher, clock


async def _fire(dispatcher: ScheduledDispatcher, name: str) -> UUID:
    """Run the routine by hand and wait for its turn to end or park."""
    accepted = await dispatcher.dispatch("routine.run", {"name": name}, set(Scope))
    assert isinstance(accepted, AcceptedResult)
    await dispatcher.scheduler.drain()
    return accepted.thread_id


async def _deliver(dispatcher: ScheduledDispatcher, name: str, body: str) -> UUID:
    """Deliver a payload to the routine and wait for the run it starts."""
    accepted = await dispatcher.dispatch(
        "routine.run",
        {"name": name, "payload": {"body": body, "content_type": "text/plain"}},
        set(Scope),
    )
    assert isinstance(accepted, AcceptedResult)
    await dispatcher.scheduler.tick()
    await dispatcher.scheduler.drain()
    return accepted.thread_id


async def _reply(dispatcher: ScheduledDispatcher, thread_id: UUID, message: str) -> None:
    """Start the user's turn in the thread and wait for it to end."""
    accepted = await dispatcher.dispatch(
        "thread.turn.start",
        {"thread_id": thread_id, "message": message},
        {Scope.THREAD_OPERATE},
    )
    assert isinstance(accepted, AcceptedResult)
    await dispatcher.scheduler.drain()


async def _create(dispatcher: ScheduledDispatcher) -> ThreadCreateResult:
    created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
    assert isinstance(created, ThreadCreateResult)
    return created


async def _threads(dispatcher: ScheduledDispatcher, thread_filter: str) -> list[ThreadSummary]:
    listed = await dispatcher.dispatch(
        "thread.list", {"filter": thread_filter}, {Scope.THREAD_READ}
    )
    assert isinstance(listed, ThreadListResult)
    return listed.threads


async def _listed(dispatcher: ScheduledDispatcher, thread_filter: str = "sidebar") -> list[UUID]:
    return [thread.id for thread in await _threads(dispatcher, thread_filter)]


async def _archive(dispatcher: ScheduledDispatcher, thread_id: UUID) -> None:
    archived = await dispatcher.dispatch(
        "thread.archive", {"thread_id": thread_id}, {Scope.THREAD_OPERATE}
    )
    assert isinstance(archived, ThreadSummary)


def test_a_thread_summary_names_its_origin(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher, _ = await _setup(tmp_path, "done")
        conversation = await _create(dispatcher)
        run = await _fire(dispatcher, "done")

        origins = {thread.id: thread.origin for thread in await _threads(dispatcher, "all")}

        assert origins[conversation.id] == UserOrigin()
        assert origins[run] == RoutineOrigin(
            name=RoutineName("done"), trigger=RoutineTrigger.MANUAL
        )

    asyncio.run(scenario())


def test_a_thread_just_created_is_in_the_sidebar(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher, _ = await _setup(tmp_path)

        created = await _create(dispatcher)

        assert await _listed(dispatcher) == [created.id]

    asyncio.run(scenario())


@pytest.mark.parametrize("message", ["done", "nothing new"])
def test_a_quiet_routine_run_stays_out_of_the_sidebar(tmp_path: Path, message: str) -> None:
    async def scenario() -> None:
        dispatcher, _ = await _setup(tmp_path, message)
        conversation = await _create(dispatcher)

        run = await _fire(dispatcher, message.replace(" ", "-"))

        assert await _listed(dispatcher) == [conversation.id]
        assert await _listed(dispatcher, "all") == [run, conversation.id]

    asyncio.run(scenario())


def test_a_scheduled_run_follows_the_same_rule(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher, clock = await _setup(tmp_path, "done", "fail")

        for _ in range(2):
            clock.now = datetime(2026, 9, 6, 9, 1, tzinfo=UTC)
            await dispatcher.scheduler.tick()
            await dispatcher.scheduler.drain()

        runs = {thread.title: thread.id for thread in await _threads(dispatcher, "all")}
        assert await _listed(dispatcher) == [runs["fail · 2026-09-06 09:01 UTC"]]

    asyncio.run(scenario())


def test_a_delivered_run_follows_the_same_rule(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher, _ = await _setup(tmp_path, "done")

        quiet = await _deliver(dispatcher, "done", "done")
        parked = await _deliver(dispatcher, "done", "park")

        assert await _listed(dispatcher) == [parked]
        assert await _listed(dispatcher, "all") == [parked, quiet]

    asyncio.run(scenario())


def test_a_routine_run_awaiting_approval_is_in_the_sidebar_archived_or_not(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        dispatcher, _ = await _setup(tmp_path, "park")
        run = await _fire(dispatcher, "park")

        assert await _listed(dispatcher) == [run]
        await _archive(dispatcher, run)
        assert await _listed(dispatcher) == [run]

    asyncio.run(scenario())


def test_a_failed_routine_run_stays_in_the_sidebar_until_it_is_archived(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        dispatcher, _ = await _setup(tmp_path, "fail")
        failed = await _fire(dispatcher, "fail")

        await _deliver(dispatcher, "fail", "done")

        assert await _listed(dispatcher) == [failed]
        await _archive(dispatcher, failed)
        assert await _listed(dispatcher) == []

    asyncio.run(scenario())


def test_a_routine_run_the_user_started_a_turn_in_stays_in_the_sidebar(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        dispatcher, _ = await _setup(tmp_path, "done")
        run = await _fire(dispatcher, "done")

        await _reply(dispatcher, run, "done")

        assert await _listed(dispatcher) == [run]
        [summary] = await _threads(dispatcher, "sidebar")
        assert summary.origin == RoutineOrigin(
            name=RoutineName("done"), trigger=RoutineTrigger.MANUAL
        )

    asyncio.run(scenario())
