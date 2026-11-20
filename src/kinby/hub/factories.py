"""The hub's factories: its own folders, over the ones kinby ships."""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from kinby.contracts import (
    FactoryCheckCommand,
    FactoryCheckResult,
    FactoryEditCommand,
    FactoryGetCommand,
    FactoryListCommand,
    FactoryListResult,
    FactoryName,
    FactoryRemoveCommand,
    FactoryResetCommand,
    FactoryResult,
    FactoryRevertCommand,
    FactorySource,
    FactorySummary,
    FactoryVersionDiff,
    FactoryVersionDiffCommand,
    FactoryVersionListCommand,
    FactoryVersionListResult,
    FactoryVersionSource,
    GitCommit,
)
from kinby.core.errors import FactoryCheckFailed, FactoryNotFound, InvalidConfig, StaleWrite
from kinby.factories.check import check_factory
from kinby.factories.file import FactoryPath, WorkbenchFactory, read_factory_file
from kinby.hub.factory_versions import FactoryVersions
from kinby.instance.config_changes import files_hash

_logger = logging.getLogger(__name__)
_BYTECODE = shutil.ignore_patterns("__pycache__")


class FactoryStore:
    """Serve each factory by name. A hub folder hides the shipped folder of the same name.

    Every save that passes the check is a version of the factory. A change made by hand in a hub
    folder becomes one the next time the hub reads the factory, or is set aside when it fails the
    check, so the last good version stays in place.
    """

    def __init__(self, directory: Path, shipped: Path) -> None:
        self._directory = directory
        self._shipped = shipped
        self._versions = FactoryVersions(directory)
        self._edit_lock = asyncio.Lock()

    async def list(self, command: FactoryListCommand) -> FactoryListResult:
        async with self._edit_lock:
            return await asyncio.to_thread(self._list)

    def _list(self) -> FactoryListResult:
        for name in _named_folders(self._directory, FactorySource.HUB):
            self._take_in(name)
        return FactoryListResult(
            factories=[
                FactorySummary(name=name, source=source, version=self._current(name, source))
                for name, (_, source) in sorted(self._folders().items())
            ]
        )

    async def get(self, command: FactoryGetCommand) -> FactoryResult:
        async with self._edit_lock:
            return await asyncio.to_thread(self._get, command.name)

    def _get(self, name: FactoryName) -> FactoryResult:
        self._take_in(name)
        folder, source = self._folder(name)
        files = _factory_files(folder)
        return FactoryResult(
            name=name,
            source=source,
            files={relative: _text(relative, path) for relative, path in files.items()},
            hash=files_hash(files),
            version=self._current(name, source),
        )

    async def versions(self, command: FactoryVersionListCommand) -> FactoryVersionListResult:
        async with self._edit_lock:
            return await asyncio.to_thread(self._listed_versions, command.name)

    def _listed_versions(self, name: FactoryName) -> FactoryVersionListResult:
        self._take_in(name)
        _, source = self._folder(name)
        listed = self._versions.listed(name) if source is FactorySource.HUB else []
        return FactoryVersionListResult(versions=listed)

    async def diff(self, command: FactoryVersionDiffCommand) -> FactoryVersionDiff:
        self._folder(command.name)
        async with self._edit_lock:
            shown = await asyncio.to_thread(self._versions.diff, command.name, command.version)
        return FactoryVersionDiff(diff=shown)

    async def check(self, command: FactoryCheckCommand) -> FactoryCheckResult:
        folder, _ = self._folder(command.name)
        return FactoryCheckResult(problems=list(await asyncio.to_thread(check_factory, folder)))

    def file(self, name: FactoryName) -> WorkbenchFactory:
        """The factory's file as the hub keeps it: the last version that passed the check."""
        folder, _ = self._folder(name)
        return read_factory_file(folder)

    def prompt(self, name: FactoryName, path: FactoryPath) -> str:
        """The text of a prompt file, from the last version of the factory that passed the check."""
        folder, _ = self._folder(name)
        return (folder / path).read_text(encoding="utf-8")

    async def checked(self, name: FactoryName) -> WorkbenchFactory:
        """The factory's file, read in the same pass as the check passes it."""
        async with self._edit_lock:
            return await asyncio.to_thread(self._checked, name)

    def _checked(self, name: FactoryName) -> WorkbenchFactory:
        self._take_in(name)
        folder, _ = self._folder(name)
        problems = check_factory(folder)
        if problems:
            raise FactoryCheckFailed(problems)
        return read_factory_file(folder)

    async def edit(self, command: FactoryEditCommand) -> FactoryResult:
        """Write the files over the factory the client read, and keep them if the check passes."""
        for path in command.files:
            relative = PurePosixPath(path)
            if not relative.parts or relative.is_absolute() or ".." in relative.parts:
                raise InvalidConfig({"files": f'"{path}" is not a path in the factory\'s folder.'})
        async with self._edit_lock:
            await asyncio.to_thread(self._edit, command)
        return await self.get(FactoryGetCommand(name=command.name))

    def _edit(self, command: FactoryEditCommand) -> None:
        self._take_in(command.name)
        current = self._folders().get(command.name)
        read = None if current is None else files_hash(_factory_files(current[0]))
        if read != command.hash:
            raise StaleWrite(f'Factory "{command.name}" changed since it was read. Read it again.')
        with self._staging(command.name) as staged:
            if current is None:
                staged.mkdir()
            else:
                shutil.copytree(current[0], staged, ignore=_BYTECODE)
            for path, content in command.files.items():
                target = staged / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            self._save(staged, FactoryVersionSource.WEB)

    async def revert(self, command: FactoryRevertCommand) -> FactoryResult:
        """Make the factory's files those of one of its versions, as a new version."""
        async with self._edit_lock:
            await asyncio.to_thread(self._revert, command)
        return await self.get(FactoryGetCommand(name=command.name))

    def _revert(self, command: FactoryRevertCommand) -> None:
        self._take_in(command.name)
        self._folder(command.name)
        with self._staging(command.name) as staged:
            self._versions.extract(command.name, command.version, staged)
            self._save(staged, FactoryVersionSource.REVERT)

    async def reset(self, command: FactoryResetCommand) -> FactoryResult:
        """Make a shipped factory's files the ones kinby ships, as a new version."""
        if not self.ships(command.name):
            raise InvalidConfig({"name": f'kinby ships no factory "{command.name}".'})
        async with self._edit_lock:
            await asyncio.to_thread(self._reset, command.name)
        return await self.get(FactoryGetCommand(name=command.name))

    def _reset(self, name: FactoryName) -> None:
        self._take_in(name)
        with self._staging(name) as staged:
            shutil.copytree(self._shipped / name, staged, ignore=_BYTECODE)
            self._save(staged, FactoryVersionSource.SHIPPED)

    @contextmanager
    def _staging(self, name: FactoryName) -> Iterator[Path]:
        """A path to stage the factory's next files at, beside the hub's folders."""
        self._directory.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".edit-", dir=self._directory) as temporary:
            # Under the factory's own name, since the check matches it to the folder.
            yield Path(temporary) / name

    def _save(self, staged: Path, source: FactoryVersionSource) -> None:
        """Put the factory staged at *staged* in place of the hub's folder, as a version, once it
        passes the check. A shipped factory's first save keeps kinby's version before it."""
        problems = check_factory(staged)
        if problems:
            raise FactoryCheckFailed(problems)
        name = staged.name
        destination = self._directory / name
        if not destination.exists() and self.ships(name):
            shutil.copytree(self._shipped / name, destination, ignore=_BYTECODE)
            self._versions.record(name, FactoryVersionSource.SHIPPED)
        replaced = staged.parent / "replaced"
        if destination.exists():
            destination.rename(replaced)
        try:
            staged.rename(destination)
        except Exception:
            if replaced.exists():
                replaced.rename(destination)
            raise
        self._versions.record(name, source)

    def _take_in(self, name: FactoryName) -> None:
        """Make a change by hand in the hub's folder of the factory a version when it passes the
        check. Set it aside when it fails, unless the factory has no version to fall back on."""
        if not (self._directory / name).is_dir() or not self._versions.changed(name):
            return
        problems = check_factory(self._directory / name)
        if not problems:
            self._versions.record(name, FactoryVersionSource.BY_HAND)
        elif self._versions.current(name) is not None:
            self._versions.set_aside(name)
            _logger.warning(
                "A change by hand to factory %s failed the check, so the hub set it aside in a "
                "git stash of its factories folder and kept the last good version: %s",
                name,
                " ".join(problems),
            )

    def _current(self, name: FactoryName, source: FactorySource) -> GitCommit | None:
        return self._versions.current(name) if source is FactorySource.HUB else None

    async def remove(self, command: FactoryRemoveCommand) -> FactoryListResult:
        """Delete the hub's own folder of the factory. A factory kinby ships stays."""
        self._folder(command.name)
        if self.ships(command.name):
            raise InvalidConfig({"name": f'kinby ships factory "{command.name}", so it stays.'})
        async with self._edit_lock:
            await asyncio.to_thread(self._remove, command.name)
        return await self.list(FactoryListCommand())

    def _remove(self, name: FactoryName) -> None:
        shutil.rmtree(self._directory / name)
        self._versions.removed(name)

    def ships(self, name: FactoryName) -> bool:
        """Whether kinby ships a factory by this name, served as it is or as the hub's copy."""
        return name in _named_folders(self._shipped, FactorySource.SHIPPED)

    def _folder(self, name: FactoryName) -> tuple[Path, FactorySource]:
        found = self._folders().get(name)
        if found is None:
            raise FactoryNotFound(f'There is no factory "{name}".')
        return found

    def _folders(self) -> dict[FactoryName, tuple[Path, FactorySource]]:
        return _named_folders(self._shipped, FactorySource.SHIPPED) | _named_folders(
            self._directory, FactorySource.HUB
        )


def _factory_files(folder: Path) -> dict[str, Path]:
    """Each file of the factory by its path in *folder*, without the bytecode Python leaves."""
    return {
        path.relative_to(folder).as_posix(): path
        for path in sorted(folder.rglob("*"))
        if path.is_file() and "__pycache__" not in path.relative_to(folder).parts
    }


def _text(relative: str, path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise InvalidConfig({"files": f'"{relative}" is not UTF-8 text.'}) from None


def _named_folders(
    directory: Path, source: FactorySource
) -> dict[FactoryName, tuple[Path, FactorySource]]:
    """Each factory folder in *directory*. A hidden one is an edit being staged."""
    if not directory.is_dir():
        return {}
    return {
        path.name: (path, source)
        for path in directory.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    }
