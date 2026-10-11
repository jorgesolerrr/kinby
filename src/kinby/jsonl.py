"""Read and append the records of a JSONL log, where a record counts once its newline is written.

A write cut short leaves a torn tail: the bytes after the last newline. Only the process that
owns the log may append, because the append cuts that tail off before writing.
"""

import logging
import os
from collections.abc import Iterator
from pathlib import Path

_logger = logging.getLogger(__name__)


def whole_lines(path: Path) -> Iterator[str]:
    """Each line of *path* that ends in a newline, in order. A missing file has none."""
    try:
        records = path.open("rb")
    except FileNotFoundError:
        return
    with records:
        for line in records:
            if line.endswith(b"\n"):
                yield line.decode()


def append_line(path: Path, line: str) -> None:
    """Write *line* as the last record of *path*, dropping a torn tail first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as records:
        end = records.seek(0, os.SEEK_END)
        records.seek(max(end - 1, 0))
        if records.read(1) not in (b"", b"\n"):
            records.seek(0)
            content = records.read()
            whole = content.rfind(b"\n") + 1
            torn = content[whole:].decode(errors="replace")
            _logger.warning("Dropped the torn last line of %s: %s", path, torn)
            records.truncate(whole)
        records.write(f"{line}\n".encode())
