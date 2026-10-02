"""Keep each edit to an instance's configuration in an append-only log, apart from any thread."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from difflib import unified_diff
from itertools import islice
from pathlib import Path
from uuid import UUID

from kinby.contracts import ConfigActor, ConfigChange, ConfigFile, FileHash
from kinby.instance.dataclasses import Instance

CONFIG_CHANGES_NAME = "config-changes.jsonl"


class ConfigChangeLog:
    """The config changes of one instance, one JSON line each, oldest first on disk."""

    def __init__(self, state_dir: Path) -> None:
        self._path = state_dir / CONFIG_CHANGES_NAME

    def append(self, change: ConfigChange) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as log:
            log.write(f"{change.model_dump_json()}\n")

    def history(self, file: ConfigFile | None, limit: int) -> list[ConfigChange]:
        """The latest *limit* changes, newest first, to *file* or to any file."""
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        changes = (ConfigChange.model_validate_json(line) for line in reversed(lines))
        return list(islice((c for c in changes if file in (None, c.file)), limit))


@contextmanager
def recorded_change(
    instance: Instance,
    file: ConfigFile,
    actor: ConfigActor,
    *,
    thread_id: UUID | None = None,
    turn_id: UUID | None = None,
) -> Iterator[None]:
    """Record what the body changes in *file*, a file or a directory, once the body succeeds."""
    with recorded_changes(instance, (file,), actor, thread_id=thread_id, turn_id=turn_id):
        yield


@contextmanager
def recorded_changes(
    instance: Instance,
    files: Sequence[ConfigFile],
    actor: ConfigActor,
    *,
    thread_id: UUID | None = None,
    turn_id: UUID | None = None,
) -> Iterator[None]:
    """Record what the body changes in each of *files* as one change each, all at one time."""
    before = [_texts(instance.path, file) for file in files]
    yield
    at = datetime.now(UTC)
    log = ConfigChangeLog(instance.manifest.state_dir)
    for file, texts in zip(files, before, strict=True):
        log.append(
            ConfigChange(
                at=at,
                file=file,
                actor=actor,
                thread_id=thread_id,
                turn_id=turn_id,
                diff=_diff(texts, _texts(instance.path, file)),
                hash=_hash(instance.path / file),
            )
        )


def file_hash(content: bytes) -> FileHash:
    return FileHash(hashlib.sha256(content).hexdigest())


def directory_hash(directory: Path) -> FileHash:
    """Hash every file under *directory* by its relative path. A missing one hashes as empty."""
    files = directory.rglob("*") if directory.is_dir() else ()
    digest = hashlib.sha256()
    for relative, path in sorted(
        (path.relative_to(directory).as_posix(), path) for path in files if path.is_file()
    ):
        content = path.read_bytes()
        digest.update(f"{relative}\0{len(content)}\0".encode())
        digest.update(content)
    return FileHash(digest.hexdigest())


def _hash(path: Path) -> FileHash | None:
    """The hash the read of *path* returns, or None when nothing is there."""
    if path.is_file():
        return file_hash(path.read_bytes())
    if path.is_dir():
        return directory_hash(path)
    return None


def _texts(instance_path: Path, file: ConfigFile) -> dict[str, str]:
    """Each text file under *file*, by its path in the instance."""
    path = instance_path / file
    if path.is_file():
        paths = [path]
    elif path.is_dir():
        paths = sorted(entry for entry in path.rglob("*") if entry.is_file())
    else:
        paths = []
    texts = {}
    for entry in paths:
        try:
            texts[entry.relative_to(instance_path).as_posix()] = entry.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # A compiled or binary file has no diff worth reading.
            continue
    return texts


def _diff(before: dict[str, str], after: dict[str, str]) -> str:
    lines = [
        line
        for name in sorted(before.keys() | after.keys())
        for line in unified_diff(
            before.get(name, "").splitlines(),
            after.get(name, "").splitlines(),
            f"a/{name}",
            f"b/{name}",
            lineterm="",
        )
    ]
    return "".join(f"{line}\n" for line in lines)
