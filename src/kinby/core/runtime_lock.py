"""Keep one instance runtime writing an instance's event log, as ADR 0006 requires."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from kinby.instance.layout import RUNTIME_LOCK_NAME

_CREATE = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC


class InstanceBusyError(RuntimeError):
    """Another process is already running this instance."""


@contextmanager
def runtime_lock(state_dir: Path) -> Iterator[None]:
    """Claim the one writer an instance allows, and release it on the way out."""
    state_dir.mkdir(parents=True, exist_ok=True)
    lock = state_dir / RUNTIME_LOCK_NAME
    pid = os.getpid()
    while True:
        try:
            fd = os.open(lock, _CREATE, 0o644)
        except FileExistsError:
            holder = _holder(lock)
            if holder is not None:
                raise InstanceBusyError(
                    f"Process {holder} is already running this instance. "
                    "Stop it before starting another, so only one writes the event log."
                ) from None
            if not _remove_stale(lock):
                raise InstanceBusyError(
                    "Another process is already running this instance. "
                    "Stop it before starting another, so only one writes the event log."
                ) from None
            continue
        try:
            os.write(fd, f"{pid}\n".encode())
        finally:
            os.close(fd)
        break
    try:
        yield
    finally:
        _release(lock, pid)


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


def _remove_stale(lock: Path) -> bool:
    """Drop a lock file whose process is gone, if it still names that process."""
    try:
        text = lock.read_text(encoding="utf-8")
        pid = int(text.strip())
    except OSError, ValueError:
        return _unlink(lock)
    if _running(pid):
        return False
    try:
        if lock.read_text(encoding="utf-8") != text:
            return True
    except OSError:
        return True
    return _unlink(lock)


def _release(lock: Path, pid: int) -> None:
    try:
        if int(lock.read_text(encoding="utf-8").strip()) != pid:
            return
    except OSError, ValueError:
        return
    _unlink(lock)


def _unlink(lock: Path) -> bool:
    try:
        lock.unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True
