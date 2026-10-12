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
    FileHash,
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
from kinby.core.errors import InvalidConfig, SkillNotFound, StaleWrite
from kinby.hub.factories import FactoryStore
from kinby.hub.registry import HubRegistry
from kinby.instance import Instance
from kinby.instance.config_changes import directory_hash, recorded_changes
from kinby.instance.layout import SKILL_FILE, SKILLS_DIR, SKILLS_LOCK_NAME
from kinby.instance.skills_lock import LockedSkill, SkillsLock, lock_hash, read_lock, write_lock
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
        _refuse_links(instance_path)
        lock = _present(read_lock(instance_path), instance_path / SKILLS_DIR)
        return InstancePicksResult(
            picks=[
                SkillPick(name=name, source=entry.source_type)
                for name, entry in sorted(lock.skills.items())
            ],
            hash=lock_hash(instance_path),
        )

    def pick(
        self, instance: Instance, skills: Sequence[SkillName], read: FileHash | None
    ) -> InstancePicksResult:
        """Make *skills* the instance's picks, recorded as config changes.

        Each new pick's folder is copied in from the library, and each dropped one's removed. A
        skill without a lock entry is the agent's own, and is never touched. Every copy and the
        new lock are written aside first, so a failed copy leaves the instance as it was.
        """
        _refuse_links(instance.path)
        if lock_hash(instance.path) != read:
            raise StaleWrite(f"{SKILLS_LOCK_NAME} changed since it was read. Read the picks again.")
        held = instance.path / SKILLS_DIR
        lock = _present(read_lock(instance.path), held)
        folders = _skill_folders(self._directory)
        added = sorted(set(skills) - lock.skills.keys())
        dropped = sorted(lock.skills.keys() - set(skills))
        refusals = [_refusal(name, folders.get(name), held) for name in added]
        if any(refusals):
            raise InvalidConfig({"skills": " ".join(filter(None, refusals))})
        # The lock is a file the agent can edit, so a name in it must stay inside skills/.
        for name in dropped:
            validate_name(name)
        if not added and not dropped:
            return self.picks(instance.path)
        shipped = self._registry.shipped_skills()
        changed = [ConfigFile(f"{SKILLS_DIR}/{name}") for name in (*added, *dropped)]
        with TemporaryDirectory(prefix=".pick-", dir=instance.path) as temporary:
            staged, removed = Path(temporary) / "added", Path(temporary) / "dropped"
            removed.mkdir()
            picked = {name: entry for name, entry in lock.skills.items() if name not in dropped}
            for name in added:
                shutil.copytree(folders[name], staged / name)
                picked[name] = LockedSkill.of(staged / name, _source(name, shipped))
            new_lock = Path(temporary) / SKILLS_LOCK_NAME
            write_lock(new_lock, SkillsLock(skills=picked))
            with recorded_changes(
                instance, (*changed, ConfigFile(SKILLS_LOCK_NAME)), ConfigActor.APP
            ):
                held.mkdir(exist_ok=True)
                for name in dropped:
                    if (held / name).exists(follow_symlinks=False):
                        (held / name).rename(removed / name)
                for name in added:
                    (staged / name).rename(held / name)
                new_lock.replace(instance.path / SKILLS_LOCK_NAME)
        return self.picks(instance.path)


def _refuse_links(instance_path: Path) -> None:
    """Refuse an instance whose skills folder or lock is a link the hub would follow out of it."""
    for file in (SKILLS_DIR, SKILLS_LOCK_NAME):
        if (instance_path / file).is_symlink():
            raise InvalidConfig(
                {"skills": f"The instance's {file} is a link, so the hub leaves its picks alone."}
            )


def _present(lock: SkillsLock, held: Path) -> SkillsLock:
    """The lock's picks whose folder the instance still holds. A deleted one is no pick."""
    return SkillsLock(
        skills={
            name: entry
            for name, entry in lock.skills.items()
            if (held / name).exists(follow_symlinks=False)
        }
    )


def _refusal(name: SkillName, folder: Path | None, held: Path) -> str | None:
    """Why the library's skill *name*, in *folder*, cannot become a pick, if it cannot."""
    if folder is None:
        return f'The library has no skill "{name}".'
    if (held / name).exists(follow_symlinks=False):
        return f'The instance has its own skill "{name}". Rename or delete it to pick this one.'
    if folder.is_symlink() or any(path.is_symlink() for path in folder.rglob("*")):
        return f'The library\'s skill "{name}" holds a link, so it cannot be picked.'
    return None


def _source(name: SkillName, copied: Collection[SkillName]) -> LibrarySource:
    """Where the library's skill *name* came from, given the shipped skills the hub copied."""
    return LibrarySource.SHIPPED if name in copied else LibrarySource.WRITTEN


def _skill_folders(directory: Path) -> dict[SkillName, Path]:
    """Each skill folder in *directory*. A hidden one is a copy being staged."""
    if not directory.is_dir():
        return {}
    return {
        SkillName(path.name): path
        for path in directory.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    }
