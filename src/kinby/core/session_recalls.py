"""The recalls of a factory run's coding session.

Only the runtime appends to the event log, so ``kinby memory search`` in the session keeps its
recall in the database apart from the events.
"""

from __future__ import annotations

import os
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from kinby.contracts import FactoryRunOrigin, MemoryRecalled, NodeId, StepId
from kinby.instance.database import open_database

_RUN = "KINBY_RUN"
_STEP = "KINBY_STEP"
_ATTEMPT = "KINBY_ATTEMPT"


def session_environment(origin: FactoryRunOrigin, attempt: int | None) -> dict[str, str]:
    """What a coding session's environment names of the run, step and attempt it works in."""
    named = {_RUN: str(origin.run_id), _STEP: origin.step}
    return named if attempt is None else named | {_ATTEMPT: str(attempt)}


@dataclass(frozen=True)
class SessionAttempt:
    """The step attempt of a factory run that a coding session works in."""

    run_id: UUID
    step: StepId
    attempt: int | None


def session_attempt() -> SessionAttempt | None:
    """The step attempt this process runs under, as its environment names it. None outside a
    factory run's coding session."""
    run = os.environ.get(_RUN)
    if run is None:
        return None
    attempt = os.environ.get(_ATTEMPT)
    return SessionAttempt(UUID(run), os.environ[_STEP], None if attempt is None else int(attempt))


def keep_recall(state_dir: Path, nodes: tuple[NodeId, ...]) -> None:
    """Keep the recall of *nodes* for the client step this process runs under. Outside a factory
    run's coding session, or with no nodes, keep nothing."""
    session = session_attempt()
    if session is None or not nodes:
        return
    recall = MemoryRecalled(nodes=nodes, step=session.step, attempt=session.attempt)
    with closing(open_database(state_dir)) as db:
        db.execute(
            "INSERT INTO recalls VALUES (?, ?)", (str(session.run_id), recall.model_dump_json())
        )
