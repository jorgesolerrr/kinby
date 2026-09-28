"""Read and write an instance's configuration files for clients, each read carrying its hash."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from kinby.contracts import (
    ConfigActor,
    ConfigFile,
    ConfigHistoryCommand,
    ConfigHistoryResult,
    FileHash,
    PromptGetCommand,
    PromptName,
    PromptResult,
    PromptSetCommand,
    RoutineDeleteCommand,
    RoutineDeleteResult,
    RoutineFile,
    RoutineName,
    RoutineReadCommand,
    RoutineSetEnabledCommand,
    RoutineWriteCommand,
)
from kinby.core.errors import InvalidConfig, RoutineNotFound, StaleWrite
from kinby.instance import Instance
from kinby.instance.config_changes import ConfigChangeLog, recorded_change
from kinby.instance.layout import RECAP_NAME, ROUTINE_FILE, SYSTEM_NAME
from kinby.instance.recap import DEFAULT_RECAP_LENS
from kinby.plugins.instance_tools import (
    delete_routine,
    enable_routine,
    routine_config_file,
    validate_name,
    write_routine,
)

_PROMPT_FILES = {
    PromptName.BEHAVIOR: ConfigFile(SYSTEM_NAME),
    PromptName.RECAP: ConfigFile(RECAP_NAME),
}
#: What each prompt reads as without its file. kinby ships no behavior prompt of its own.
_SHIPPED_PROMPTS = {PromptName.BEHAVIOR: "", PromptName.RECAP: DEFAULT_RECAP_LENS}


def _file_hash(content: bytes) -> FileHash:
    return FileHash(hashlib.sha256(content).hexdigest())


def _directory_hash(directory: Path) -> FileHash:
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


def _check_unchanged(instance_path: Path, file: ConfigFile, read: FileHash | None) -> None:
    """Refuse a write over a directory changed since the client read it, or over one it creates."""
    directory = instance_path / file
    if read is None and directory.exists():
        raise StaleWrite(f"{file} already exists. Read it first.")
    if read is not None and _directory_hash(directory) != read:
        raise StaleWrite(f"{file} changed since it was read. Read it again.")


@contextmanager
def _refusals() -> Iterator[None]:
    """Report the instance tools' refusals as the contract errors a client reads."""
    try:
        yield
    except LookupError as exc:
        raise RoutineNotFound(str(exc)) from exc
    except ValueError as exc:
        raise InvalidConfig(str(exc)) from exc


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _write_over(path: Path, content: bytes, read: FileHash) -> None:
    """Replace the file the client read, staging beside it so no reader sees half a file."""
    if _file_hash(_read_bytes(path) or b"") != read:
        raise StaleWrite(f"{path.name} changed since it was read. Read it again.")
    staging = path.with_name(f".{path.name}.staging")
    staging.write_bytes(content)
    staging.replace(path)


class InstanceConfig:
    """The instance's configuration as the contract reads and writes it."""

    def __init__(self, instance: Instance) -> None:
        self._instance = instance

    async def get_prompt(self, command: PromptGetCommand) -> PromptResult:
        content = _read_bytes(self._instance.path / _PROMPT_FILES[command.name])
        if content is None:
            return PromptResult(
                content=_SHIPPED_PROMPTS[command.name], hash=_file_hash(b""), default=True
            )
        return PromptResult(
            content=content.decode("utf-8"), hash=_file_hash(content), default=False
        )

    async def set_prompt(self, command: PromptSetCommand) -> PromptResult:
        content = command.content.encode("utf-8")
        async with self._instance.config_lock:
            await asyncio.to_thread(self._write, _PROMPT_FILES[command.name], content, command.hash)
        return PromptResult(content=command.content, hash=_file_hash(content), default=False)

    async def read_routine(self, command: RoutineReadCommand) -> RoutineFile:
        async with self._instance.routine_lock:
            return await asyncio.to_thread(self._read_routine, command.name)

    async def write_routine(self, command: RoutineWriteCommand) -> RoutineFile:
        async with self._instance.routine_lock:
            return await asyncio.to_thread(self._write_routine, command)

    async def set_routine_enabled(self, command: RoutineSetEnabledCommand) -> RoutineFile:
        async with self._instance.routine_lock:
            return await asyncio.to_thread(self._set_routine_enabled, command)

    async def delete_routine(self, command: RoutineDeleteCommand) -> RoutineDeleteResult:
        async with self._instance.routine_lock:
            await asyncio.to_thread(self._delete_routine, command)
        return RoutineDeleteResult()

    async def history(self, command: ConfigHistoryCommand) -> ConfigHistoryResult:
        log = ConfigChangeLog(self._instance.manifest.state_dir)
        return ConfigHistoryResult(changes=log.history(command.file, command.limit))

    def _write(self, file: ConfigFile, content: bytes, read: FileHash) -> None:
        with self._recorded(file):
            _write_over(self._instance.path / file, content, read)

    def _recorded(self, file: ConfigFile) -> AbstractContextManager[None]:
        return recorded_change(self._instance, file, ConfigActor.APP)

    def _write_routine(self, command: RoutineWriteCommand) -> RoutineFile:
        with _refusals():
            validate_name(command.name)
            _check_unchanged(self._instance.path, routine_config_file(command.name), command.hash)
            write_routine(self._instance, command.name, command.content, self._recorded)
        return self._read_routine(command.name)

    def _set_routine_enabled(self, command: RoutineSetEnabledCommand) -> RoutineFile:
        with _refusals():
            enable_routine(self._instance, command.name, self._recorded, enabled=command.enabled)
        return self._read_routine(command.name)

    def _delete_routine(self, command: RoutineDeleteCommand) -> None:
        with _refusals():
            validate_name(command.name)
            _check_unchanged(self._instance.path, routine_config_file(command.name), command.hash)
            delete_routine(self._instance, command.name, self._recorded)

    def _read_routine(self, name: RoutineName) -> RoutineFile:
        with _refusals():
            validate_name(name)
        directory = self._instance.path / routine_config_file(name)
        content = _read_bytes(directory / ROUTINE_FILE)
        if content is None:
            raise RoutineNotFound(f'Routine "{name}" was not found.')
        return RoutineFile(
            name=name, content=content.decode("utf-8"), hash=_directory_hash(directory)
        )
