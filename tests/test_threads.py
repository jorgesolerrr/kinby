import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import uuid4

from kinby.cli.client import ContractClient
from kinby.contracts import (
    THREAD_SUBSCRIBE,
    ErrorCode,
    ErrorEnvelope,
    Event,
    Scope,
    ThreadCreateCommand,
    ThreadCreateResult,
    ThreadListResult,
    ThreadSubscribeCommand,
    TurnCompleted,
    TurnStarted,
)
from kinby.contracts.methods import Method, Subscription
from kinby.core.dispatcher import Dispatcher, build_dispatcher
from kinby.core.events import EventLog

STARTED = TurnStarted(message="Hello", model="openai:gpt-5")


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
        Method("thread.create", Scope.THREAD_OPERATE, ThreadCreateCommand, ThreadCreateResult),
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
        Method("thread.create", Scope.THREAD_OPERATE, ThreadCreateCommand, ThreadCreateResult),
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


def test_thread_subscribe_replays_a_finished_thread_through_dispatcher(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        thread_id = uuid4()
        turn_id = uuid4()
        event_log = EventLog(tmp_path)
        stored = [
            await event_log.append(thread_id, turn_id, STARTED),
            await event_log.append(
                thread_id,
                turn_id,
                TurnCompleted(input_tokens=0, output_tokens=0),
            ),
        ]
        dispatcher = build_dispatcher(tmp_path)

        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": thread_id, "after_sequence": 0},
            {Scope.THREAD_READ},
        )
        replayed = [await anext(subscription) for _ in stored]
        await subscription.aclose()

        assert all(isinstance(event, Event) for event in replayed)
        assert replayed == stored

    asyncio.run(scenario())


def test_thread_subscribe_checks_scope_before_payload(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(tmp_path)

        denied_subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"unexpected": True},
            set(),
        )
        denied = await anext(denied_subscription)
        invalid_subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"unexpected": True},
            {Scope.THREAD_READ},
        )
        invalid = await anext(invalid_subscription)

        assert isinstance(denied, ErrorEnvelope)
        assert denied.code is ErrorCode.PERMISSION_DENIED
        assert isinstance(invalid, ErrorEnvelope)
        assert invalid.code is ErrorCode.INVALID_ARGUMENT

    asyncio.run(scenario())


def test_unknown_subscription_returns_not_found() -> None:
    async def scenario() -> None:
        subscription = Dispatcher().subscribe(
            "thread.missing",
            {},
            {Scope.THREAD_READ},
        )

        result = await anext(subscription)

        assert isinstance(result, ErrorEnvelope)
        assert result.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_subscription_translates_an_unexpected_handler_failure() -> None:
    async def scenario() -> None:
        async def handler(
            command: ThreadCreateCommand,
        ) -> AsyncGenerator[ThreadCreateCommand]:
            if command.title is None:
                raise RuntimeError("event log unavailable")
            yield command

        dispatcher = Dispatcher()
        dispatcher.register_subscription(
            Subscription(
                "thread.subscribe", Scope.THREAD_READ, ThreadCreateCommand, ThreadCreateCommand
            ),
            handler,
        )
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {},
            {Scope.THREAD_READ},
        )

        result = await anext(subscription)

        assert isinstance(result, ErrorEnvelope)
        assert result == ErrorEnvelope(
            code=ErrorCode.INTERNAL,
            message="The subscription failed unexpectedly.",
            retryable=False,
        )

    asyncio.run(scenario())


def test_closing_subscription_releases_its_handler() -> None:
    async def scenario() -> None:
        handler_closed = False

        async def handler(
            command: ThreadCreateCommand,
        ) -> AsyncGenerator[ThreadCreateCommand]:
            nonlocal handler_closed
            try:
                yield command
            finally:
                handler_closed = True

        dispatcher = Dispatcher()
        dispatcher.register_subscription(
            Subscription(
                "thread.subscribe", Scope.THREAD_READ, ThreadCreateCommand, ThreadCreateCommand
            ),
            handler,
        )
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {},
            {Scope.THREAD_READ},
        )

        await anext(subscription)
        await subscription.aclose()

        assert handler_closed is True

    asyncio.run(scenario())


def test_contract_client_subscription_replays_then_stays_live(tmp_path: Path) -> None:
    async def scenario() -> None:
        thread_id = uuid4()
        turn_id = uuid4()
        event_log = EventLog(tmp_path)
        replayed = await event_log.append(
            thread_id,
            turn_id,
            STARTED,
        )
        dispatcher = build_dispatcher(tmp_path, event_log=event_log)
        client = ContractClient(
            dispatcher.dispatch,
            dispatcher.subscribe,
            {Scope.THREAD_READ},
        )
        subscription = client.subscribe(
            THREAD_SUBSCRIBE, ThreadSubscribeCommand(thread_id=thread_id)
        )

        received_replay = await anext(subscription)
        waiting_for_live = asyncio.ensure_future(anext(subscription))
        await asyncio.sleep(0)
        live = await event_log.append(
            thread_id,
            turn_id,
            TurnCompleted(input_tokens=0, output_tokens=0),
        )
        received_live = await asyncio.wait_for(waiting_for_live, timeout=1)
        await subscription.aclose()

        assert [received_replay, received_live] == [replayed, live]

    asyncio.run(scenario())
