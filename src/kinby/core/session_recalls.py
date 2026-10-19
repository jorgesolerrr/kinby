"""The recalls of a factory run's coding session, kept until its client step records them.

Only the runtime appends to the event log, so ``kinby memory search`` in the session keeps its
recall beside the log, and the step moves it onto the run's thread once the session ends.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

from kinby.contracts import FactoryRunOrigin, MemoryRecalled, NodeId
from kinby.jsonl import append_line, whole_lines

_RUN = "KINBY_RUN"
_STEP = "KINBY_STEP"
_ATTEMPT = "KINBY_ATTEMPT"
_RECALLS_DIR = "recalls"


def session_environment(origin: FactoryRunOrigin, attempt: int | None) -> dict[str, str]:
    """What a coding session's environment names of the run, step and attempt it works in."""
    named = {_RUN: str(origin.run_id), _STEP: origin.step}
    return named if attempt is None else named | {_ATTEMPT: str(attempt)}


def keep_recall(state_dir: Path, nodes: tuple[NodeId, ...]) -> None:
    """Keep the recall of *nodes* for the client step this process runs under. Outside a factory
    run's coding session, or with no nodes, keep nothing."""
    run = os.environ.get(_RUN)
    if run is None or not nodes:
        return
    attempt = os.environ.get(_ATTEMPT)
    recall = MemoryRecalled(
        nodes=nodes,
        step=os.environ[_STEP],
        attempt=None if attempt is None else int(attempt),
    )
    append_line(_recalls(state_dir, UUID(run)), recall.model_dump_json())


def take_recalls(state_dir: Path, run_id: UUID) -> list[MemoryRecalled]:
    """The recalls kept for *run_id*, in the order kept. None are kept after."""
    path = _recalls(state_dir, run_id)
    recalls = [MemoryRecalled.model_validate_json(line) for line in whole_lines(path)]
    path.unlink(missing_ok=True)
    return recalls


def _recalls(state_dir: Path, run_id: UUID) -> Path:
    return state_dir / _RECALLS_DIR / f"{run_id}.jsonl"
