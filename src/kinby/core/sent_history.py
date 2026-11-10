"""A thread's history as the model is sent it: earlier turns' large tool results cleared
(ADR 0122), and the latest compaction's summary in place of the messages before its boundary
(ADR 0123). The checkpoint keeps every message. A thread whose context size passes the
compaction trigger compacts at the start of its next turn, and a turn whose next model call would
not fit the model's window compacts before it."""

from collections.abc import Sequence
from itertools import takewhile
from typing import TypeGuard

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage

from kinby.contracts import Event, ModelCompleted, ThreadCompacted, TurnStarted, is_turn_closing

#: The size over which a tool result from an earlier turn reaches the model cleared.
_CLEARED_OVER = 1_000
#: What a compaction keeps whole: about 20,000 tokens, at four characters a token.
_KEPT_CHARACTERS = 80_000
#: What the effective compaction trigger leaves free of the model's window, in tokens.
_WINDOW_RESERVE = 16_000
COMPACTION_PROMPT = """\
Summarize the conversation below so that the work can go on from your summary alone. The \
conversation's newest turns follow your summary whole, so cover only what is below.

Write these sections:
- Goal: what the user is trying to get done.
- Decisions: what was decided, and why.
- Constraints: what the user asked for or ruled out, and limits the work found.
- Progress: what is done, with the names, paths, numbers and results it depends on.
- Next steps: what was about to happen, and what is still open.

Keep names, paths, numbers and commitments exactly as written. A tool result can be read again, \
so say which tool gave it rather than copying it. Skill instructions read in the conversation are \
not sent again: name each skill, so it can be read again when the work needs it."""


def sent_history(
    messages: Sequence[AnyMessage], compaction: ThreadCompacted | None, cut: int | None = None
) -> list[AnyMessage]:
    """*messages* as the model is sent them under *compaction*, the thread's latest. Its summary
    also stands in for the running turn's messages before *cut*, when it cut into the turn."""
    if compaction is None:
        return clear_earlier_results(messages)
    summary = HumanMessage(
        content=f"This thread was compacted. A summary of its earlier turns:\n\n"
        f"{compaction.summary}"
    )
    kept = compaction.boundary if cut is None else cut
    return [summary, *clear_earlier_results(messages[kept:])]


def effective_trigger(trigger: int | None, window: int | None) -> int | None:
    """The context size past which a thread compacts at the start of a turn: the instance's
    compaction *trigger*, or the model's *window* less the reserve when that is smaller. None when
    automatic compaction is off."""
    if trigger is None or window is None:
        return trigger
    return min(trigger, window - _WINDOW_RESERVE)


def context_size(events: Sequence[Event]) -> int | None:
    """The context size of the thread whose *events* these are: the input tokens of the latest
    model call its turns made. A recap's call after its turn closed is not one. None when its
    turns made none, or the thread compacted since."""
    in_turn = False
    for event in reversed(events):
        payload = event.payload
        if isinstance(payload, ThreadCompacted):
            return None
        if isinstance(payload, TurnStarted):
            in_turn = False
        elif is_turn_closing(payload):
            in_turn = True
        elif isinstance(payload, ModelCompleted) and in_turn:
            return payload.input_tokens
    return None


def passes_window(
    window: int | None, turn_events: Sequence[Event], messages: Sequence[AnyMessage]
) -> bool:
    """Whether a running turn's next model call, which sends *messages*, would pass the model's
    *window* less the reserve: its latest call's input and output, with the tool results since at
    four characters a token. False with no known window, before the turn's first model call, or
    when the thread compacted since that call."""
    latest = next(
        (
            event.payload
            for event in reversed(turn_events)
            if isinstance(event.payload, ModelCompleted | ThreadCompacted)
        ),
        None,
    )
    if window is None or not isinstance(latest, ModelCompleted):
        return False
    results = takewhile(lambda message: isinstance(message, ToolMessage), reversed(messages))
    added = sum(len(result.text) for result in results) // 4
    return latest.input_tokens + latest.output_tokens + added > window - _WINDOW_RESERVE


def clear_earlier_results(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
    """*messages* with each tool result over the threshold from before the current turn's user
    message replaced by a stub."""
    start = turn_start(messages)
    return [
        _cleared(message) if index < start and _clears(message) else message
        for index, message in enumerate(messages)
    ]


def turn_start(messages: Sequence[AnyMessage]) -> int:
    """Where the current turn begins in *messages*: at its user message."""
    return max(
        (index for index, message in enumerate(messages) if isinstance(message, HumanMessage)),
        default=0,
    )


def unanswered(messages: Sequence[AnyMessage]) -> int:
    """Where the messages the model has not answered yet begin: the user message, or the latest
    tool calls with their results."""
    return next(
        index
        for index in range(len(messages) - 1, -1, -1)
        if not isinstance(messages[index], ToolMessage)
    )


def kept_from(messages: Sequence[AnyMessage], start: int, running: int | None = None) -> int:
    """Where the turns a compaction keeps begin: the newest whole turns from *start* on that fit
    in what it keeps, as they are sent: cleared, but for the turn running from *running*.
    ``len(messages)`` when none fits."""
    kept = len(messages)
    size = 0
    for index in range(len(messages) - 1, start - 1, -1):
        whole = running is not None and index >= running
        size += _sent_characters(messages[index], whole=whole)
        if size > _KEPT_CHARACTERS:
            break
        if isinstance(messages[index], HumanMessage):
            kept = index
    return kept


def summary_request(
    previous: str | None, messages: Sequence[AnyMessage], focus: str | None
) -> HumanMessage:
    """The one message that asks the model for a compaction summary of *messages*, which follow
    the *previous* summary."""
    conversation = [_transcript_line(message) for message in messages]
    if previous is not None:
        conversation.insert(0, f"Summary of the turns before these:\n{previous}")
    asked = COMPACTION_PROMPT if focus is None else f"{COMPACTION_PROMPT}\n\nFocus on: {focus}"
    return HumanMessage(
        content=f"{asked}\n\n<conversation>\n" + "\n\n".join(conversation) + "\n</conversation>"
    )


def _transcript_line(message: AnyMessage) -> str:
    match message:
        case HumanMessage():
            return f"User: {message.text}"
        case AIMessage():
            said = [f"Assistant: {message.text}"] if message.text else []
            calls = [f"Called {call['name']} with {call['args']}" for call in message.tool_calls]
            return "\n".join([*said, *calls])
        case ToolMessage():
            sent = _cleared(message) if _clears(message) else message
            return f"{message.name} returned: {sent.text}"
        case _:
            return message.text


def _sent_characters(message: AnyMessage, *, whole: bool) -> int:
    sent = _cleared(message) if not whole and _clears(message) else message
    calls = message.tool_calls if isinstance(message, AIMessage) else []
    return len(sent.text) + sum(len(str(call["args"])) for call in calls)


def _clears(message: AnyMessage) -> TypeGuard[ToolMessage]:
    return isinstance(message, ToolMessage) and len(message.text) > _CLEARED_OVER


def _cleared(result: ToolMessage) -> ToolMessage:
    stub = (
        f"[{result.name} result cleared: {len(result.text):,} characters from an earlier turn. "
        f"Call {result.name} again if you need it.]"
    )
    return result.model_copy(update={"content": stub})
