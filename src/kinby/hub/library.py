"""The hub's library: the skills it keeps, beside the factories it serves."""

from __future__ import annotations

import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from kinby.contracts import (
    FactoryListCommand,
    LibraryEntry,
    LibraryKind,
    LibraryListCommand,
    LibraryListResult,
    LibrarySkillReadCommand,
    LibrarySource,
    SkillName,
    SkillResult,
)
from kinby.core.config import skill_result
from kinby.core.errors import SkillNotFound
from kinby.hub.factories import FactoryStore
from kinby.hub.registry import HubRegistry
from kinby.instance.config_changes import directory_hash
from kinby.instance.layout import SKILL_FILE
from kinby.plugins.skills import load_skill_file


class Library:
    """Serve the library's entries: the skill folders in *directory* and the hub's factories."""

    def __init__(self, directory: Path, factories: FactoryStore, registry: HubRegistry) -> None:
        self._directory = directory
        self._factories = factories
        self._registry = registry

    def receive(self, shipped: Path) -> None:
        """Copy each skill kinby ships that the hub has never copied, and record its hash.

        A shipped skill the user deleted stays deleted, and one the user edited stays edited. A
        folder the library already holds by the skill's name is the user's and stays as it is.
        """
        copied = self._registry.shipped_skills()
        self._directory.mkdir(parents=True, exist_ok=True)
        for name, source in _skill_folders(shipped).items():
            destination = self._directory / name
            if name in copied or destination.exists():
                continue
            with TemporaryDirectory(prefix=".copy-", dir=self._directory) as temporary:
                staged = Path(temporary) / name
                shutil.copytree(source, staged)
                staged.rename(destination)
            self._registry.record_shipped_skill(name, directory_hash(destination))

    async def list(self, command: LibraryListCommand) -> LibraryListResult:
        factories = await self._factories.list(FactoryListCommand())
        copied = self._registry.shipped_skills()
        return LibraryListResult(
            entries=[
                *(
                    LibraryEntry(
                        kind=LibraryKind.FACTORY,
                        name=factory.name,
                        description=None,
                        source=LibrarySource.SHIPPED
                        if self._factories.ships(factory.name)
                        else LibrarySource.WRITTEN,
                    )
                    for factory in factories.factories
                ),
                *(
                    LibraryEntry(
                        kind=LibraryKind.SKILL,
                        name=name,
                        description=load_skill_file(folder / SKILL_FILE).description,
                        source=LibrarySource.SHIPPED if name in copied else LibrarySource.WRITTEN,
                    )
                    for name, folder in sorted(_skill_folders(self._directory).items())
                ),
            ]
        )

    async def read_skill(self, command: LibrarySkillReadCommand) -> SkillResult:
        folder = _skill_folders(self._directory).get(command.name)
        if folder is None:
            raise SkillNotFound(f'The library has no skill "{command.name}".')
        return skill_result(folder)


def _skill_folders(directory: Path) -> dict[SkillName, Path]:
    """Each skill folder in *directory*. A hidden one is a copy being staged."""
    if not directory.is_dir():
        return {}
    return {
        SkillName(path.name): path
        for path in directory.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    }
