"""Write deterministic tool traces after turns close."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from kinby.contracts import MemoryRecapped, NodeId, ToolCall, TurnStarted, Warning
from kinby.memory.facade import Episode, Memory, new_node_id

if TYPE_CHECKING:
    from kinby.contracts import Event
    from kinby.core.events import EventLog

logger = logging.getLogger(__name__)
_TOOL_CALL_SUMMARY_MAX_CHARS = 160


@dataclass(frozen=True)
class _RecapRequest:
    thread_id: UUID
    turn_id: UUID


class RecapWriter:
    """Queue and write one trace-only episode at a time."""

    def __init__(self, event_log: EventLog, memory: Memory) -> None:
        self._event_log = event_log
        self._memory = memory
        self._queue: asyncio.Queue[_RecapRequest] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None

    def schedule(self, thread_id: UUID, turn_id: UUID) -> None:
        """Queue a closed turn without waiting for its recap."""
        if self._is_recapped(self._turn_events(thread_id, turn_id)):
            return
        self._queue.put_nowait(_RecapRequest(thread_id, turn_id))
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._work())

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
        events = self._turn_events(request.thread_id, request.turn_id)
        if self._is_recapped(events):
            return
        calls = [event.payload for event in events if isinstance(event.payload, ToolCall)]
        node: NodeId | None = None
        if calls:
            started = next(
                event.payload for event in events if isinstance(event.payload, TurnStarted)
            )
            description = " ".join(started.message.split()) or "Turn used tools"
            recorded_on = date.today()
            episode = Episode(
                node=new_node_id(recorded_on, description),
                date=recorded_on,
                thread=request.thread_id,
                description=description,
                subjects=(),
                body=_path_taken(calls),
                turn=request.turn_id,
                tools=tuple(dict.fromkeys(call.name for call in calls)),
            )
            node = await asyncio.to_thread(self._memory.remember, episode)
        await self._event_log.append(
            request.thread_id,
            request.turn_id,
            MemoryRecapped(node=node, input_tokens=0, output_tokens=0),
        )

    def _turn_events(self, thread_id: UUID, turn_id: UUID) -> list[Event]:
        return [event for event in self._event_log.stored(thread_id) if event.turn_id == turn_id]

    @staticmethod
    def _is_recapped(events: Iterable[Event]) -> bool:
        return any(isinstance(event.payload, MemoryRecapped) for event in events)


def _path_taken(calls: list[ToolCall]) -> str:
    steps = [f"{number}. {_summarize_call(call)}" for number, call in enumerate(calls, 1)]
    return "## Path taken\n" + "\n".join(steps)


def _summarize_call(call: ToolCall) -> str:
    arguments = json.dumps(call.arguments, ensure_ascii=False, sort_keys=True)
    summary = f"{call.name}: {arguments}"
    if len(summary) <= _TOOL_CALL_SUMMARY_MAX_CHARS:
        return summary
    return f"{summary[: _TOOL_CALL_SUMMARY_MAX_CHARS - 3]}..."
