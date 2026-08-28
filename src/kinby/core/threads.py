"""Keep thread identity available when no session process is running."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from kinby.contracts import ThreadCreateResult, ThreadListResult, ThreadSummary

THREADS_NAME = "threads.jsonl"


class ThreadStore:
    def __init__(self, state_dir: Path) -> None:
        self._path = state_dir / THREADS_NAME

    def create(self, title: str | None) -> ThreadCreateResult:
        thread = ThreadSummary(
            id=uuid4(),
            title=title,
            created_at=datetime.now(UTC),
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as records:
            records.write(f"{thread.model_dump_json()}\n")
        return ThreadCreateResult(id=thread.id, created_at=thread.created_at)

    def list(self) -> ThreadListResult:
        if not self._path.exists():
            return ThreadListResult(threads=[])
        with self._path.open(encoding="utf-8") as records:
            threads = [ThreadSummary.model_validate_json(line) for line in records]
        return ThreadListResult(threads=threads)

    def exists(self, thread_id: UUID) -> bool:
        return any(thread.id == thread_id for thread in self.list().threads)
