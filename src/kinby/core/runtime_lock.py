"""Keep one instance runtime writing an instance's event log, as ADR 0006 requires."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from kinby.instance.layout import RUNTIME_LOCK_NAME


class InstanceBusyError(RuntimeError):
    """Another process is already running this instance."""


@contextmanager
def runtime_lock(state_dir: Path) -> Iterator[None]:
    """Claim the one writer an instance allows, and release it on the way out."""
    lock = state_dir / RUNTIME_LOCK_NAME
    holder = _holder(lock)
    if holder is not None:
        raise InstanceBusyError(
            f"Process {holder} is already running this instance. "
            "Stop it before starting another, so only one writes the event log."
        )
    state_dir.mkdir(parents=True, exist_ok=True)
    lock.write_text(f"{os.getpid()}\n", encoding="utf-8")
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _holder(lock: Path) -> int | None:
    """The process the lock file names, once a stale file has been ruled out."""
    try:
        pid = int(lock.read_text(encoding="utf-8").strip())
    except OSError, ValueError:
        return None
    return pid if _running(pid) else None


def _running(pid: int) -> bool:
    """Signal 0 asks the operating system about a process without touching it."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
