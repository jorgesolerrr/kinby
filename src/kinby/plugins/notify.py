"""The notify tool: a turn tells the user something through the hub's channels (ADR 0103)."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from kinby.contracts import (
    NOTIFICATION_RAISE,
    Event,
    NotificationAccepted,
    NotificationRaiseCommand,
    NotificationRaised,
    NotificationRaiseResult,
    NotificationRefused,
    Scope,
)
from kinby.plugins.hub import HUB_SCOPES_VARIABLE, HUB_URL_VARIABLE, call_hub
from kinby.plugins.tools import Tool, ToolContext, tool


def notify_tools() -> tuple[Tool, ...]:
    """The notify tool, in an instance its hub grants notification:raise on its hub route."""
    granted = os.environ.get(HUB_SCOPES_VARIABLE, "").split(",")
    if not os.environ.get(HUB_URL_VARIABLE) or Scope.NOTIFICATION_RAISE not in granted:
        return ()
    return (notify,)


@tool(write=True, notify=True)
async def notify(
    title: str, text: str, context: ToolContext, options: list[str] | None = None
) -> str:
    """Tell the user something now, through every channel the hub sends to, such as their phone.

    Notify only when the user needs to know: something they asked to hear about happened, or
    something needs them. text says what happened and why you are telling them. kinby heads it
    with your name and where it came from. options, up to four short labels, are what the user
    can reply with; a reply starts a turn in this thread with the option as its message. The hub
    takes at most 20 notifications from this instance a day.
    """
    raised = NotificationRaised(
        source=context.notification_source or "Untitled thread",
        title=title,
        text=text,
        options=options or [],
    )
    event = await context.report_notification(raised)
    answer = await raise_recorded(
        RecordedNotification(event.thread_id, event.turn_id, event.sequence, raised)
    )
    if answer is None:
        return "The hub is out of reach. kinby raises the notification again once it is back."
    await context.report_notification(answer)
    if isinstance(answer, NotificationRefused):
        raise ValueError(answer.reason)
    return "The hub took the notification and sends it through every channel."


@dataclass(frozen=True)
class RecordedNotification:
    """A notification.raised event: the notification, and where it sits in the event log."""

    thread_id: UUID
    turn_id: UUID
    sequence: int
    raised: NotificationRaised


def pending_notifications(events: Iterable[Event]) -> list[RecordedNotification]:
    """The notifications raised that the hub has not answered yet."""
    pending: dict[tuple[UUID, int], RecordedNotification] = {}
    for event in events:
        match event.payload:
            case NotificationRaised() as raised:
                pending[event.thread_id, event.sequence] = RecordedNotification(
                    event.thread_id, event.turn_id, event.sequence, raised
                )
            case NotificationAccepted(raised=sequence) | NotificationRefused(raised=sequence):
                pending.pop((event.thread_id, sequence), None)
    return list(pending.values())


async def raise_recorded(
    recorded: RecordedNotification,
) -> NotificationAccepted | NotificationRefused | None:
    """Raise a recorded notification on this instance's hub route, keyed by its event. The hub's
    answer, or None while the instance has no hub route or cannot reach it."""
    if not os.environ.get(HUB_URL_VARIABLE):
        return None
    raised = recorded.raised
    command = NotificationRaiseCommand(
        key=f"{recorded.thread_id}:{recorded.sequence}",
        thread_id=recorded.thread_id,
        source=raised.source,
        title=raised.title,
        text=raised.text,
        options=raised.options,
    )
    try:
        result = await call_hub(
            NOTIFICATION_RAISE.name, command.model_dump(mode="json"), "the notification"
        )
    except ConnectionError:
        return None
    except ValueError as exc:
        return NotificationRefused(raised=recorded.sequence, reason=str(exc))
    return NotificationAccepted(
        raised=recorded.sequence,
        notification_id=NotificationRaiseResult.model_validate(result).notification_id,
    )
