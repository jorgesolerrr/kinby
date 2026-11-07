import asyncio
from pathlib import Path
from uuid import UUID

import pytest

from kinby.contracts import (
    AcceptedResult,
    ApprovalRequested,
    EventType,
    Scope,
    ThreadListResult,
    ThreadStatus,
)
from kinby.core.dispatcher import Dispatcher, build_dispatcher
from tests.test_thread_archive import _create
from tests.test_thread_sidebar import _fire, _setup
from tests.test_thread_status import _until


async def _list(dispatcher: Dispatcher, **command: object) -> ThreadListResult:
    listed = await dispatcher.dispatch("thread.list", command, {Scope.THREAD_READ})
    assert isinstance(listed, ThreadListResult), listed
    return listed


async def _listed(dispatcher: Dispatcher, **command: object) -> list[UUID]:
    return [thread.id for thread in (await _list(dispatcher, **command)).threads]


def test_the_routine_option_lists_that_routines_runs_alone(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher, _ = await _setup(tmp_path, "done", "fail")
        await _create(dispatcher)
        first = await _fire(dispatcher, "done")
        failed = await _fire(dispatcher, "fail")
        second = await _fire(dispatcher, "done")

        assert await _listed(dispatcher, filter="all", routine="done") == [second, first]
        assert await _listed(dispatcher, filter="sidebar", routine="fail") == [failed]
        assert await _listed(dispatcher, filter="sidebar", routine="done") == []

    asyncio.run(scenario())


def test_a_routine_run_parked_on_a_gated_call_lists_the_approval_it_waits_on(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        dispatcher, _ = await _setup(tmp_path, "park")
        thread_id = await _fire(dispatcher, "park")
        requested = await _until(dispatcher, thread_id, EventType.APPROVAL_REQUESTED)

        [thread] = (await _list(dispatcher, filter="all", routine="park")).threads

        assert thread.status is ThreadStatus.AWAITING_APPROVAL
        assert thread.approval == requested.payload

    asyncio.run(scenario())


@pytest.mark.parametrize("decision", ["approve", "deny"])
def test_an_answered_approval_leaves_the_summary_once_the_turn_moves_on(
    tmp_path: Path, decision: str
) -> None:
    async def scenario() -> None:
        dispatcher, _ = await _setup(tmp_path, "park")
        thread_id = await _fire(dispatcher, "park")
        requested = await _until(dispatcher, thread_id, EventType.APPROVAL_REQUESTED)
        assert isinstance(requested.payload, ApprovalRequested)
        answered = await dispatcher.dispatch(
            "thread.approval.respond",
            {
                "thread_id": thread_id,
                "approval_id": requested.payload.approval_id,
                "decision": decision,
            },
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(answered, AcceptedResult), answered
        await _until(dispatcher, thread_id, EventType.TURN_COMPLETED)

        [thread] = (await _list(dispatcher, filter="all", routine="park")).threads

        assert thread.status is ThreadStatus.IDLE
        assert thread.approval is None

    asyncio.run(scenario())


def test_thread_list_pages_by_cursor_newest_activity_first(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(tmp_path)
        created = [(await _create(dispatcher)).id for _ in range(5)]
        newest_first = created[::-1]

        first = await _list(dispatcher, filter="all", limit=2)
        second = await _list(dispatcher, filter="all", limit=2, cursor=_cursor(first))
        last = await _list(dispatcher, filter="all", limit=2, cursor=_cursor(second))

        assert [thread.id for thread in first.threads] == newest_first[:2]
        assert [thread.id for thread in second.threads] == newest_first[2:4]
        assert [thread.id for thread in last.threads] == newest_first[4:]
        assert last.cursor is None

    asyncio.run(scenario())


def test_thread_list_without_a_limit_lists_every_thread_on_one_page(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(tmp_path)
        created = [(await _create(dispatcher)).id for _ in range(3)]

        listed = await _list(dispatcher)

        assert [thread.id for thread in listed.threads] == created[::-1]
        assert listed.cursor is None

    asyncio.run(scenario())


def test_a_page_that_ends_on_the_last_thread_has_no_cursor(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(tmp_path)
        for _ in range(2):
            await _create(dispatcher)

        listed = await _list(dispatcher, limit=2)

        assert len(listed.threads) == 2
        assert listed.cursor is None

    asyncio.run(scenario())


def _cursor(page: ThreadListResult) -> dict[str, object]:
    assert page.cursor is not None
    return page.cursor.model_dump(mode="json")
