import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from kinby.contracts import (
    AcceptedResult,
    ErrorCode,
    ErrorEnvelope,
    Event,
    EventType,
    Scope,
    ThreadCreateResult,
    ThreadListResult,
    ThreadSummary,
)
from kinby.core.dispatcher import Dispatcher, TurnConfig, build_dispatcher
from tests.helpers import fixed_permission_ceiling, fixed_turn_preparation, thread_events
from tests.test_thread_status import ScriptedRunner


def _dispatcher(tmp_path: Path) -> Dispatcher:
    return build_dispatcher(
        tmp_path,
        turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, ScriptedRunner()),
    )


async def _create(dispatcher: Dispatcher) -> ThreadCreateResult:
    created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
    assert isinstance(created, ThreadCreateResult)
    return created


async def _put(dispatcher: Dispatcher, method: str, thread_id: UUID) -> ThreadSummary:
    summary = await dispatcher.dispatch(method, {"thread_id": thread_id}, {Scope.THREAD_OPERATE})
    assert isinstance(summary, ThreadSummary)
    return summary


async def _archive(dispatcher: Dispatcher, thread_id: UUID) -> ThreadSummary:
    return await _put(dispatcher, "thread.archive", thread_id)


async def _unarchive(dispatcher: Dispatcher, thread_id: UUID) -> ThreadSummary:
    return await _put(dispatcher, "thread.unarchive", thread_id)


async def _listed(dispatcher: Dispatcher, thread_filter: str | None = None) -> list[UUID]:
    command = {} if thread_filter is None else {"filter": thread_filter}
    listed = await dispatcher.dispatch("thread.list", command, {Scope.THREAD_READ})
    assert isinstance(listed, ThreadListResult)
    return [thread.id for thread in listed.threads]


async def _run(dispatcher: Dispatcher, thread_id: UUID, message: str, closing: EventType) -> None:
    """Start a turn and follow the thread until the event of type ``closing`` arrives."""
    accepted = await dispatcher.dispatch(
        "thread.turn.start",
        {"thread_id": thread_id, "message": message},
        {Scope.THREAD_OPERATE},
    )
    assert isinstance(accepted, AcceptedResult)
    subscription = await thread_events(dispatcher, {"thread_id": thread_id})
    try:
        while True:
            event = await asyncio.wait_for(anext(subscription), timeout=1)
            assert isinstance(event, Event)
            if event.type is closing:
                return
    finally:
        await subscription.aclose()


def test_an_archived_thread_leaves_the_sidebar_and_stays_in_all(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(tmp_path)
        kept = await _create(dispatcher)
        put_away = await _create(dispatcher)

        archived = await _archive(dispatcher, put_away.id)

        assert (archived.id, archived.archived) == (put_away.id, True)
        assert await _listed(dispatcher) == [kept.id]
        assert await _listed(dispatcher, "sidebar") == [kept.id]
        assert await _listed(dispatcher, "all") == [put_away.id, kept.id]
        assert await _listed(dispatcher, "archived") == [put_away.id]

    asyncio.run(scenario())


def test_an_unarchived_thread_returns_to_the_sidebar(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(tmp_path)
        created = await _create(dispatcher)
        await _archive(dispatcher, created.id)

        unarchived = await _unarchive(dispatcher, created.id)

        assert unarchived.archived is False
        assert await _listed(dispatcher) == [created.id]
        assert await _listed(dispatcher, "archived") == []

    asyncio.run(scenario())


def test_archive_and_unarchive_are_idempotent(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(tmp_path)
        created = await _create(dispatcher)

        unarchived = await _unarchive(dispatcher, created.id)
        await _archive(dispatcher, created.id)
        archived = await _archive(dispatcher, created.id)

        assert unarchived.archived is False
        assert archived.archived is True
        assert await _listed(dispatcher, "archived") == [created.id]

    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["thread.archive", "thread.unarchive"])
def test_archiving_an_unknown_thread_is_not_found(tmp_path: Path, method: str) -> None:
    thread_id = uuid4()

    refused = asyncio.run(
        build_dispatcher(tmp_path).dispatch(
            method, {"thread_id": thread_id}, {Scope.THREAD_OPERATE}
        )
    )

    assert refused == ErrorEnvelope(
        code=ErrorCode.NOT_FOUND, message=f'Thread "{thread_id}" was not found.', retryable=False
    )


@pytest.mark.parametrize("method", ["thread.archive", "thread.unarchive"])
def test_archiving_needs_the_operate_scope(tmp_path: Path, method: str) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(tmp_path)
        created = await _create(dispatcher)

        refused = await dispatcher.dispatch(method, {"thread_id": created.id}, {Scope.THREAD_READ})

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.PERMISSION_DENIED

    asyncio.run(scenario())


def test_an_archived_thread_awaiting_approval_stays_in_the_sidebar(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        created = await _create(dispatcher)
        await _run(dispatcher, created.id, "park", EventType.APPROVAL_REQUESTED)

        await _archive(dispatcher, created.id)

        assert await _listed(dispatcher) == [created.id]
        assert await _listed(dispatcher, "archived") == [created.id]

    asyncio.run(scenario())


def test_an_archived_failed_thread_leaves_the_sidebar(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        created = await _create(dispatcher)
        await _run(dispatcher, created.id, "fail", EventType.TURN_FAILED)

        await _archive(dispatcher, created.id)

        assert await _listed(dispatcher) == []

    asyncio.run(scenario())


def test_an_archived_thread_stays_archived_after_a_dispatcher_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(tmp_path)
        kept = await _create(dispatcher)
        put_away = await _create(dispatcher)
        await _archive(dispatcher, put_away.id)

        restarted = build_dispatcher(tmp_path)

        assert await _listed(restarted) == [kept.id]
        assert await _listed(restarted, "archived") == [put_away.id]

    asyncio.run(scenario())


def test_starting_a_turn_in_an_archived_thread_unarchives_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        created = await _create(dispatcher)
        await _archive(dispatcher, created.id)

        await _run(dispatcher, created.id, "done", EventType.TURN_COMPLETED)

        assert await _listed(dispatcher) == [created.id]
        assert await _listed(dispatcher, "archived") == []

    asyncio.run(scenario())
