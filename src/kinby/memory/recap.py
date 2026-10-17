"""Write narrative recaps and deterministic tool traces after turns close and factory runs stop."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from pathlib import Path
from textwrap import indent
from typing import TYPE_CHECKING, Literal, Protocol, cast
from uuid import UUID, uuid5

from langchain_core.exceptions import ModelNotFoundError
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import (
    BaseModel,  # noqa: TID251 - model output boundary
    ConfigDict,
    Field,
    field_validator,
)

from kinby.contracts import (
    CompletionOutcome,
    MemoryRecapped,
    MessageDelta,
    NodeId,
    RunDelegated,
    RunStop,
    RunStopped,
    StepAttempt,
    TokenTotals,
    ToolCall,
    ToolResult,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnStarted,
    Warning,
    is_turn_closing,
)
from kinby.core.model_calls import completed_model_call
from kinby.core.models import init_model
from kinby.instance import Instance, RecapPolicy, reload_manifest
from kinby.instance.layout import CLAUDE_DIR, GRAPH_DIR, MEMORY_DIR
from kinby.instance.recap import load_recap_lens
from kinby.memory.facade import Episode, Memory, RecappedRun, RecappedTurn, new_node_id
from kinby.memory.session_digest import MissingSession, SessionDigest, digest_session
from kinby.plugins.hub import hub_read

if TYPE_CHECKING:
    from kinby.contracts import Event
    from kinby.core.events import EventLog

logger = logging.getLogger(__name__)
_TOOL_CALL_SUMMARY_MAX_CHARS = 160
_TRACE_DESCRIPTION_MAX_CHARS = 200
_TOOL_RESULT_MAX_CHARS = 800


class RecapDraft(BaseModel):
    """The narrative fields returned by the recap model."""

    model_config = ConfigDict(extra="forbid")

    keep: bool = Field(description="Whether this turn is useful enough to keep as an episode.")
    description: str = Field(description="A short, searchable description of the turn.")
    subjects: tuple[str, ...] = Field(description="Names and topics the episode is about.")
    happened: str = Field(description="What happened during the turn.")
    decided: str = Field(description="What was decided during the turn.")
    retrospective: str = Field(description="What should have gone differently.")

    # A validator, not min_length: OpenAI's strict JSON schema mode refuses length keywords.
    @field_validator("description")
    @classmethod
    def non_empty_description(cls, value: str) -> str:
        if not value:
            raise ValueError("must not be empty")
        return value


class RecapModelNotFound(LookupError):
    def __init__(self, model: str) -> None:
        super().__init__(f"The recap model {model} doesn't exist.")


class StructuredRecap(Protocol):
    async def ainvoke(self, messages: Sequence[BaseMessage]) -> object: ...


class RecapModel(Protocol):
    def with_structured_output(
        self,
        schema: type[RecapDraft],
        *,
        method: Literal["json_schema"],
        include_raw: bool,
    ) -> StructuredRecap: ...


type RecapModelFactory = Callable[[str], RecapModel]


def _init_model(model: str) -> RecapModel:
    return cast(RecapModel, init_model(model))


@dataclass(frozen=True)
class _TurnRecap:
    thread_id: UUID
    turn_id: UUID


@dataclass(frozen=True)
class _RunRecap:
    """A factory run stop to recap. Its events go under the run, as a turn's go under its thread."""

    run_id: UUID
    generation: int

    @property
    def thread_id(self) -> UUID:
        return self.run_id

    @property
    def turn_id(self) -> UUID:
        """The same for every recap of this stop, so its marker is found again."""
        return uuid5(self.run_id, str(self.generation))


type _RecapRequest = _TurnRecap | _RunRecap


class RecapWriter:
    """Queue and write one episode at a time after turns close and factory runs stop."""

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
        self._enqueue(_TurnRecap(thread_id, turn_id))

    def schedule_run_stop(self, run_id: UUID, generation: int) -> None:
        """Queue a factory run stop without waiting for its recap."""
        self._enqueue(_RunRecap(run_id, generation))

    def _enqueue(self, request: _RecapRequest) -> None:
        self._queue.put_nowait(request)
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._work())

    async def catch_up(self) -> None:
        """Queue every closed turn and factory run stop that has no recap marker."""
        events = await asyncio.to_thread(lambda: list(self._event_log.all_events()))
        await asyncio.to_thread(
            _delete_no_work_traces,
            self._instance.path / MEMORY_DIR / GRAPH_DIR,
            events,
        )
        ended: list[_RecapRequest] = []
        covered: set[tuple[UUID, UUID]] = set()
        for event in events:
            if isinstance(event.payload, MemoryRecapped):
                covered.add((event.thread_id, event.turn_id))
            elif isinstance(event.payload, RunStopped):
                ended.append(_RunRecap(event.payload.stop.run_id, event.payload.stop.generation))
            elif is_turn_closing(event.payload):
                ended.append(_TurnRecap(event.thread_id, event.turn_id))
        for request in ended:
            if (request.thread_id, request.turn_id) not in covered:
                self._enqueue(request)

    async def drain(self) -> None:
        """Wait until every queued recap has finished."""
        await self._queue.join()
        if self._worker is not None:
            await self._worker

    async def _work(self) -> None:
        while not self._queue.empty():
            request = await self._queue.get()
            recapped = "turn" if isinstance(request, _TurnRecap) else "run"
            try:
                match request:
                    case _TurnRecap():
                        await self._write_turn(request)
                    case _RunRecap():
                        await self._write_run(request)
            except Exception as exc:
                logger.exception("The %s recap failed.", recapped)
                message = (
                    str(exc)
                    if isinstance(exc, RecapModelNotFound)
                    else f"The {recapped} recap failed: {type(exc).__name__}: {exc}"
                )
                await self._event_log.append(
                    request.thread_id,
                    request.turn_id,
                    Warning(sources=("recap",), message=message),
                )
            finally:
                self._queue.task_done()

    async def _write_turn(self, request: _TurnRecap) -> None:
        events = await asyncio.to_thread(
            self._turn_events,
            request.thread_id,
            request.turn_id,
        )
        if self._is_recapped(events):
            return
        if any(
            isinstance(event.payload, TurnCompleted)
            and event.payload.outcome is CompletionOutcome.NO_WORK
            for event in events
        ):
            await self._finish(request, None, TokenTotals(input_tokens=0, output_tokens=0), None)
            return
        calls = [event.payload for event in events if isinstance(event.payload, ToolCall)]
        manifest = reload_manifest(self._instance, model_override=self._model_override)
        if manifest.memory.recap is RecapPolicy.TRACE_ONLY:
            await self._write_trace_only(request, events, calls, manifest.models.recap)
            return

        lens = load_recap_lens(self._instance.path)
        draft, usage = await self._draft(
            request,
            _recap_frame(events, calls, lens.text),
            manifest.models.recap,
        )
        episode = (
            _episode(
                request,
                description=draft.description,
                subjects=draft.subjects,
                body=f"{_narrative(draft)}\n{_path_taken(calls)}",
                calls=calls,
                recorded_on=_recorded_on(events),
            )
            if draft.keep
            else None
        )
        await self._finish(request, episode, usage, manifest.models.recap)

    async def _write_run(self, request: _RunRecap) -> None:
        events = await asyncio.to_thread(self._event_log.stored, request.run_id)
        if self._is_recapped(event for event in events if event.turn_id == request.turn_id):
            return
        stops = {
            event.payload.stop.generation: (event.payload.stop, event.timestamp.date())
            for event in events
            if isinstance(event.payload, RunStopped)
        }
        stop, recorded_on = stops[request.generation]
        manifest = reload_manifest(self._instance, model_override=self._model_override)
        digests = await asyncio.to_thread(
            _session_digests, manifest.state_dir / CLAUDE_DIR, stop, events
        )
        if manifest.memory.recap is RecapPolicy.TRACE_ONLY:
            changed = dict.fromkeys(
                path
                for digest in digests
                if isinstance(digest, SessionDigest)
                for path in digest.changed
            )
            body = f"## What happened\nThe run stopped as {stop.status}."
            if changed:
                body = f"{body}\n## Files changed\n{_bullets(changed)}"
            episode = _run_episode(
                request,
                stop,
                description=_shorten(_run_description(stop), _TRACE_DESCRIPTION_MAX_CHARS),
                subjects=(),
                body=body,
                digests=digests,
                recorded_on=recorded_on,
            )
            usage = TokenTotals(input_tokens=0, output_tokens=0)
            await self._finish(request, episode, usage, manifest.models.recap)
            return
        lens = load_recap_lens(self._instance.path)
        reached = [attempt for each, _ in stops.values() for attempt in each.attempts]
        draft, usage = await self._draft(
            request,
            _run_frame(stop, _send_backs(stop, reached), digests, lens.text),
            manifest.models.recap,
        )
        # A run stop always leaves an episode, whatever the shared draft says about keeping it.
        episode = _run_episode(
            request,
            stop,
            description=draft.description,
            subjects=draft.subjects,
            body=_narrative(draft),
            digests=digests,
            recorded_on=recorded_on,
        )
        await self._finish(request, episode, usage, manifest.models.recap)

    async def _write_trace_only(
        self,
        request: _TurnRecap,
        events: list[Event],
        calls: list[ToolCall],
        recap: str,
    ) -> None:
        episode: Episode | None = None
        if calls:
            started = next(
                event.payload for event in events if isinstance(event.payload, TurnStarted)
            )
            first_line = next(iter(started.message.strip().splitlines()), "")
            description = _shorten(
                " ".join(first_line.split()) or "Turn used tools",
                _TRACE_DESCRIPTION_MAX_CHARS,
            )
            episode = _episode(
                request,
                description=description,
                subjects=(),
                body=_path_taken(calls),
                calls=calls,
                recorded_on=_recorded_on(events),
            )
        await self._finish(
            request,
            episode,
            TokenTotals(input_tokens=0, output_tokens=0),
            recap,
        )

    async def _finish(
        self,
        request: _RecapRequest,
        episode: Episode | None,
        usage: TokenTotals,
        model: str | None,
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
                cache_read_tokens=usage.cache_read_tokens,
                cache_creation_tokens=usage.cache_creation_tokens,
                model=model,
            ),
        )

    async def _draft(
        self,
        request: _RecapRequest,
        frame: str,
        model_name: str,
    ) -> tuple[RecapDraft, TokenTotals]:
        model = self._model_factory(model_name)
        # Newer Anthropic models reject a forced tool_choice, so ask for native JSON output.
        runnable = model.with_structured_output(
            RecapDraft,
            method="json_schema",
            include_raw=True,
        )
        started_at = asyncio.get_running_loop().time()
        try:
            result = await runnable.ainvoke((HumanMessage(content=frame),))
        except ModelNotFoundError as exc:
            raise RecapModelNotFound(model_name) from exc
        if not isinstance(result, Mapping):
            raise TypeError("The recap model returned an invalid structured response.")
        raw = result.get("raw")
        if not isinstance(raw, AIMessage):
            raise TypeError("The recap model returned no token usage.")
        completed = completed_model_call(
            model_name,
            raw.usage_metadata,
            started_at=started_at,
            completed_at=asyncio.get_running_loop().time(),
        )
        await self._event_log.append(
            request.thread_id,
            request.turn_id,
            completed,
        )
        parsing_error = result.get("parsing_error")
        if isinstance(parsing_error, BaseException):
            raise parsing_error
        parsed = result.get("parsed")
        if not isinstance(parsed, RecapDraft):
            raise TypeError("The recap model returned an invalid draft.")
        return parsed, completed

    def _turn_events(self, thread_id: UUID, turn_id: UUID) -> list[Event]:
        return [event for event in self._event_log.stored(thread_id) if event.turn_id == turn_id]

    @staticmethod
    def _is_recapped(events: Iterable[Event]) -> bool:
        return any(isinstance(event.payload, MemoryRecapped) for event in events)


# Temporary: remove once the deployed instances have started with it (#558).
def _delete_no_work_traces(graph: Path, events: list[Event]) -> None:
    """Delete the trace episodes ADR 0021 had no-work turns write, which ADR 0074 retired."""
    no_work = {
        (event.thread_id, event.turn_id)
        for event in events
        if isinstance(event.payload, TurnCompleted)
        and event.payload.outcome is CompletionOutcome.NO_WORK
    }
    for event in events:
        if isinstance(event.payload, TurnStarted) and (event.thread_id, event.turn_id) in no_work:
            recorded_on = event.timestamp.date().isoformat()
            (graph / f"{recorded_on}-{event.turn_id.hex}-trace.md").unlink(missing_ok=True)


def _path_taken(calls: list[ToolCall]) -> str:
    steps = [f"{number}. {_summarize_call(call)}" for number, call in enumerate(calls, 1)]
    return "## Path taken\n" + "\n".join(steps)


def _recorded_on(events: list[Event]) -> date:
    started = next(event for event in events if isinstance(event.payload, TurnStarted))
    return started.timestamp.date()


def _episode(
    request: _TurnRecap,
    *,
    description: str,
    subjects: tuple[str, ...],
    body: str,
    calls: list[ToolCall],
    recorded_on: date,
) -> Episode:
    return Episode(
        node=new_node_id(recorded_on, description),
        date=recorded_on,
        description=description,
        subjects=subjects,
        body=body,
        recaps=RecappedTurn(request.thread_id, request.turn_id),
        tools=tuple(dict.fromkeys(call.name for call in calls)),
    )


def _run_episode(
    request: _RunRecap,
    stop: RunStop,
    *,
    description: str,
    subjects: tuple[str, ...],
    body: str,
    digests: list[SessionDigest | MissingSession],
    recorded_on: date,
) -> Episode:
    """The episode of *stop*, under an id a retried recap of the stop writes again."""
    return Episode(
        node=new_node_id(recorded_on, _run_description(stop), request.turn_id),
        date=recorded_on,
        description=description,
        subjects=subjects,
        body=body,
        recaps=RecappedRun(request.run_id, request.generation),
        tools=tuple(
            dict.fromkeys(
                tool
                for digest in digests
                if isinstance(digest, SessionDigest)
                for tool in digest.tools
            )
        ),
    )


def _narrative(draft: RecapDraft) -> str:
    return "\n".join(
        (
            "## What happened",
            draft.happened.strip(),
            "## What was decided",
            draft.decided.strip(),
            "## What should have gone differently",
            draft.retrospective.strip(),
        )
    )


def _recap_frame(events: list[Event], calls: list[ToolCall], lens: str) -> str:
    return (
        "Write a retrospective draft for this turn. Do not write facts. "
        "Set keep to false for trivial chat with no durable outcome or reusable path.\n\n"
        f"# Recap lens\n{lens}\n\n"
        f"# Turn events\n{_render_turn(events, calls)}\n\n"
        f"# Deterministic trace\n{_path_taken(calls)}\n\n"
        "# Output contract\n"
        "Return whether to keep the episode, its description and subjects, and text for "
        "What happened, What was decided, and What should have gone differently."
    )


def _run_frame(
    stop: RunStop,
    send_backs: list[tuple[StepAttempt, str]],
    digests: list[SessionDigest | MissingSession],
    lens: str,
) -> str:
    rendered = "\n\n".join(_render_digest(digest) for digest in digests) or "None."
    return (
        "Write a retrospective draft of this factory run, as code knowledge for the next session "
        "that works on the same area. Do not write facts. Set keep to true.\n\n"
        f"# Recap lens\n{lens}\n\n"
        f"# Run record\n{_render_run(stop, send_backs)}\n\n"
        f"# Session digests\n{rendered}\n\n"
        "# Output contract\n"
        "Return its description and subjects, and text for "
        "What happened (the work item, how the run ended, and the path taken: the main files "
        "read and changed), What was decided (each decision and its reason, from the "
        "assistant's text), and What should have gone differently (review pushback, check "
        "failures, dead ends). The review comments and the replies given are in the attempts' "
        "values. Name the area of the code in the description, and the area and its main paths "
        "in the subjects."
    )


def _session_digests(
    claude_dir: Path, stop: RunStop, events: list[Event]
) -> list[SessionDigest | MissingSession]:
    """A digest of each Claude session *stop*'s attempts ran in. Codex sessions have none yet."""
    codex = {
        event.payload.run.session
        for event in events
        if isinstance(event.payload, RunDelegated) and event.payload.run.client == "codex"
    }
    sessions = dict.fromkeys(
        attempt.session
        for attempt in stop.attempts
        if attempt.session is not None and attempt.session not in codex
    )
    return [digest_session(claude_dir, session) for session in sessions]


def _render_digest(digest: SessionDigest | MissingSession) -> str:
    heading = f"## Session {digest.session}"
    if isinstance(digest, MissingSession):
        return f"{heading}\nIts transcript is missing."
    failed = [
        f"- {_shorten(failure.command, _TOOL_CALL_SUMMARY_MAX_CHARS)}\n{indent(failure.tail, '  ')}"
        for failure in digest.failed
    ]
    return "\n".join(
        (
            heading,
            f"Files read:\n{_bullets(digest.read)}",
            f"Files changed:\n{_bullets(digest.changed)}",
            "Failed commands:\n" + ("\n".join(failed) or "None."),
            f"Memory searches:\n{_bullets(digest.searches)}",
            "Assistant text:\n" + "\n\n".join(digest.said),
        )
    )


def _bullets(items: Iterable[object]) -> str:
    return "\n".join(f"- {item}" for item in items) or "None."


def _render_run(stop: RunStop, send_backs: list[tuple[StepAttempt, str]]) -> str:
    attempts = []
    for number, attempt in enumerate(stop.attempts, 1):
        ending = f"{attempt.ending}"
        if attempt.outcome is not None:
            ending = f"{ending}, outcome {attempt.outcome}"
        rendered = (
            f"{number}. {attempt.step}, attempt {attempt.attempt}: {ending}. {attempt.summary}"
        )
        if attempt.values:
            values = json.dumps(attempt.values, ensure_ascii=False)
            rendered = f"{rendered}\n   Values: {values}"
        attempts.append(rendered)
    sent = [
        f"- {attempt.step}, attempt {attempt.attempt}, sent the work back to {back}: "
        f"{attempt.summary}"
        for attempt, back in send_backs
    ]
    return "\n".join(
        (
            f"Factory: {stop.factory}",
            f"Work item: {_work_item(stop)}",
            f"Status: {stop.status}",
            "",
            "## Attempts",
            *attempts,
            "",
            "## Send-backs",
            *(sent or ["None."]),
        )
    )


def _send_backs(stop: RunStop, reached: list[StepAttempt]) -> list[tuple[StepAttempt, str]]:
    """Each attempt of *stop* that sent the work back, with the step it went back to.

    The instance holds no factory file, so it orders the steps by when the run first reached
    each: a run reaches a step first from the one before it, and a send-back goes to an earlier
    step. An attempt followed by one at an earlier step sent the work back.
    """
    order = list(dict.fromkeys(attempt.step for attempt in reached))
    return [
        (attempt, following.step)
        for attempt, following in pairwise(stop.attempts)
        if order.index(following.step) < order.index(attempt.step)
    ]


def _work_item(stop: RunStop) -> str:
    return ", ".join(f"{name} {value}" for name, value in stop.work_item.items())


def _run_description(stop: RunStop) -> str:
    work_item = _work_item(stop)
    return f"{stop.factory} run on {work_item}" if work_item else f"{stop.factory} run"


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
        if call.name == hub_read.name:
            # What another instance holds stays out of this instance's memory.
            rendered_result = "Withheld: it was read through the hub."
        elif result is not None:
            rendered_result = _truncate_result(result.output)
            if result.error:
                rendered_result = f"Error: {rendered_result}"
        sections.append(
            f"# Tool call {call.name}\n"
            f"Arguments: {json.dumps(call.arguments, ensure_ascii=False, sort_keys=True)}\n"
            f"Result: {rendered_result}"
        )
    closing = next(event.payload for event in reversed(events) if is_turn_closing(event.payload))
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
    return _shorten(f"{call.name}: {arguments}", _TOOL_CALL_SUMMARY_MAX_CHARS)


def _shorten(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 3]}..."
