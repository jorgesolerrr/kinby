import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from kinby.contracts import (
    AcceptedResult,
    ErrorCode,
    ErrorEnvelope,
    Scope,
    ThreadCreateResult,
    ThreadListResult,
    ThreadSummary,
)
from kinby.core.dispatcher import Dispatcher, TurnConfig, build_dispatcher
from tests.helpers import fixed_permission_ceiling, fixed_turn_preparation
from tests.test_thread_status import ScriptedRunner


def _dispatcher(tmp_path: Path) -> Dispatcher:
    return build_dispatcher(
        tmp_path,
        turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, ScriptedRunner()),
    )


async def _create(dispatcher: Dispatcher, title: str | None = None) -> ThreadCreateResult:
    created = await dispatcher.dispatch("thread.create", {"title": title}, {Scope.THREAD_OPERATE})
    assert isinstance(created, ThreadCreateResult)
    return created


async def _start(dispatcher: Dispatcher, created: ThreadCreateResult, message: str) -> None:
    started = await dispatcher.dispatch(
        "thread.turn.start", {"thread_id": created.id, "message": message}, {Scope.THREAD_OPERATE}
    )
    assert isinstance(started, AcceptedResult)


async def _listed(dispatcher: Dispatcher) -> list[ThreadSummary]:
    listed = await dispatcher.dispatch("thread.list", {}, {Scope.THREAD_READ})
    assert isinstance(listed, ThreadListResult)
    return listed.threads


@pytest.mark.parametrize(
    ("message", "title"),
    [
        ("What's on my calendar today?", "What's on my calendar today?"),
        (
            "Summarize the quarterly report and draft a reply to the finance team about it",
            "Summarize the quarterly report and draft a reply to the…",
        ),
        ("Fix the build\n\n  then   run\tthe tests\n", "Fix the build then run the tests"),
    ],
)
def test_the_first_message_titles_an_untitled_thread(
    tmp_path: Path, message: str, title: str
) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        created = await _create(dispatcher)

        await _start(dispatcher, created, message)
        [thread] = await _listed(dispatcher)

        assert thread.title == title

    asyncio.run(scenario())


def test_a_thread_created_with_a_title_keeps_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = _dispatcher(tmp_path)
        created = await _create(dispatcher, "Launch notes")

        await _start(dispatcher, created, "What's on my calendar today?")
        [thread] = await _listed(dispatcher)

        assert thread.title == "Launch notes"

    asyncio.run(scenario())


def test_a_rename_titles_the_thread_and_returns_its_summary(tmp_path: Path) -> None:
    async def scenario() -> None:
        created = await _create(build_dispatcher(tmp_path), "Launch notes")

        renamed = await build_dispatcher(tmp_path).dispatch(
            "thread.rename",
            {"thread_id": created.id, "title": "  Launch plan  "},
            {Scope.THREAD_OPERATE},
        )
        [listed] = await _listed(build_dispatcher(tmp_path))

        assert isinstance(renamed, ThreadSummary)
        assert (renamed.id, renamed.title, renamed.created_at) == (
            created.id,
            "Launch plan",
            created.created_at,
        )
        assert listed == renamed

    asyncio.run(scenario())


@pytest.mark.parametrize("title", ["", "   ", "x" * 201])
def test_a_rename_refuses_an_empty_or_overlong_title(tmp_path: Path, title: str) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(tmp_path)
        created = await _create(dispatcher, "Launch notes")

        refused = await dispatcher.dispatch(
            "thread.rename", {"thread_id": created.id, "title": title}, {Scope.THREAD_OPERATE}
        )
        [listed] = await _listed(dispatcher)

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert listed.title == "Launch notes"

    asyncio.run(scenario())


def test_a_rename_takes_a_title_of_two_hundred_characters(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(tmp_path)
        created = await _create(dispatcher)

        renamed = await dispatcher.dispatch(
            "thread.rename",
            {"thread_id": created.id, "title": f" {'x' * 200} "},
            {Scope.THREAD_OPERATE},
        )

        assert isinstance(renamed, ThreadSummary)
        assert renamed.title == "x" * 200

    asyncio.run(scenario())


def test_a_rename_of_an_unknown_thread_is_not_found(tmp_path: Path) -> None:
    thread_id = uuid4()

    refused = asyncio.run(
        build_dispatcher(tmp_path).dispatch(
            "thread.rename", {"thread_id": thread_id, "title": "Launch"}, {Scope.THREAD_OPERATE}
        )
    )

    assert refused == ErrorEnvelope(
        code=ErrorCode.NOT_FOUND, message=f'Thread "{thread_id}" was not found.', retryable=False
    )


def test_a_rename_needs_the_operate_scope(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(tmp_path)
        created = await _create(dispatcher)

        refused = await dispatcher.dispatch(
            "thread.rename", {"thread_id": created.id, "title": "Launch"}, {Scope.THREAD_READ}
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.PERMISSION_DENIED

    asyncio.run(scenario())
