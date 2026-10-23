"""Record each reply the user sends at once, then apply it to what holds by then (ADR 0103)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime
from uuid import UUID, uuid4

from aiohttp import web

from kinby.contracts import (
    NotificationReply,
    NotificationReplyCommand,
    ReplyStatus,
    ThreadTurnStartCommand,
)
from kinby.core.errors import CoreError, NotificationNotFound, OptionNotOffered
from kinby.hub.channels import Channels, ReceivingKind
from kinby.hub.outbox import GIVE_UP_AFTER, Sleep
from kinby.hub.registry import HubRegistry, ReceivedReply
from kinby.hub.runs import BACKOFF_CAP_SECONDS, BACKOFF_SECONDS

_logger = logging.getLogger(__name__)

#: Start a user turn in one of an instance's threads.
type StartTurn = Callable[[UUID, ThreadTurnStartCommand], Awaitable[object]]


class Replies:
    """Take the user's replies to notifications, and apply them one at a time.

    A reply to an instance's notification starts a user turn in the thread that raised it, and
    "Mute this instance" mutes the instance on the channel the reply came through. One that
    answers something already settled changes nothing. Either way, the channel's message is
    edited to show what came of it.
    """

    def __init__(
        self,
        registry: HubRegistry,
        channels: Channels,
        start_turn: StartTurn,
        clock: Callable[[], datetime],
        sleep: Sleep,
    ) -> None:
        self._registry = registry
        self._channels = channels
        self._start_turn = start_turn
        self._clock = clock
        self._sleep = sleep
        self._applying = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    async def receive(self, channel_id: str, kind: str, request: web.Request) -> web.Response:
        """Answer a call to a channel's public route as soon as the reply it carries is
        recorded."""
        received = await self._channels.receive(channel_id, kind, request)
        if received is None:
            return web.Response()
        channel, reply = received
        notification = self._registry.sent_notification(channel.channel_id, reply.message)
        if notification is None:
            return web.Response()
        # The channel sent the notification's options with "Mute this instance" after them.
        options = [*notification.options, None]
        if 0 <= reply.option < len(options):
            self._record(notification.notification_id, channel.channel_id, options[reply.option])
        return web.Response()

    async def reply(self, command: NotificationReplyCommand) -> NotificationReply:
        """Answer a notification from the web app, which edits no channel's message."""
        notification = next(
            iter(self._registry.notifications(notification_id=command.notification_id)), None
        )
        if notification is None:
            raise NotificationNotFound(f'Notification "{command.notification_id}" was not found.')
        if command.option not in notification.options:
            raise OptionNotOffered(f'The notification does not offer "{command.option}".')
        return self._record(notification.notification_id, None, command.option)

    def resume(self) -> None:
        """Apply what the previous process recorded and did not apply."""
        self._apply_all(self._registry.received_replies())

    def _record(
        self, notification_id: UUID, channel_id: UUID | None, option: str | None
    ) -> NotificationReply:
        reply_id = uuid4()
        received_at = self._clock()
        self._registry.record_reply(reply_id, notification_id, channel_id, option, received_at)
        self._apply_all(self._registry.received_replies(reply_id))
        return NotificationReply(
            option=option,
            channel_id=channel_id,
            received_at=received_at,
            status=ReplyStatus.RECEIVED,
        )

    def _apply_all(self, replies: Iterable[ReceivedReply]) -> None:
        for reply in replies:
            task = asyncio.create_task(self._apply(reply))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _apply(self, reply: ReceivedReply) -> None:
        """Apply the reply, trying again with backoff for a day while the instance cannot take
        it yet, and edit the message it answered."""
        attempts = 0
        while (outcome := await self._settled(reply)) is None:
            attempts += 1
            await self._sleep(min(BACKOFF_SECONDS * 2 ** (attempts - 1), BACKOFF_CAP_SECONDS))
        await self._edit(reply, outcome)

    async def _settled(self, reply: ReceivedReply) -> str | None:
        """Record what came of the reply, or None when it is to try again."""
        async with self._applying:
            try:
                status, outcome = await self._applied(reply)
            except Exception as exc:
                retryable = isinstance(exc, CoreError) and exc.envelope().retryable
                if retryable and self._clock() - reply.received_at < GIVE_UP_AFTER:
                    return None
                status, outcome = ReplyStatus.FAILED, f"Not answered: {exc or type(exc).__name__}"
            self._registry.settle_reply(reply.reply_id, status, outcome)
            return outcome

    async def _applied(self, reply: ReceivedReply) -> tuple[ReplyStatus, str]:
        if reply.option is None:
            channel = (
                self._registry.channel(reply.channel_id) if reply.channel_id is not None else None
            )
            if channel is None:
                return ReplyStatus.STALE, "The channel was deleted."
            if reply.instance_id in channel.muted:
                return ReplyStatus.STALE, "This instance was already muted on this channel."
            self._registry.mute(channel.channel_id, reply.instance_id)
            return ReplyStatus.APPLIED, "Muted this instance on this channel."
        if self._registry.answered(reply.notification_id):
            return ReplyStatus.STALE, "Already answered."
        await self._start_turn(
            reply.instance_id,
            ThreadTurnStartCommand(thread_id=reply.thread_id, message=reply.option),
        )
        return ReplyStatus.APPLIED, f'Answered "{reply.option}".'

    async def _edit(self, reply: ReceivedReply, outcome: str) -> None:
        channel = self._registry.channel(reply.channel_id) if reply.channel_id is not None else None
        if channel is None or reply.message is None:
            return
        try:
            editor = self._channels.sender(channel)
            if isinstance(editor, ReceivingKind):
                await editor.edit(reply.message, outcome)
        except Exception:
            _logger.exception('Channel "%s" did not edit its message.', channel.name)
