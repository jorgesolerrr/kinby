"""Land a file the user gives a turn in the workspace, under the day's uploads folder
(ADR 0132)."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from datetime import datetime
from itertools import chain, count
from pathlib import Path

from kinby.contracts import UploadPath, UploadPutCommand, UploadPutResult

_UPLOADS = "uploads"
#: What a file name may not hold on any system the workspace may sit on.
_UNSAFE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


class InstanceUploads:
    def __init__(self, workspace: Path, clock: Callable[[], datetime]) -> None:
        self._workspace = workspace
        self._clock = clock

    async def put(self, command: UploadPutCommand) -> UploadPutResult:
        folder = self._workspace / _UPLOADS / self._clock().date().isoformat()
        path = await asyncio.to_thread(_write, folder, _safe_name(command.name), command.content)
        return UploadPutResult(path=UploadPath(path.relative_to(self._workspace).as_posix()))


def names_upload(workspace: Path, path: UploadPath) -> bool:
    """Whether *path* is a file under the workspace's uploads."""
    landed = (workspace / path).resolve()
    return landed.is_relative_to((workspace / _UPLOADS).resolve()) and landed.is_file()


def _safe_name(name: str) -> str:
    """*name*'s last part, with what a path can't hold replaced."""
    safe = _UNSAFE.sub("_", name.replace("\\", "/").rsplit("/", 1)[-1]).strip()
    return safe if safe.strip(".") else "upload"


def _write(folder: Path, name: str, content: bytes) -> Path:
    """Write *content* as *name* in *folder*, numbered before its extension past any file
    already there."""
    folder.mkdir(parents=True, exist_ok=True)
    stem, suffix = Path(name).stem, Path(name).suffix
    names = chain((name,), (f"{stem} ({number}){suffix}" for number in count(2)))
    while True:
        path = folder / next(names)
        try:
            with path.open("xb") as file:
                file.write(content)
        except FileExistsError:
            continue
        return path
