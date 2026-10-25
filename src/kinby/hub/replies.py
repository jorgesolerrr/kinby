"""Record each reply the user sends at once, then apply it to what holds by then (ADR 0103)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime
from uuid import UUID, uuid4

from aiohttp import web

from kinby.contracts import (
    APPROVAL_OPTIONS,
    ApprovalDecision,
    NotificationReply,
    NotificationReplyCommand,
    ReplyStatus,
    ThreadTurnStartCommand,
    WaitingApproval,
)
from kinby.core.errors import (
    CoreError,
    InstanceAnswerLost,
    NotificationNotFound,
    OptionNotOffered,
)
from kinby.hub.channels import Channels, ReceivingKind, Reply
from kinby.hub.outbox import GIVE_UP_AFTER, Sleep
from kinby.hub.registry import ChannelRecord, HubRegistry, ReceivedReply, RunStopRef
from kinby.hub.runs import BACKOFF_CAP_SECONDS, BACKOFF_SECONDS

_logger = logging.getLogger(__name__)
_UNKNOWN = "Not known whether the instance got this answer. Answer again from the web app."
_DECIDED = {ApprovalDecision.APPROVE: "Approved.", ApprovalDecision.DENY: "Denied."}

#: Start a user turn in one of an instance's threads.
type StartTurn = Callable[[UUID, ThreadTurnStartCommand], Awaitable[object]]
#: Answer an approval waiting in one of an instance's threads. False when it no longer waits.
type AnswerApproval = Callable[[UUID, UUID, WaitingApproval, ApprovalDecision], Awaitable[bool]]
#: Move a run on from its stop with one of its notification's options. None when it moved since.
type AnswerStop = Callable[[RunStopRef, str], Awaitable[str | None]]


class Replies:
    """Take the user's replies to notifications, and apply them one at a time.

    A reply to an instance's notification starts a user turn in the thread that raised it, one to
    an approval's answers that approval, one to a run that needs human retries, sends back or
    cancels that run, and "Mute this instance" mutes the instance on the channel the reply came
    through. One that answers something already settled changes nothing. Either way, the
    channel's message is edited to show what came of it.
    """

    def __init__(
        self,
        registry: HubRegistry,
        channels: Channels,
        start_turn: StartTurn,
        answer_approval: AnswerApproval,
        answer_stop: AnswerStop,
        clock: Callable[[], datetime],
        sleep: Sleep,
    ) -> None:
        self._registry = registry
        self._channels = channels
        self._start_turn = start_turn
        self._answer_approval = answer_approval
        self._answer_stop = answer_stop
        self._clock = clock
        self._sleep = sleep
        self._applying = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    async def receive(self, channel_id: str, kind: str, request: web.Request) -> web.Response:
        """Answer a call to a channel's public route as soon as the reply it carries is
        recorded."""
        return await self._channels.receive(channel_id, kind, request, self._take)

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

    def _take(self, channel: ChannelRecord, reply: Reply) -> None:
        notification = self._registry.sent_notification(channel.channel_id, reply.message)
        if notification is None:
            return
        # The channel sent the notification's options with "Mute this instance" after them.
        options = [*notification.options, None]
        if 0 <= reply.option < len(options):
            self._record(notification.notification_id, channel.channel_id, options[reply.option])

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
        if reply.stop is not None:
            outcome = await self._answer_stop(reply.stop, reply.option)
            if outcome is None:
                return ReplyStatus.STALE, "The run already moved."
            return ReplyStatus.APPLIED, outcome
        if reply.thread_id is None:
            return ReplyStatus.STALE, "No thread takes this answer."
        if self._registry.answered(reply.notification.notification_id):
            return ReplyStatus.STALE, "Already answered."
        # An answer is asked for at most once: one that may have landed is never repeated.
        if reply.calling:
            return ReplyStatus.FAILED, _UNKNOWN
        self._registry.reply_calling(reply.reply_id, calling=True)
        try:
            if reply.approval is None:
                await self._start_turn(
                    reply.instance_id,
                    ThreadTurnStartCommand(thread_id=reply.thread_id, message=reply.option),
                )
                return ReplyStatus.APPLIED, f'Answered "{reply.option}".'
            decision = APPROVAL_OPTIONS[reply.option]
            if not await self._answer_approval(
                reply.instance_id, reply.thread_id, reply.approval, decision
            ):
                return ReplyStatus.STALE, "No longer waiting."
            return ReplyStatus.APPLIED, _DECIDED[decision]
        except InstanceAnswerLost:
            return ReplyStatus.FAILED, _UNKNOWN
        except Exception:
            self._registry.reply_calling(reply.reply_id, calling=False)
            raise

    async def _edit(self, reply: ReceivedReply, outcome: str) -> None:
        channel = self._registry.channel(reply.channel_id) if reply.channel_id is not None else None
        if channel is None or reply.message is None:
            return
        try:
            editor = self._channels.sender(channel)
            if isinstance(editor, ReceivingKind):
                await editor.edit(reply.message, reply.notification, outcome)
        except Exception:
            _logger.exception('Channel "%s" did not edit its message.', channel.name)
