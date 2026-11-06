"""Keep each edit to an instance's configuration in the instance database, apart from any thread."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from difflib import unified_diff
from pathlib import Path
from uuid import UUID

from kinby.contracts import ConfigActor, ConfigChange, ConfigFile, FileHash
from kinby.instance.database import open_database
from kinby.instance.dataclasses import Instance


class ConfigChangeLog:
    """The config changes of one instance, kept in the order made."""

    def __init__(self, state_dir: Path) -> None:
        self._db = open_database(state_dir)

    def append(self, change: ConfigChange) -> None:
        self._db.execute(
            "INSERT INTO config_changes VALUES (?, ?)", (change.file, change.model_dump_json())
        )

    def history(self, file: ConfigFile | None, limit: int) -> list[ConfigChange]:
        """The latest *limit* changes, newest first, to *file* or to any file."""
        rows = self._db.execute(
            "SELECT change FROM config_changes WHERE ?1 IS NULL OR file = ?1"
            " ORDER BY rowid DESC LIMIT ?2",
            (file, limit),
        )
        return [ConfigChange.model_validate_json(change) for (change,) in rows]


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
                diff=text_diff(texts, _texts(instance.path, file)),
                hash=_hash(instance.path / file),
            )
        )


def file_hash(content: bytes) -> FileHash:
    return FileHash(hashlib.sha256(content).hexdigest())


def directory_hash(directory: Path) -> FileHash:
    """Hash every file under *directory* by its relative path. A missing one hashes as empty."""
    files = directory.rglob("*") if directory.is_dir() else ()
    return files_hash(
        {path.relative_to(directory).as_posix(): path for path in files if path.is_file()}
    )


def files_hash(files: Mapping[str, Path]) -> FileHash:
    """Hash each file by the relative path it is named by."""
    digest = hashlib.sha256()
    for relative, path in sorted(files.items()):
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


def text_diff(before: dict[str, str], after: dict[str, str]) -> str:
    """A unified diff from *before* to *after*, each a text by its path."""
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
