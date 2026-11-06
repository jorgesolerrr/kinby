"""The recalls of a factory run's coding session, kept until its client step records them.

Only the runtime appends to the event log, so ``kinby memory search`` in the session keeps its
recall in the instance database apart from the events, and the step moves it onto the run's thread
once the session ends.
"""

from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path
from uuid import UUID

from kinby.contracts import FactoryRunOrigin, MemoryRecalled, NodeId
from kinby.instance.database import open_database

_RUN = "KINBY_RUN"
_STEP = "KINBY_STEP"
_ATTEMPT = "KINBY_ATTEMPT"


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
    with closing(open_database(state_dir)) as db:
        db.execute("INSERT INTO recalls VALUES (?, ?)", (str(UUID(run)), recall.model_dump_json()))


def take_recalls(state_dir: Path, run_id: UUID) -> list[MemoryRecalled]:
    """The recalls kept for *run_id*, in the order kept. None are kept after."""
    with closing(open_database(state_dir)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        kept = db.execute(
            "SELECT recall FROM recalls WHERE run_id = ? ORDER BY rowid", (str(run_id),)
        ).fetchall()
        db.execute("DELETE FROM recalls WHERE run_id = ?", (str(run_id),))
    return [MemoryRecalled.model_validate_json(recall) for (recall,) in kept]
