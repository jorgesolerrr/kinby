"""Keep one instance runtime writing an instance's event log, as ADR 0006 requires."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from kinby.instance.layout import RUNTIME_LOCK_NAME

_OPEN = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC
_held: set[Path] = set()


class InstanceBusyError(RuntimeError):
    """Another process is already running this instance."""


@contextmanager
def runtime_lock(state_dir: Path) -> Iterator[None]:
    """Claim the one writer an instance allows, and release it on the way out."""
    state_dir.mkdir(parents=True, exist_ok=True)
    lock = (state_dir / RUNTIME_LOCK_NAME).resolve()
    if lock in _held:
        raise InstanceBusyError(_busy(os.getpid()))
    fd = os.open(lock, _OPEN, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        holder = _pid(fd)
        os.close(fd)
        raise InstanceBusyError(_busy(holder)) from None
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    _held.add(lock)
    try:
        yield
    finally:
        _held.discard(lock)
        os.close(fd)


def _busy(holder: int | None) -> str:
    who = f"Process {holder}" if holder is not None else "Another process"
    return (
        f"{who} is already running this instance. "
        "Stop it before starting another, so only one writes the event log."
    )


def _pid(fd: int) -> int | None:
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        return int(os.read(fd, 32).decode().strip())
    except ValueError:
        return None
