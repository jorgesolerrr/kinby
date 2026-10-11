"""Read and append the records of a JSONL log, where a record counts once its newline is written.

A write cut short leaves a torn tail: the bytes after the last newline. Only the process that
owns the log may append, because the append cuts that tail off before writing. Its threads append
one at a time for the same reason.
"""

import logging
import os
from collections.abc import Iterator
from pathlib import Path
from threading import Lock
from typing import BinaryIO

_logger = logging.getLogger(__name__)
_appending = Lock()
_BLOCK = 8192


def whole_lines(path: Path) -> Iterator[str]:
    """Each line of *path* that ends in a newline, in order. A missing file has none.

    The read stops at the last newline the file had when opened. An append never changes what
    comes before a newline, so a torn tail it drops meanwhile is never read.
    """
    try:
        records = path.open("rb")
    except FileNotFoundError:
        return
    with records:
        unread = _whole_end(records, os.fstat(records.fileno()).st_size)
        records.seek(0)
        for line in records:
            unread -= len(line)
            if unread < 0:
                return
            yield line.decode()


def append_line(path: Path, line: str) -> None:
    """Write *line* as the last record of *path*, dropping a torn tail first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _appending, path.open("a+b") as records:
        end = records.seek(0, os.SEEK_END)
        whole = _whole_end(records, end)
        if whole < end:
            records.seek(whole)
            torn = records.read().decode(errors="replace")
            _logger.warning("Dropped the torn last line of %s: %s", path, torn)
            records.truncate(whole)
        records.write(f"{line}\n".encode())


def _whole_end(records: BinaryIO, size: int) -> int:
    """Where the last whole line in the first *size* bytes of *records* ends, 0 when it has none."""
    end = size
    while end > 0:
        start = max(end - _BLOCK, 0)
        records.seek(start)
        newline = records.read(end - start).rfind(b"\n")
        if newline >= 0:
            return start + newline + 1
        end = start
    return 0
