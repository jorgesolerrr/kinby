"""The hub's factories: its own folders, over the ones kinby ships."""

from __future__ import annotations

import asyncio
import shutil
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
    FactoryResult,
    FactorySource,
    FactorySummary,
)
from kinby.core.errors import FactoryCheckFailed, FactoryNotFound, InvalidConfig, StaleWrite
from kinby.factories.check import check_factory
from kinby.instance.config_changes import directory_hash


class FactoryStore:
    """Serve each factory by name. A hub folder hides the shipped folder of the same name.

    The hub's folders only ever hold a factory that passed the check, so a broken edit leaves
    the last good version in place.
    """

    def __init__(self, directory: Path, shipped: Path) -> None:
        self._directory = directory
        self._shipped = shipped
        self._edit_lock = asyncio.Lock()

    async def list(self, command: FactoryListCommand) -> FactoryListResult:
        return FactoryListResult(
            factories=[
                FactorySummary(name=name, source=source)
                for name, (_, source) in sorted(self._folders().items())
            ]
        )

    async def get(self, command: FactoryGetCommand) -> FactoryResult:
        folder, source = self._folder(command.name)
        return FactoryResult(
            name=command.name,
            source=source,
            files={
                path.relative_to(folder).as_posix(): path.read_text(encoding="utf-8")
                for path in sorted(folder.rglob("*"))
                if path.is_file()
            },
            hash=directory_hash(folder),
        )

    async def check(self, command: FactoryCheckCommand) -> FactoryCheckResult:
        folder, _ = self._folder(command.name)
        return FactoryCheckResult(problems=list(await asyncio.to_thread(check_factory, folder)))

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
        current = self._folders().get(command.name)
        read = None if current is None else directory_hash(current[0])
        if read != command.hash:
            raise StaleWrite(f'Factory "{command.name}" changed since it was read. Read it again.')
        self._directory.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".edit-", dir=self._directory) as temporary:
            # Staged under the factory's own name, since the check matches it to the folder.
            staged = Path(temporary) / command.name
            if current is None:
                staged.mkdir()
            else:
                shutil.copytree(current[0], staged)
            for path, content in command.files.items():
                target = staged / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            problems = check_factory(staged)
            if problems:
                raise FactoryCheckFailed(problems)
            destination = self._directory / command.name
            replaced = Path(temporary) / "replaced"
            if destination.exists():
                destination.rename(replaced)
            try:
                staged.rename(destination)
            except Exception:
                if replaced.exists():
                    replaced.rename(destination)
                raise

    def _folder(self, name: FactoryName) -> tuple[Path, FactorySource]:
        found = self._folders().get(name)
        if found is None:
            raise FactoryNotFound(f'There is no factory "{name}".')
        return found

    def _folders(self) -> dict[FactoryName, tuple[Path, FactorySource]]:
        return _named_folders(self._shipped, FactorySource.SHIPPED) | _named_folders(
            self._directory, FactorySource.HUB
        )


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
