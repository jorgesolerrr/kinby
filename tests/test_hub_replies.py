"""The user's replies to notifications: recorded at once, then applied against what holds now."""

import asyncio
import json
import queue
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from uuid import UUID, uuid4

from aiohttp import web

from kinby.contracts import (
    CHANNEL_MUTE,
    NOTIFICATION_LIST,
    NOTIFICATION_RAISE,
    NOTIFICATION_REPLY,
    THREAD_TURN_START,
    AcceptedResult,
    Channel,
    ChannelMuteCommand,
    DeliveryStatus,
    ErrorCode,
    ErrorEnvelope,
    NotificationDelivery,
    NotificationListCommand,
    NotificationListResult,
    NotificationReply,
    NotificationReplyCommand,
    ReplyStatus,
    Scope,
    ThreadTurnStartCommand,
)
from kinby.core.dispatcher import Dispatcher
from kinby.core.errors import ThreadBusy
from kinby.hub import Hub
from kinby.hub.channels import SentRef
from kinby.instance import Serve
from tests.test_hub import FakeRuntime, created_instance, hub_client, started_instance
from tests.test_hub_channels import (
    _NOW,
    FakeService,
    FakeTime,
    channel_hub,
    next_sent,
    notification,
    paired_channel,
    posted,
    raised,
    raising,
    until,
)
from tests.test_hub_routing import control_token, instance_served
from tests.test_hub_server import served

_UNKNOWN = "Not known whether the instance got this answer. Answer again from the web app."


class FakeThreads:
    """An instance's threads as the hub's relay reaches them: the turns they started, after
    refusing as many as *refusals* because a turn was running."""

    def __init__(self, *, refusals: int = 0) -> None:
        self.refusals = refusals
        self.started: asyncio.Queue[ThreadTurnStartCommand] = asyncio.Queue()

    def dispatcher(self) -> Dispatcher:
        dispatcher = Dispatcher()
        dispatcher.register(THREAD_TURN_START, self._start)
        return dispatcher

    async def _start(self, command: ThreadTurnStartCommand) -> AcceptedResult:
        if self.refusals:
            self.refusals -= 1
            raise ThreadBusy("A turn is running in the thread.")
        self.started.put_nowait(command)
        return AcceptedResult(thread_id=command.thread_id, turn_id=uuid4(), sequence=1)


@asynccontextmanager
async def running(
    runtime: FakeRuntime, hub: Hub, instance_id: UUID, threads: FakeThreads
) -> AsyncIterator[None]:
    """Serve *threads* where the hub reaches the instance."""
    async with instance_served(threads.dispatcher(), control_token(hub, str(instance_id))) as at:
        runtime.addresses[str(instance_id)] = f"http://{at.host}:{at.port}"
        yield


@asynccontextmanager
async def hanging_up(
    runtime: FakeRuntime, instance_id: UUID, taken: list[str]
) -> AsyncIterator[None]:
    """Stand in for an instance that hangs up on every call it takes, unanswered."""

    async def socket(request: web.Request) -> web.WebSocketResponse:
        answering = web.WebSocketResponse()
        await answering.prepare(request)
        taken.append(json.loads(await answering.receive_str())["method"])
        await answering.close()
        return answering

    application = web.Application()
    application.router.add_get("/ws", socket)
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    runtime.addresses[str(instance_id)] = f"http://127.0.0.1:{site.port}"
    try:
        yield
    finally:
        await runner.cleanup()


@contextmanager
def holding(taken: queue.Queue[str]) -> Iterator[str]:
    """Stand in for an instance that holds every call it takes unanswered, on a loop of its own,
    so the hub's loop can stop while a call is out. Yields where it listens."""

    async def socket(request: web.Request) -> web.WebSocketResponse:
        answering = web.WebSocketResponse()
        await answering.prepare(request)
        taken.put(json.loads(await answering.receive_str())["method"])
        await answering.receive()
        return answering

    async def serve() -> tuple[web.AppRunner, int]:
        application = web.Application()
        application.router.add_get("/ws", socket)
        runner = web.AppRunner(application)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        return runner, site.port

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever)
    thread.start()
    runner, port = asyncio.run_coroutine_threadsafe(serve(), loop).result(timeout=5)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        asyncio.run_coroutine_threadsafe(runner.cleanup(), loop).result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
        loop.close()


async def replied(
    address: Serve, channel: Channel, message: SentRef, option: int, *, user: str = "jorge"
) -> None:
    await posted(address, channel, {"user": user, "message": message, "option": option})


async def next_edit(service: FakeService) -> tuple[SentRef, str]:
    return await asyncio.wait_for(service.edited.get(), timeout=5)


async def next_turn(threads: FakeThreads) -> ThreadTurnStartCommand:
    return await asyncio.wait_for(threads.started.get(), timeout=5)


async def replies(hub: Hub, instance_id: UUID) -> list[NotificationReply]:
    listed = await hub_client(hub).call(
        NOTIFICATION_LIST, NotificationListCommand(instance_id=instance_id)
    )
    assert isinstance(listed, NotificationListResult), listed
    return [reply for notification in listed.notifications for reply in notification.replies]


def test_every_notification_offers_to_mute_its_instance_and_that_reply_mutes_it_on_the_channel(
    tmp_path,
):
    async def scenario() -> None:
        service = FakeService()
        hub = channel_hub(tmp_path / "hub", service, FakeTime())
        client = hub_client(hub)
        alice_id = (await created_instance(client)).instance_id
        alice = raising(hub, alice_id)
        async with served(hub) as address:
            channel = await paired_channel(client, address)
            await raised(alice, "news")
            sent = await next_sent(service)
            await replied(address, channel, sent.ref, 1)
            muted = await next_edit(service)
            await replied(address, channel, sent.ref, 1)
            again = await next_edit(service)
        await raised(alice, "after the mute")
        listed = await client.call(NOTIFICATION_LIST, NotificationListCommand(instance_id=alice_id))

        assert sent.notification.options == ("Rerun", "Mute this instance")
        assert muted == (sent.ref, "Muted this instance on this channel.")
        assert again == (sent.ref, "This instance was already muted on this channel.")
        assert [attempt.title for attempt in service.attempts] == ["news"]
        assert isinstance(listed, NotificationListResult), listed
        news, after = listed.notifications
        assert [(reply.option, reply.channel_id, reply.status) for reply in news.replies] == [
            (None, channel.channel_id, ReplyStatus.APPLIED),
            (None, channel.channel_id, ReplyStatus.STALE),
        ]
        assert news.replies[0].received_at == _NOW
        assert after.deliveries == [
            NotificationDelivery(channel_id=channel.channel_id, status=DeliveryStatus.MUTED)
        ]

    asyncio.run(scenario())


def test_a_reply_to_an_instances_notification_starts_a_user_turn_in_the_thread_that_sent_it(
    tmp_path,
):
    async def scenario() -> None:
        runtime = FakeRuntime()
        service = FakeService()
        hub = channel_hub(tmp_path / "hub", service, FakeTime(), runtime)
        client = hub_client(hub)
        alice_id = (await started_instance(client, hub)).instance_id
        threads = FakeThreads()
        async with served(hub) as address, running(runtime, hub, alice_id, threads):
            channel = await paired_channel(client, address)
            await raised(raising(hub, alice_id), "news")
            sent = await next_sent(service)
            await replied(address, channel, sent.ref, 0)
            started = await next_turn(threads)
            edited = await next_edit(service)

        assert started == ThreadTurnStartCommand(thread_id=UUID(int=1), message="Rerun")
        assert edited == (sent.ref, 'Answered "Rerun".')
        [reply] = await replies(hub, alice_id)
        assert (reply.option, reply.status, reply.outcome) == (
            "Rerun",
            ReplyStatus.APPLIED,
            'Answered "Rerun".',
        )

    asyncio.run(scenario())


def test_a_reply_to_a_notification_already_answered_is_stale_and_still_edits_the_message(
    tmp_path,
):
    async def scenario() -> None:
        runtime = FakeRuntime()
        service = FakeService()
        hub = channel_hub(tmp_path / "hub", service, FakeTime(), runtime)
        client = hub_client(hub)
        alice_id = (await started_instance(client, hub)).instance_id
        threads = FakeThreads()
        async with served(hub) as address, running(runtime, hub, alice_id, threads):
            channel = await paired_channel(client, address)
            notification_id = (await raised(raising(hub, alice_id), "news")).notification_id
            sent = await next_sent(service)
            answered = await client.call(
                NOTIFICATION_REPLY,
                NotificationReplyCommand(notification_id=notification_id, option="Rerun"),
            )
            await next_turn(threads)
            await replied(address, channel, sent.ref, 0)
            edited = await next_edit(service)

        assert isinstance(answered, NotificationReply), answered
        assert (answered.option, answered.channel_id, answered.status) == (
            "Rerun",
            None,
            ReplyStatus.RECEIVED,
        )
        assert edited == (sent.ref, "Already answered.")
        assert threads.started.empty()
        assert [(reply.channel_id, reply.status) for reply in await replies(hub, alice_id)] == [
            (None, ReplyStatus.APPLIED),
            (channel.channel_id, ReplyStatus.STALE),
        ]

    asyncio.run(scenario())


def test_a_reply_from_anyone_but_the_paired_user_is_ignored_and_recorded_nowhere(tmp_path):
    async def scenario() -> None:
        service = FakeService()
        hub = channel_hub(tmp_path / "hub", service, FakeTime())
        client = hub_client(hub)
        alice_id = (await created_instance(client)).instance_id
        async with served(hub) as address:
            channel = await paired_channel(client, address)
            await raised(raising(hub, alice_id), "news")
            sent = await next_sent(service)
            await replied(address, channel, sent.ref, 1, user="mallory")
            await replied(address, channel, sent.ref, 0, user="mallory")
            await replied(address, channel, sent.ref, 1)
            edited = await next_edit(service)

        assert edited == (sent.ref, "Muted this instance on this channel.")
        assert service.edited.empty()
        assert [reply.option for reply in await replies(hub, alice_id)] == [None]

    asyncio.run(scenario())


def test_a_reply_the_thread_is_too_busy_for_is_tried_again_after_a_backoff(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        service = FakeService()
        time = FakeTime()
        hub = channel_hub(tmp_path / "hub", service, time, runtime)
        client = hub_client(hub)
        alice_id = (await started_instance(client, hub)).instance_id
        threads = FakeThreads(refusals=2)
        async with served(hub) as address, running(runtime, hub, alice_id, threads):
            channel = await paired_channel(client, address)
            await raised(raising(hub, alice_id), "news")
            sent = await next_sent(service)
            await replied(address, channel, sent.ref, 0)
            started = await next_turn(threads)
            edited = await next_edit(service)

        assert time.slept == [5, 10]
        assert started.message == "Rerun"
        assert edited == (sent.ref, 'Answered "Rerun".')

    asyncio.run(scenario())


def test_a_recorded_reply_that_was_not_applied_is_applied_after_the_hub_restarts(tmp_path):
    directory = tmp_path / "hub"
    runtime = FakeRuntime()
    instances: list[UUID] = []

    async def interrupted() -> SentRef:
        service = FakeService()
        time = FakeTime(gate=asyncio.Event())
        hub = channel_hub(directory, service, time, runtime)
        client = hub_client(hub)
        instances.append((await started_instance(client, hub)).instance_id)
        threads = FakeThreads(refusals=1)
        async with served(hub) as address, running(runtime, hub, instances[0], threads):
            channel = await paired_channel(client, address)
            await raised(raising(hub, instances[0]), "news")
            sent = await next_sent(service)
            await replied(address, channel, sent.ref, 0)
            await until(lambda: len(time.slept) == 1)
        hub.close()
        return sent.ref

    async def resumed() -> tuple[ThreadTurnStartCommand, tuple[SentRef, str]]:
        service = FakeService()
        hub = channel_hub(directory, service, FakeTime(), runtime)
        threads = FakeThreads()
        async with running(runtime, hub, instances[0], threads):
            await hub.recover()
            return await next_turn(threads), await next_edit(service)

    ref = asyncio.run(interrupted())
    started, edited = asyncio.run(resumed())

    assert started == ThreadTurnStartCommand(thread_id=UUID(int=1), message="Rerun")
    assert edited == (ref, 'Answered "Rerun".')


def test_a_turn_start_whose_answer_was_lost_is_not_tried_again(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        service = FakeService()
        time = FakeTime()
        hub = channel_hub(tmp_path / "hub", service, time, runtime)
        client = hub_client(hub)
        alice_id = (await started_instance(client, hub)).instance_id
        taken: list[str] = []
        async with served(hub) as address, hanging_up(runtime, alice_id, taken):
            channel = await paired_channel(client, address)
            await raised(raising(hub, alice_id), "news")
            sent = await next_sent(service)
            await replied(address, channel, sent.ref, 0)
            edited = await next_edit(service)

        assert taken == ["thread.turn.start"]
        assert time.slept == []
        assert edited == (sent.ref, _UNKNOWN)
        [reply] = await replies(hub, alice_id)
        assert reply.status is ReplyStatus.FAILED

    asyncio.run(scenario())


def test_a_turn_start_the_hub_stopped_waiting_for_is_not_made_again_after_a_restart(tmp_path):
    directory = tmp_path / "hub"
    runtime = FakeRuntime()
    instances: list[UUID] = []

    taken: queue.Queue[str] = queue.Queue()

    async def interrupted(held_at: str) -> SentRef:
        """Stop the hub, its loop with it, while the instance holds the turn start."""
        service = FakeService()
        hub = channel_hub(directory, service, FakeTime(), runtime)
        client = hub_client(hub)
        instances.append((await started_instance(client, hub)).instance_id)
        runtime.addresses[str(instances[0])] = held_at
        async with served(hub) as address:
            channel = await paired_channel(client, address)
            await raised(raising(hub, instances[0]), "news")
            sent = await next_sent(service)
            await replied(address, channel, sent.ref, 0)
            assert await asyncio.to_thread(taken.get, timeout=5) == "thread.turn.start"
        hub.close()
        return sent.ref

    async def resumed() -> tuple[tuple[SentRef, str], FakeThreads]:
        service = FakeService()
        hub = channel_hub(directory, service, FakeTime(), runtime)
        threads = FakeThreads()
        async with running(runtime, hub, instances[0], threads):
            await hub.recover()
            return await next_edit(service), threads

    with holding(taken) as held_at:
        ref = asyncio.run(interrupted(held_at))
    edited, threads = asyncio.run(resumed())

    assert edited == (ref, _UNKNOWN)
    assert threads.started.empty()


def test_notifications_are_listed_by_instance_or_thread_with_their_deliveries(tmp_path):
    async def scenario() -> None:
        hub = channel_hub(tmp_path / "hub", FakeService(), FakeTime())
        client = hub_client(hub)
        alice_id = (await created_instance(client, name="alice")).instance_id
        bob_id = (await created_instance(client, name="bob")).instance_id
        async with served(hub) as address:
            channel = await paired_channel(client, address)
        await client.call(
            CHANNEL_MUTE, ChannelMuteCommand(channel_id=channel.channel_id, instance_id=alice_id)
        )
        alice = raising(hub, alice_id)
        await raised(alice, "news")
        await alice.call(
            NOTIFICATION_RAISE,
            notification("weather").model_copy(update={"thread_id": UUID(int=2)}),
        )
        await raised(raising(hub, bob_id), "from bob")

        by_instance = await client.call(
            NOTIFICATION_LIST, NotificationListCommand(instance_id=alice_id)
        )
        by_thread = await client.call(
            NOTIFICATION_LIST, NotificationListCommand(thread_id=UUID(int=2))
        )
        every = await client.call(NOTIFICATION_LIST, NotificationListCommand())

        assert isinstance(by_instance, NotificationListResult), by_instance
        assert [item.title for item in by_instance.notifications] == ["news", "weather"]
        news = by_instance.notifications[0]
        assert (news.instance_id, news.thread_id, news.options, news.raised_at) == (
            alice_id,
            UUID(int=1),
            ["Rerun"],
            _NOW,
        )
        assert news.header == "alice · Daily news"
        assert news.deliveries == [
            NotificationDelivery(channel_id=channel.channel_id, status=DeliveryStatus.MUTED)
        ]
        assert news.replies == []
        assert isinstance(by_thread, NotificationListResult), by_thread
        assert [item.title for item in by_thread.notifications] == ["weather"]
        assert isinstance(every, NotificationListResult), every
        assert [item.title for item in every.notifications] == ["news", "weather", "from bob"]

    asyncio.run(scenario())


def test_the_web_app_replies_only_with_an_option_the_notification_offers_and_only_as_admin(
    tmp_path,
):
    async def scenario() -> None:
        hub = channel_hub(tmp_path / "hub", FakeService(), FakeTime())
        client = hub_client(hub)
        alice_id = (await created_instance(client)).instance_id
        notification_id = (await raised(raising(hub, alice_id), "news")).notification_id

        unoffered = await client.call(
            NOTIFICATION_REPLY,
            NotificationReplyCommand(notification_id=notification_id, option="Mute this instance"),
        )
        missing = await client.call(
            NOTIFICATION_REPLY, NotificationReplyCommand(notification_id=uuid4(), option="Rerun")
        )
        reader = await hub_client(hub, {Scope.HUB_READ}).call(
            NOTIFICATION_REPLY,
            NotificationReplyCommand(notification_id=notification_id, option="Rerun"),
        )

        assert isinstance(unoffered, ErrorEnvelope)
        assert unoffered.code is ErrorCode.INVALID_ARGUMENT
        assert isinstance(missing, ErrorEnvelope)
        assert missing.code is ErrorCode.NOT_FOUND
        assert isinstance(reader, ErrorEnvelope)
        assert reader.code is ErrorCode.PERMISSION_DENIED
        assert await replies(hub, alice_id) == []

    asyncio.run(scenario())
