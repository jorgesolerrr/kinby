"""The hub's library: the skills it keeps, beside the factories it serves."""

from __future__ import annotations

import shutil
from collections.abc import Collection, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from kinby.contracts import (
    ConfigActor,
    ConfigFile,
    FactoryListCommand,
    InstancePicksResult,
    LibraryEntry,
    LibraryKind,
    LibraryListCommand,
    LibraryListResult,
    LibrarySkillReadCommand,
    LibrarySource,
    SkillName,
    SkillPick,
    SkillResult,
)
from kinby.core.config import skill_result
from kinby.core.errors import InvalidConfig, SkillNotFound
from kinby.hub.factories import FactoryStore
from kinby.hub.registry import HubRegistry
from kinby.instance import Instance
from kinby.instance.config_changes import directory_hash, recorded_changes
from kinby.instance.layout import SKILL_FILE, SKILLS_DIR, SKILLS_LOCK_NAME
from kinby.instance.skills_lock import LockedSkill, SkillsLock, read_lock, write_lock
from kinby.plugins.instance_tools import validate_name
from kinby.plugins.skills import load_skill_file


class Library:
    """Serve the library's entries: the skill folders in *directory* and the hub's factories."""

    def __init__(self, directory: Path, factories: FactoryStore, registry: HubRegistry) -> None:
        self._directory = directory
        self._factories = factories
        self._registry = registry

    def receive(self, shipped: Path) -> None:
        """Copy each skill kinby ships that the hub has never copied, and record its hash.

        A shipped skill the user deleted stays deleted, and one the user edited stays edited. An
        unrecorded folder that matches the shipped skill is a copy cut short before its record,
        and is recorded. One that differs is the user's and stays as it is.
        """
        copied = self._registry.shipped_skills()
        self._directory.mkdir(parents=True, exist_ok=True)
        for name, source in _skill_folders(shipped).items():
            destination = self._directory / name
            if name in copied:
                continue
            if not destination.exists():
                with TemporaryDirectory(prefix=".copy-", dir=self._directory) as temporary:
                    staged = Path(temporary) / name
                    shutil.copytree(source, staged)
                    staged.rename(destination)
            elif directory_hash(destination) != directory_hash(source):
                continue
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
                        source=_source(name, copied),
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

    def picks(self, instance_path: Path) -> InstancePicksResult:
        """The library skills the instance in *instance_path* picked, as its lock records them."""
        return _picks(read_lock(instance_path))

    def pick(self, instance: Instance, skills: Sequence[SkillName]) -> InstancePicksResult:
        """Make *skills* the instance's picks, recorded as config changes.

        Each new pick's folder is copied in from the library, and each dropped one's removed. A
        skill without a lock entry is the agent's own, and is never touched.
        """
        lock = read_lock(instance.path)
        folders = _skill_folders(self._directory)
        held = instance.path / SKILLS_DIR
        added = sorted(set(skills) - lock.skills.keys())
        dropped = sorted(lock.skills.keys() - set(skills))
        refusals = [
            f'The library has no skill "{name}".'
            if name not in folders
            else f'The instance has its own skill "{name}". Rename or delete it to pick this one.'
            for name in added
            if name not in folders or (held / name).exists()
        ]
        if refusals:
            raise InvalidConfig({"skills": " ".join(refusals)})
        # The lock is a file the agent can edit, so a name in it must stay inside skills/.
        for name in dropped:
            validate_name(name)
        if not added and not dropped:
            return _picks(lock)
        changed = [ConfigFile(f"{SKILLS_DIR}/{name}") for name in (*added, *dropped)]
        shipped = self._registry.shipped_skills()
        picked = {name: entry for name, entry in lock.skills.items() if name not in dropped}
        with recorded_changes(instance, (*changed, ConfigFile(SKILLS_LOCK_NAME)), ConfigActor.APP):
            for name in dropped:
                if (held / name).exists():
                    shutil.rmtree(held / name)
            held.mkdir(exist_ok=True)
            for name in added:
                with TemporaryDirectory(prefix=".pick-", dir=instance.path) as temporary:
                    staged = Path(temporary) / name
                    shutil.copytree(folders[name], staged)
                    staged.rename(held / name)
                picked[name] = LockedSkill.of(held / name, _source(name, shipped))
            lock = SkillsLock(skills=picked)
            write_lock(instance.path, lock)
        return _picks(lock)


def _source(name: SkillName, copied: Collection[SkillName]) -> LibrarySource:
    """Where the library's skill *name* came from, given the shipped skills the hub copied."""
    return LibrarySource.SHIPPED if name in copied else LibrarySource.WRITTEN


def _picks(lock: SkillsLock) -> InstancePicksResult:
    return InstancePicksResult(
        picks=[
            SkillPick(name=name, source=entry.source_type)
            for name, entry in sorted(lock.skills.items())
        ]
    )


def _skill_folders(directory: Path) -> dict[SkillName, Path]:
    """Each skill folder in *directory*. A hidden one is a copy being staged."""
    if not directory.is_dir():
        return {}
    return {
        SkillName(path.name): path
        for path in directory.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    }
