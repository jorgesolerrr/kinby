"""Send each notification once through every channel, and try again for a day (ADR 0103)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from kinby.contracts import NotificationRaiseCommand, NotificationRaiseResult
from kinby.core.errors import ManagedInstanceNotFound
from kinby.hub.channels import Channels
from kinby.hub.models import ChannelNotification
from kinby.hub.registry import DeliveryStatus, HubRegistry, PendingDelivery
from kinby.hub.runs import BACKOFF_CAP_SECONDS, BACKOFF_SECONDS

#: How long after a notification was raised the outbox stops trying a channel that fails.
GIVE_UP_AFTER = timedelta(days=1)

type Sleep = Callable[[float], Awaitable[None]]


class Outbox:
    """Record the notifications instances raise, and deliver each to every channel that sends."""

    def __init__(
        self,
        registry: HubRegistry,
        channels: Channels,
        public_url: str | None,
        clock: Callable[[], datetime],
        sleep: Sleep,
    ) -> None:
        """*public_url* is where the user reaches the hub's web app, which links point into."""
        self._registry = registry
        self._channels = channels
        self._public_url = public_url
        self._clock = clock
        self._sleep = sleep
        self._tasks: set[asyncio.Task[None]] = set()

    async def raise_notification(
        self, instance_id: UUID, command: NotificationRaiseCommand
    ) -> NotificationRaiseResult:
        """Record the notification under the hub's header and link, once per key, and deliver it.

        A channel that mutes the instance records it as muted and never sends it.
        """
        record = self._registry.instance(instance_id)
        if record is None:
            raise ManagedInstanceNotFound(f'Instance "{instance_id}" was not found.')
        notification = ChannelNotification(
            notification_id=uuid4(),
            header=f"{record.persona_name or record.manifest_id} · {command.source}",
            title=command.title,
            text=command.text,
            link=(
                f"{self._public_url}/instances/{instance_id}/threads/{command.thread_id}"
                if self._public_url is not None
                else None
            ),
            options=tuple(command.options),
        )
        deliveries = {
            channel.channel_id: (
                DeliveryStatus.MUTED if instance_id in channel.muted else DeliveryStatus.PENDING
            )
            for channel in self._registry.channels()
            if channel.sends
        }
        raised = self._registry.raise_notification(
            instance_id, command.key, notification, self._clock(), deliveries
        )
        if raised == notification.notification_id:
            self._deliver_all(self._registry.pending_deliveries(raised))
        return NotificationRaiseResult(notification_id=raised)

    def resume(self) -> None:
        """Deliver what the previous process left pending, each when its next try is due."""
        self._deliver_all(self._registry.pending_deliveries())

    def _deliver_all(self, deliveries: Iterable[PendingDelivery]) -> None:
        for delivery in deliveries:
            task = asyncio.create_task(self._deliver(delivery))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _deliver(self, delivery: PendingDelivery) -> None:
        notification = delivery.notification
        attempts = delivery.attempts
        wait = (delivery.next_attempt - self._clock()).total_seconds()
        while True:
            if wait > 0:
                await self._sleep(wait)
            channel = self._registry.channel(delivery.channel_id)
            if channel is None:
                return
            if not channel.sends:
                self._registry.delivery_failed(
                    notification.notification_id, channel.channel_id, "The channel was disabled."
                )
                return
            try:
                limit = self._channels.limit(channel)
                sent = await self._channels.sender(channel).send(
                    replace(notification, text=_cut(notification.text, limit))
                )
            except Exception as exc:
                attempts += 1
                error = str(exc) or type(exc).__name__
                now = self._clock()
                self._registry.channel_failed(channel.channel_id, error, now)
                if now - delivery.raised_at >= GIVE_UP_AFTER:
                    self._registry.delivery_failed(
                        notification.notification_id, channel.channel_id, error
                    )
                    return
                wait = min(BACKOFF_SECONDS * 2 ** (attempts - 1), BACKOFF_CAP_SECONDS)
                self._registry.delivery_retried(
                    notification.notification_id,
                    channel.channel_id,
                    error,
                    now + timedelta(seconds=wait),
                )
            else:
                self._registry.delivery_sent(notification.notification_id, channel.channel_id, sent)
                self._registry.channel_sent(channel.channel_id, self._clock())
                return


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"
