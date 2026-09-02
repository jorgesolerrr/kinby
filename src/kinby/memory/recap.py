"""Write narrative recaps and deterministic tool traces after turns close."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field  # noqa: TID251 - model output boundary

from kinby.contracts import (
    MemoryRecapped,
    MessageDelta,
    NodeId,
    TokenTotals,
    ToolCall,
    ToolResult,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnStarted,
    Warning,
)
from kinby.instance import Instance, RecapPolicy, reload_manifest
from kinby.memory.facade import Episode, Memory, new_node_id

if TYPE_CHECKING:
    from kinby.contracts import Event
    from kinby.core.events import EventLog

logger = logging.getLogger(__name__)
_TOOL_CALL_SUMMARY_MAX_CHARS = 160
_TOOL_RESULT_MAX_CHARS = 800
_DEFAULT_RECAP_LENS = (
    "Describe the turn's concrete outcome and decisions. "
    "Name one honest way the work could have gone differently."
)


class RecapDraft(BaseModel):
    """The narrative fields returned by the recap model."""

    model_config = ConfigDict(extra="forbid")

    keep: bool = Field(description="Whether this turn is useful enough to keep as an episode.")
    description: str = Field(
        min_length=1,
        description="A short, searchable description of the turn.",
    )
    subjects: tuple[str, ...] = Field(description="Names and topics the episode is about.")
    happened: str = Field(description="What happened during the turn.")
    decided: str = Field(description="What was decided during the turn.")
    retrospective: str = Field(description="What should have gone differently.")


class StructuredRecap(Protocol):
    async def ainvoke(self, messages: Sequence[BaseMessage]) -> object: ...


class RecapModel(Protocol):
    def with_structured_output(
        self,
        schema: type[RecapDraft],
        *,
        include_raw: bool,
    ) -> StructuredRecap: ...


type RecapModelFactory = Callable[[str], RecapModel]


def _init_model(model: str) -> RecapModel:
    return cast(RecapModel, init_chat_model(model))


@dataclass(frozen=True)
class _RecapRequest:
    thread_id: UUID
    turn_id: UUID


class RecapWriter:
    """Queue and write one episode at a time after turns close."""

    def __init__(
        self,
        event_log: EventLog,
        memory: Memory,
        instance: Instance,
        *,
        model_factory: RecapModelFactory = _init_model,
        model_override: str | None = None,
    ) -> None:
        self._event_log = event_log
        self._memory = memory
        self._instance = instance
        self._model_factory = model_factory
        self._model_override = model_override
        self._queue: asyncio.Queue[_RecapRequest] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None

    def schedule(self, thread_id: UUID, turn_id: UUID) -> None:
        """Queue a closed turn without waiting for its recap."""
        self._queue.put_nowait(_RecapRequest(thread_id, turn_id))
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._work())

    async def catch_up(self) -> None:
        """Queue every closed turn that has no recap marker."""
        events = await asyncio.to_thread(lambda: list(self._event_log.all_events()))
        closed: list[_RecapRequest] = []
        covered: set[_RecapRequest] = set()
        for event in events:
            request = _RecapRequest(event.thread_id, event.turn_id)
            if isinstance(event.payload, MemoryRecapped):
                covered.add(request)
            elif isinstance(event.payload, TurnCompleted | TurnFailed | TurnInterrupted):
                closed.append(request)
        for request in closed:
            if request not in covered:
                self.schedule(request.thread_id, request.turn_id)

    async def drain(self) -> None:
        """Wait until every queued recap has finished."""
        await self._queue.join()
        if self._worker is not None:
            await self._worker

    async def _work(self) -> None:
        while not self._queue.empty():
            request = await self._queue.get()
            try:
                await self._write(request)
            except Exception as exc:
                logger.exception("The turn recap failed.")
                await self._event_log.append(
                    request.thread_id,
                    request.turn_id,
                    Warning(
                        sources=("recap",),
                        message=(f"The turn recap failed: {type(exc).__name__}: {exc}"),
                    ),
                )
            finally:
                self._queue.task_done()

    async def _write(self, request: _RecapRequest) -> None:
        events = await asyncio.to_thread(
            self._turn_events,
            request.thread_id,
            request.turn_id,
        )
        if self._is_recapped(events):
            return
        calls = [event.payload for event in events if isinstance(event.payload, ToolCall)]
        manifest = reload_manifest(self._instance, model_override=self._model_override)
        if manifest.memory.recap is RecapPolicy.TRACE_ONLY:
            await self._write_trace_only(request, events, calls)
            return

        draft, usage = await self._draft(events, calls, manifest.models.recap)
        episode = (
            _episode(
                request,
                description=draft.description,
                subjects=draft.subjects,
                body=_episode_body(draft, calls),
                calls=calls,
            )
            if draft.keep
            else None
        )
        await self._finish(request, episode, usage)

    async def _write_trace_only(
        self,
        request: _RecapRequest,
        events: list[Event],
        calls: list[ToolCall],
    ) -> None:
        episode: Episode | None = None
        if calls:
            started = next(
                event.payload for event in events if isinstance(event.payload, TurnStarted)
            )
            description = " ".join(started.message.split()) or "Turn used tools"
            episode = _episode(
                request,
                description=description,
                subjects=(),
                body=_path_taken(calls),
                calls=calls,
            )
        await self._finish(request, episode, TokenTotals(input_tokens=0, output_tokens=0))

    async def _finish(
        self,
        request: _RecapRequest,
        episode: Episode | None,
        usage: TokenTotals,
    ) -> None:
        node: NodeId | None = None
        if episode is not None:
            node = await asyncio.to_thread(self._memory.remember, episode)
        await self._event_log.append(
            request.thread_id,
            request.turn_id,
            MemoryRecapped(
                node=node,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
            ),
        )

    async def _draft(
        self,
        events: list[Event],
        calls: list[ToolCall],
        model_name: str,
    ) -> tuple[RecapDraft, TokenTotals]:
        model = self._model_factory(model_name)
        runnable = model.with_structured_output(RecapDraft, include_raw=True)
        result = await runnable.ainvoke((SystemMessage(content=_recap_frame(events, calls)),))
        if not isinstance(result, Mapping):
            raise TypeError("The recap model returned an invalid structured response.")
        parsing_error = result.get("parsing_error")
        if isinstance(parsing_error, BaseException):
            raise parsing_error
        parsed = result.get("parsed")
        if not isinstance(parsed, RecapDraft):
            raise TypeError("The recap model returned an invalid draft.")
        raw = result.get("raw")
        if not isinstance(raw, AIMessage):
            raise TypeError("The recap model returned no token usage.")
        usage = raw.usage_metadata
        return parsed, TokenTotals(
            input_tokens=usage["input_tokens"] if usage is not None else 0,
            output_tokens=usage["output_tokens"] if usage is not None else 0,
        )

    def _turn_events(self, thread_id: UUID, turn_id: UUID) -> list[Event]:
        return [event for event in self._event_log.stored(thread_id) if event.turn_id == turn_id]

    @staticmethod
    def _is_recapped(events: Iterable[Event]) -> bool:
        return any(isinstance(event.payload, MemoryRecapped) for event in events)


def _path_taken(calls: list[ToolCall]) -> str:
    steps = [f"{number}. {_summarize_call(call)}" for number, call in enumerate(calls, 1)]
    return "## Path taken\n" + "\n".join(steps)


def _episode(
    request: _RecapRequest,
    *,
    description: str,
    subjects: tuple[str, ...],
    body: str,
    calls: list[ToolCall],
) -> Episode:
    recorded_on = date.today()
    return Episode(
        node=new_node_id(recorded_on, description),
        date=recorded_on,
        thread=request.thread_id,
        description=description,
        subjects=subjects,
        body=body,
        turn=request.turn_id,
        tools=tuple(dict.fromkeys(call.name for call in calls)),
    )


def _episode_body(draft: RecapDraft, calls: list[ToolCall]) -> str:
    return "\n".join(
        (
            "## What happened",
            draft.happened.strip(),
            "## What was decided",
            draft.decided.strip(),
            "## What should have gone differently",
            draft.retrospective.strip(),
            _path_taken(calls),
        )
    )


def _recap_frame(events: list[Event], calls: list[ToolCall]) -> str:
    return (
        "Write a retrospective draft for this turn. Do not write facts. "
        "Set keep to false for trivial chat with no durable outcome or reusable path.\n\n"
        f"# Recap lens\n{_DEFAULT_RECAP_LENS}\n\n"
        f"# Turn events\n{_render_turn(events, calls)}\n\n"
        f"# Deterministic trace\n{_path_taken(calls)}\n\n"
        "# Output contract\n"
        "Return whether to keep the episode, its description and subjects, and text for "
        "What happened, What was decided, and What should have gone differently."
    )


def _render_turn(events: list[Event], calls: list[ToolCall]) -> str:
    started = next(event.payload for event in events if isinstance(event.payload, TurnStarted))
    assistant_text = "".join(
        event.payload.text for event in events if isinstance(event.payload, MessageDelta)
    )
    results = {
        event.payload.call_id: event.payload
        for event in events
        if isinstance(event.payload, ToolResult)
    }
    sections = [
        f"# User message\n{started.message}",
        f"# Assistant text\n{assistant_text}",
    ]
    for call in calls:
        result = results.get(call.call_id)
        rendered_result = "No result was recorded."
        if result is not None:
            rendered_result = _truncate_result(result.output)
            if result.error:
                rendered_result = f"Error: {rendered_result}"
        sections.append(
            f"# Tool call {call.name}\n"
            f"Arguments: {json.dumps(call.arguments, ensure_ascii=False, sort_keys=True)}\n"
            f"Result: {rendered_result}"
        )
    closing = next(
        event.payload
        for event in reversed(events)
        if isinstance(event.payload, TurnCompleted | TurnFailed | TurnInterrupted)
    )
    match closing:
        case TurnCompleted(input_tokens=input_tokens, output_tokens=output_tokens):
            rendered_closing = (
                f"Completed: input_tokens={input_tokens} output_tokens={output_tokens}"
            )
        case TurnFailed(code=code, message=message):
            rendered_closing = f"Failed: code={code.value} message={message}"
        case TurnInterrupted():
            rendered_closing = "Interrupted"
    sections.append(f"# Closing event\n{rendered_closing}")
    return "\n\n".join(sections)


def _truncate_result(output: str) -> str:
    if len(output) <= _TOOL_RESULT_MAX_CHARS:
        return output
    return f"{output[:_TOOL_RESULT_MAX_CHARS]}..."


def _summarize_call(call: ToolCall) -> str:
    arguments = json.dumps(call.arguments, ensure_ascii=False, sort_keys=True)
    summary = f"{call.name}: {arguments}"
    if len(summary) <= _TOOL_CALL_SUMMARY_MAX_CHARS:
        return summary
    return f"{summary[: _TOOL_CALL_SUMMARY_MAX_CHARS - 3]}..."
