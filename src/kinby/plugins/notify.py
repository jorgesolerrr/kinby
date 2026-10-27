"""The notify tool: a turn tells the user something through the hub's channels (ADR 0103)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from kinby.contracts import (
    APPROVAL_OPTIONS,
    NOTIFICATION_RAISE,
    ApprovalDecision,
    ApprovalRequested,
    Event,
    NotificationAccepted,
    NotificationRaiseCommand,
    NotificationRaised,
    NotificationRaiseResult,
    NotificationRefused,
    Scope,
    TakeoverRequested,
    WaitingApproval,
)
from kinby.plugins.hub import HUB_SCOPES_VARIABLE, HUB_URL_VARIABLE, call_hub
from kinby.plugins.tools import NotificationReporter, Tool, ToolContext, tool

#: The source of a notification from a thread without a title.
UNTITLED = "Untitled thread"
#: How much of each argument's value an approval's notification shows.
_ARGUMENT_LENGTH = 80


def raises_notifications() -> bool:
    """Whether the instance's hub grants it notification:raise on its hub route."""
    granted = os.environ.get(HUB_SCOPES_VARIABLE, "").split(",")
    return bool(os.environ.get(HUB_URL_VARIABLE)) and Scope.NOTIFICATION_RAISE in granted


def notify_tools() -> tuple[Tool, ...]:
    """The notify tool, in an instance that raises notifications."""
    return (notify,) if raises_notifications() else ()


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
        source=context.notification_source or UNTITLED,
        title=title,
        text=text,
        options=options or [],
    )
    answer = await raise_notification(raised, context.report_notification)
    if answer is None:
        return "The hub is out of reach. kinby raises the notification again once it is back."
    if isinstance(answer, NotificationRefused):
        raise ValueError(answer.reason)
    return "The hub took the notification and sends it through every channel."


async def raise_notification(
    raised: NotificationRaised, report: NotificationReporter
) -> NotificationAccepted | NotificationRefused | None:
    """Record the notification in the turn, raise it on the hub route, and record the hub's
    answer. None while the hub is out of reach, so a scheduler pass raises it again."""
    event = await report(raised)
    answer = await raise_recorded(
        RecordedNotification(event.thread_id, event.turn_id, event.sequence, raised)
    )
    if answer is not None:
        await report(answer)
    return answer


def approval_notification(
    source: str, turn_id: UUID, request: ApprovalRequested, *, in_routine: bool
) -> NotificationRaised:
    """kinby's notification for an approval a turn parked on: the tool and its arguments on one
    line, each value cut short, and the options that answer it. Only a routine's turn offers to
    allow the tool in the routine."""
    arguments = ", ".join(
        f"{name}={_shown(value if isinstance(value, str) else json.dumps(value))}"
        for name, value in request.arguments.items()
    )
    return NotificationRaised(
        source=source,
        title="Approval waiting",
        text=f"{request.name}: {arguments}",
        options=[
            option
            for option, decision in APPROVAL_OPTIONS.items()
            if in_routine or decision is not ApprovalDecision.ALLOW_IN_ROUTINE
        ],
        approval=WaitingApproval(turn_id=turn_id, request=request),
    )


def takeover_notification(source: str, request: TakeoverRequested) -> NotificationRaised:
    """kinby's notification for a takeover a turn parked on: its reason. The user takes over in
    the web app, which the notification links to."""
    return NotificationRaised(source=source, title="Takeover waiting", text=request.reason)


def _shown(value: str) -> str:
    line = " ".join(value.split())
    return line if len(line) <= _ARGUMENT_LENGTH else f"{line[: _ARGUMENT_LENGTH - 1]}…"


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
        approval=raised.approval,
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
