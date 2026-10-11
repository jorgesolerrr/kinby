"""Read and append the records of a JSONL log, where a record counts once its newline is written.

A write cut short leaves a torn tail: the bytes after the last newline. Only the process that
owns the log may append, because the append cuts that tail off before writing. Its threads append
one at a time for the same reason.
"""

import logging
import os
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from threading import Lock

_logger = logging.getLogger(__name__)
_appending = Lock()


def whole_lines(path: Path) -> Iterator[str]:
    """Each line of *path* that ends in a newline, in order. A missing file has none.

    One unbuffered read takes the file up to its size when opened. A read that resumed after an
    append dropped a torn tail would join that tail to the record written in its place.
    """
    try:
        records = path.open("rb", buffering=0)
    except FileNotFoundError:
        return
    with records:
        content = records.read(os.fstat(records.fileno()).st_size)
    for line in BytesIO(content):
        if line.endswith(b"\n"):
            yield line.decode()


def append_line(path: Path, line: str) -> None:
    """Write *line* as the last record of *path*, dropping a torn tail first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _appending, path.open("a+b") as records:
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
