"""A thread's history as the model is sent it: earlier turns' large tool results cleared
(ADR 0122), and the latest compaction's summary in place of the messages before its boundary
(ADR 0123). The checkpoint keeps every message."""

from collections.abc import Sequence
from typing import TypeGuard

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage

from kinby.contracts import ThreadCompacted

#: The size over which a tool result from an earlier turn reaches the model cleared.
_CLEARED_OVER = 1_000
#: What a compaction keeps whole: about 20,000 tokens, at four characters a token.
_KEPT_CHARACTERS = 80_000
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
    messages: Sequence[AnyMessage], compaction: ThreadCompacted | None
) -> list[AnyMessage]:
    """*messages* as the model is sent them under *compaction*, the thread's latest."""
    if compaction is None:
        return clear_earlier_results(messages)
    summary = HumanMessage(
        content=f"This thread was compacted. A summary of its earlier turns:\n\n"
        f"{compaction.summary}"
    )
    return [summary, *clear_earlier_results(messages[compaction.boundary :])]


def clear_earlier_results(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
    """*messages* with each tool result over the threshold from before the current turn's user
    message replaced by a stub."""
    turn_start = max(
        (index for index, message in enumerate(messages) if isinstance(message, HumanMessage)),
        default=0,
    )
    return [
        _cleared(message) if index < turn_start and _clears(message) else message
        for index, message in enumerate(messages)
    ]


def kept_from(messages: Sequence[AnyMessage], start: int) -> int:
    """Where the turns a compaction keeps begin: the newest whole turns from *start* on that fit
    in what it keeps, as they are sent cleared. ``len(messages)`` when none fits."""
    kept = len(messages)
    size = 0
    for index in range(len(messages) - 1, start - 1, -1):
        size += _sent_characters(messages[index])
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


def _sent_characters(message: AnyMessage) -> int:
    sent = _cleared(message) if _clears(message) else message
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
