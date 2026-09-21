import os
import subprocess
from multiprocessing import Barrier, Process, Queue
from pathlib import Path
from typing import Protocol

import pytest

from kinby.core.runtime_lock import InstanceBusyError, runtime_lock
from kinby.instance.layout import RUNTIME_LOCK_NAME


class _Gate(Protocol):
    def wait(self) -> object: ...


class _Claims(Protocol):
    def put(self, item: str, /) -> None: ...
    def get_nowait(self) -> str: ...


def _race_for_lock(state_dir: str, barrier: _Gate, results: _Claims) -> None:
    barrier.wait()
    try:
        with runtime_lock(Path(state_dir)):
            results.put("held")
            barrier.wait()
    except InstanceBusyError:
        results.put("busy")
        barrier.wait()


def test_only_one_process_holds_the_runtime_lock(tmp_path: Path) -> None:
    barrier = Barrier(3)
    results: _Claims = Queue()
    processes = [
        Process(target=_race_for_lock, args=(str(tmp_path), barrier, results)) for _ in range(2)
    ]
    for process in processes:
        process.start()
    barrier.wait()
    barrier.wait()
    for process in processes:
        process.join(timeout=5)
        assert process.exitcode == 0

    claimed = [results.get_nowait() for _ in range(2)]
    assert claimed.count("held") == 1
    assert claimed.count("busy") == 1


def test_a_process_releases_only_the_lock_it_owns(tmp_path: Path) -> None:
    other = subprocess.Popen(["sleep", "30"])
    lock = tmp_path / RUNTIME_LOCK_NAME
    try:
        with runtime_lock(tmp_path):
            lock.write_text(f"{other.pid}\n", encoding="utf-8")
        assert lock.read_text(encoding="utf-8") == f"{other.pid}\n"
    finally:
        other.kill()
        other.wait()


def test_a_stale_runtime_lock_does_not_block_the_next_runtime(tmp_path: Path) -> None:
    dead = subprocess.Popen(["true"])
    assert dead.wait() == 0
    lock = tmp_path / RUNTIME_LOCK_NAME
    lock.write_text(f"{dead.pid}\n", encoding="utf-8")

    with runtime_lock(tmp_path):
        assert lock.read_text(encoding="utf-8") == f"{os.getpid()}\n"

    assert not lock.exists()


def test_a_live_holder_still_refuses_a_second_runtime(tmp_path: Path) -> None:
    with (
        runtime_lock(tmp_path),
        pytest.raises(InstanceBusyError, match=str(os.getpid())),
        runtime_lock(tmp_path),
    ):
        pass
