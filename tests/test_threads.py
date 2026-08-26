import asyncio
from pathlib import Path

from kinby.contracts import (
    ErrorCode,
    ErrorEnvelope,
    Scope,
    ThreadCreateCommand,
    ThreadCreateResult,
    ThreadListResult,
)
from kinby.core.dispatcher import Dispatcher, build_dispatcher


def test_create_and_list_thread_after_dispatcher_restart(tmp_path: Path) -> None:
    dispatcher = build_dispatcher(tmp_path)

    created = asyncio.run(
        dispatcher.dispatch(
            "thread.create",
            {"title": "Launch notes"},
            {Scope.THREAD_OPERATE},
        )
    )

    assert isinstance(created, ThreadCreateResult)

    restarted_dispatcher = build_dispatcher(tmp_path)
    listed = asyncio.run(
        restarted_dispatcher.dispatch(
            "thread.list",
            {},
            {Scope.THREAD_READ},
        )
    )

    assert isinstance(listed, ThreadListResult)
    assert [(thread.id, thread.title) for thread in listed.threads] == [
        (created.id, "Launch notes")
    ]


def test_dispatcher_returns_typed_errors_before_running_a_handler() -> None:
    handler_ran = False

    async def handler(command: ThreadCreateCommand) -> ThreadCreateResult:
        nonlocal handler_ran
        handler_ran = True
        raise AssertionError("handler should not run")

    dispatcher = Dispatcher()
    dispatcher.register(
        "thread.create",
        Scope.THREAD_OPERATE,
        ThreadCreateCommand,
        handler,
    )

    denied = asyncio.run(dispatcher.dispatch("thread.create", {"unexpected": True}, set()))
    missing = asyncio.run(dispatcher.dispatch("thread.missing", {}, {Scope.THREAD_OPERATE}))
    invalid = asyncio.run(
        dispatcher.dispatch(
            "thread.create",
            {"unexpected": True},
            {Scope.THREAD_OPERATE},
        )
    )

    assert handler_ran is False
    assert isinstance(denied, ErrorEnvelope)
    assert denied.code is ErrorCode.PERMISSION_DENIED
    assert isinstance(missing, ErrorEnvelope)
    assert missing.code is ErrorCode.NOT_FOUND
    assert isinstance(invalid, ErrorEnvelope)
    assert invalid.code is ErrorCode.INVALID_ARGUMENT


def test_dispatcher_translates_an_unexpected_handler_failure() -> None:
    async def handler(command: ThreadCreateCommand) -> ThreadCreateResult:
        raise RuntimeError("database unavailable")

    dispatcher = Dispatcher()
    dispatcher.register(
        "thread.create",
        Scope.THREAD_OPERATE,
        ThreadCreateCommand,
        handler,
    )

    result = asyncio.run(
        dispatcher.dispatch(
            "thread.create",
            {},
            {Scope.THREAD_OPERATE},
        )
    )

    assert isinstance(result, ErrorEnvelope)
    assert result == ErrorEnvelope(
        code=ErrorCode.INTERNAL,
        message="The method failed unexpectedly.",
        retryable=False,
    )
