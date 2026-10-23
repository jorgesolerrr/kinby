"""The step result an instance holds for each run, so it answers a step call once per run, step
and attempt however often the hub sends it (ADR 0107)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID

from pydantic import TypeAdapter

from kinby.contracts import StepEnding, StepId, StepResult, StepRunCommand

_RESTARTED = StepResult(ending=StepEnding.FAILED, summary="The instance restarted during the step.")


@dataclass(frozen=True)
class _Held:
    """A run's latest step call, and its result once it has one."""

    step: StepId
    attempt: int
    result: StepResult | None = None


_HELD = TypeAdapter(_Held)


class HeldResults:
    """The latest step call of each run, kept on disk until the run's next call."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._running: dict[UUID, tuple[_Held, asyncio.Task[StepResult]]] = {}

    async def answer(
        self, command: StepRunCommand, run: Callable[[], Awaitable[StepResult]]
    ) -> StepResult:
        """Run the step call once per attempt, and answer each send of it with that run's result.

        A call still running is waited for. One started before the instance restarted, and never
        finished, answers failed. A call that is no attempt runs each time it comes, and lets go
        of what its run held.
        """
        run_id = command.origin.run_id
        if command.attempt is None:
            self._path(run_id).unlink(missing_ok=True)
            return await run()
        called = _Held(step=command.origin.step, attempt=command.attempt)
        running = self._running.get(run_id)
        if running is not None and running[0] == called:
            return await asyncio.shield(running[1])
        held = self._read(run_id)
        if held is not None and (held.step, held.attempt) == (called.step, called.attempt):
            return held.result or _RESTARTED
        self._write(run_id, called)
        task = asyncio.create_task(self._run(run_id, called, run))
        self._running[run_id] = (called, task)
        return await asyncio.shield(task)

    async def _run(
        self, run_id: UUID, called: _Held, run: Callable[[], Awaitable[StepResult]]
    ) -> StepResult:
        """Run the call and hold its result, unless a later call of the run replaced it. A call
        that raised, as one a draining instance refuses, holds nothing."""
        try:
            result = await run()
        except BaseException:
            if self._latest(run_id, called):
                self._path(run_id).unlink(missing_ok=True)
            raise
        else:
            if self._latest(run_id, called):
                self._write(run_id, replace(called, result=result))
            return result
        finally:
            if self._latest(run_id, called):
                del self._running[run_id]

    def _latest(self, run_id: UUID, called: _Held) -> bool:
        running = self._running.get(run_id)
        return running is not None and running[0] == called

    def _read(self, run_id: UUID) -> _Held | None:
        try:
            return _HELD.validate_json(self._path(run_id).read_bytes())
        except FileNotFoundError:
            return None

    def _write(self, run_id: UUID, held: _Held) -> None:
        """Replace what the run holds in one step, so a restart never reads half of it."""
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._path(run_id)
        written = path.with_suffix(".tmp")
        written.write_bytes(_HELD.dump_json(held))
        os.replace(written, path)

    def _path(self, run_id: UUID) -> Path:
        return self._directory / f"{run_id}.json"
