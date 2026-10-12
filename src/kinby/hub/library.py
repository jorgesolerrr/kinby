"""The hub's library: the skills it keeps, beside the factories it serves."""

from __future__ import annotations

import asyncio
import shutil
import threading
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from kinby.contracts import (
    ConfigActor,
    ConfigFile,
    FactoryListCommand,
    FileHash,
    GitSource,
    InstancePicksResult,
    LibraryEntry,
    LibraryGitAddCommand,
    LibraryGitAddResult,
    LibraryGitPreviewCommand,
    LibraryGitPreviewResult,
    LibraryKind,
    LibraryListCommand,
    LibraryListResult,
    LibrarySkillDeleteCommand,
    LibrarySkillReadCommand,
    LibrarySkillWriteCommand,
    LibrarySource,
    PickState,
    PreviewedSkill,
    SkillName,
    SkillPick,
    SkillResult,
)
from kinby.core.config import skill_result
from kinby.core.errors import InvalidConfig, SkillNotFound, StaleWrite
from kinby.hub.factories import FactoryStore
from kinby.hub.git_skills import Address, checked_out, found_skills, parse_address
from kinby.hub.registry import HubRegistry
from kinby.instance import Instance
from kinby.instance.config_changes import directory_hash, file_hash, recorded_changes
from kinby.instance.layout import SKILL_FILE, SKILLS_DIR, SKILLS_LOCK_NAME
from kinby.instance.skills_lock import (
    LockedSkill,
    SkillsLock,
    lock_hash,
    read_lock,
    skill_hash,
    write_lock,
)
from kinby.plugins.instance_tools import validate_name, write_skill
from kinby.plugins.skills import load_skill_file


@dataclass(frozen=True)
class PickSync:
    """The picks an update wrote from the library and kept, by name."""

    written: tuple[SkillName, ...]
    kept: tuple[SkillName, ...]

    @property
    def detail(self) -> str:
        done = [
            f"{verb} {', '.join(names)}."
            for verb, names in (("Wrote", self.written), ("Kept the instance's own", self.kept))
            if names
        ]
        return " ".join(done) or "No pick is behind the library."


class Library:
    """Serve the library's entries: the skill folders in *directory* and the hub's factories."""

    def __init__(self, directory: Path, factories: FactoryStore, registry: HubRegistry) -> None:
        self._directory = directory
        self._factories = factories
        self._registry = registry
        self._adding = threading.Lock()
        self._edit_lock = asyncio.Lock()

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
                    self._entry(name, folder)
                    for name, folder in sorted(_skill_folders(self._directory).items())
                ),
            ]
        )

    async def read_skill(self, command: LibrarySkillReadCommand) -> SkillResult:
        return skill_result(self._folder(command.name))

    async def write_skill(self, command: LibrarySkillWriteCommand) -> SkillResult:
        async with self._edit_lock:
            return await asyncio.to_thread(self._write_skill, command)

    def _write_skill(self, command: LibrarySkillWriteCommand) -> SkillResult:
        if command.hash is not None:
            self._as_read(command.name, command.hash)
        elif command.name in _skill_folders(self._directory):
            raise InvalidConfig({"name": f'The library already holds a skill "{command.name}".'})
        try:
            write_skill(self._directory, command.name, command.content, self._directory)
        except ValueError as exc:
            raise InvalidConfig({"content": str(exc)}) from exc
        return skill_result(self._directory / command.name)

    async def delete_skill(self, command: LibrarySkillDeleteCommand) -> LibraryListResult:
        """Delete the skill from the library, and from no instance (ADR 0091)."""
        async with self._edit_lock:
            await asyncio.to_thread(self._delete_skill, command)
        return await self.list(LibraryListCommand())

    def _delete_skill(self, command: LibrarySkillDeleteCommand) -> None:
        folder = self._as_read(command.name, command.hash)
        with TemporaryDirectory(prefix=".delete-", dir=self._directory) as temporary:
            folder.rename(Path(temporary) / command.name)
        self._registry.forget_git_skill(command.name)

    async def preview(self, command: LibraryGitPreviewCommand) -> LibraryGitPreviewResult:
        return await asyncio.to_thread(self._preview, parse_address(command.address))

    def _preview(self, address: Address) -> LibraryGitPreviewResult:
        with checked_out(address.url, address.ref or "HEAD") as (commit, tree):
            return LibraryGitPreviewResult(
                url=address.url,
                ref=address.ref,
                commit=commit,
                skills=[
                    PreviewedSkill(
                        name=name,
                        path=str(path),
                        content=(shown := skill_result(tree / path)).content,
                        files=shown.files,
                    )
                    for name, path in sorted(found_skills(tree, address.path).items())
                ],
            )

    async def add(self, command: LibraryGitAddCommand) -> LibraryGitAddResult:
        """Add skills from git at the commit a preview resolved, whatever the ref names now."""
        address = parse_address(command.address)
        async with self._edit_lock:
            return await asyncio.to_thread(self._add, address, command)

    def _add(self, address: Address, command: LibraryGitAddCommand) -> LibraryGitAddResult:
        names = sorted(set(command.skills))
        with checked_out(address.url, command.commit) as (commit, tree), self._adding:
            found = found_skills(tree, address.path)
            # Read under the lock, so a second add of the same name sees the first one's folder.
            folders = _skill_folders(self._directory)
            refusals = [
                f'The library already holds a skill "{name}".'
                if name in folders
                else f'The repository holds no skill "{name}" at {commit}.'
                for name in names
                if name in folders or name not in found
            ]
            if refusals:
                raise InvalidConfig({"skills": " ".join(refusals)})
            for name in names:
                _copy_in(tree / found[name], self._directory / name, self._directory)
                self._registry.record_git_skill(
                    name,
                    GitSource(
                        url=address.url, path=str(found[name]), ref=address.ref, commit=commit
                    ),
                    directory_hash(self._directory / name),
                )
        return LibraryGitAddResult(
            entries=[self._entry(name, self._directory / name) for name in names]
        )

    def refusal(self, skills: Iterable[SkillName], own: Collection[SkillName]) -> str | None:
        """Why an instance whose own skills are *own* cannot pick *skills*, or None if it can."""
        folders = _skill_folders(self._directory)
        refusals = [_refusal(name, folders.get(name), own=name in own) for name in skills]
        return " ".join(filter(None, refusals)) or None

    def picks(self, instance_path: Path) -> InstancePicksResult:
        """The library skills the instance in *instance_path* picked, as its lock records them,
        each with how it stands against the library."""
        _refuse_links(instance_path)
        folders = _skill_folders(self._directory)
        held = instance_path / SKILLS_DIR
        lock = _present(read_lock(instance_path), held)
        return InstancePicksResult(
            picks=[
                SkillPick(
                    name=name,
                    source=entry.source_type,
                    state=_state(entry, held, name, folders.get(name)),
                )
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
        refusal = self.refusal(
            added, {name for name in added if (held / name).exists(follow_symlinks=False)}
        )
        if refusal is not None:
            raise InvalidConfig({"skills": refusal})
        # The lock is a file the agent can edit, so a name in it must stay inside skills/.
        for name in dropped:
            validate_name(name)
        if not added and not dropped:
            return self.picks(instance.path)
        changed = [ConfigFile(f"{SKILLS_DIR}/{name}") for name in (*added, *dropped)]
        with TemporaryDirectory(prefix=".pick-", dir=instance.path) as temporary:
            staged, removed = Path(temporary) / "added", Path(temporary) / "dropped"
            removed.mkdir()
            picked = {name: entry for name, entry in lock.skills.items() if name not in dropped}
            for name in added:
                shutil.copytree(folders[name], staged / name)
                picked[name] = LockedSkill.of(staged / name, self._origin(name))
            write_lock(Path(temporary), SkillsLock(skills=picked))
            with recorded_changes(
                instance, (*changed, ConfigFile(SKILLS_LOCK_NAME)), ConfigActor.APP
            ):
                held.mkdir(exist_ok=True)
                for name in dropped:
                    if (held / name).exists(follow_symlinks=False):
                        (held / name).rename(removed / name)
                for name in added:
                    (staged / name).rename(held / name)
                (Path(temporary) / SKILLS_LOCK_NAME).replace(instance.path / SKILLS_LOCK_NAME)
        return self.picks(instance.path)

    def sync(self, instance_path: Path) -> PickSync:
        """Bring each pick of the instance in *instance_path* up to the library's (ADR 0091).

        A pick behind the library is written from it, unless the instance edited its copy, which
        is kept. A pick the library no longer holds is left alone. A copy that already matches the
        library, as an interrupted update leaves one, only takes its lock entry.
        """
        lock = read_lock(instance_path)
        folders = _skill_folders(self._directory)
        held = instance_path / SKILLS_DIR
        written: list[SkillName] = []
        kept: list[SkillName] = []
        relocked = False
        for name, entry in sorted(lock.skills.items()):
            library = folders.get(name)
            latest = None if library is None else skill_hash(library)
            if library is None or latest == entry.computed_hash:
                continue
            copy = _copy_hash(held, name)
            if copy != latest:
                if copy != entry.computed_hash:
                    kept.append(name)
                    continue
                _copy_in(library, held / name, instance_path)
                written.append(name)
            lock.skills[name] = LockedSkill.of(held / name, self._origin(name))
            relocked = True
        if relocked:
            write_lock(instance_path, lock)
        return PickSync(written=tuple(written), kept=tuple(kept))

    def take_supplied(
        self, instance_path: Path, baseline: dict[str, FileHash], served: Iterable[str]
    ) -> tuple[dict[str, FileHash], tuple[SkillName, ...]]:
        """Make each shipped skill the template supplied, and serves no more, a pick of the
        instance in *instance_path*, and give back *baseline* without it.

        *served* is the paths of the template's files now. A pick's lock entry hashes the
        instance's copy where it still matches *baseline*, and the library's otherwise, so a copy
        the instance edited or linked out of its folder counts as edited locally. A skill an
        interrupted update already made a pick leaves *baseline* too, keeping its lock entry.
        """
        lock = read_lock(instance_path)
        folders = _skill_folders(self._directory)
        shipped = self._registry.shipped_skills()
        held = instance_path / SKILLS_DIR
        dropped = skill_names(baseline) - skill_names(served)
        taken = tuple(
            sorted(
                name
                for name in dropped - lock.skills.keys()
                if name in shipped
                and name in folders
                and (held / name).is_dir()
                and not (held / name).is_symlink()
            )
        )
        for name in taken:
            prefix = f"{SKILLS_DIR}/{name}/"
            supplied = {path: entry for path, entry in baseline.items() if path.startswith(prefix)}
            untouched = not _linked(held / name) and _file_hashes(held / name, prefix) == supplied
            lock.skills[name] = LockedSkill.of(
                held / name if untouched else folders[name], LibrarySource.SHIPPED
            )
        if taken:
            write_lock(instance_path, lock)
        prefixes = tuple(f"{SKILLS_DIR}/{name}/" for name in dropped & lock.skills.keys())
        return {
            path: entry for path, entry in baseline.items() if not path.startswith(prefixes)
        }, taken

    def _folder(self, name: SkillName) -> Path:
        folder = _skill_folders(self._directory).get(name)
        if folder is None:
            raise SkillNotFound(f'The library has no skill "{name}".')
        return folder

    def _as_read(self, name: SkillName, read: FileHash) -> Path:
        """The folder of the skill *name*, unless it changed since the client read it as *read*."""
        folder = self._folder(name)
        if directory_hash(folder) != read:
            raise StaleWrite(
                f'The library\'s skill "{name}" changed since it was read. Read it again.'
            )
        return folder

    def _entry(self, name: SkillName, folder: Path) -> LibraryEntry:
        origin = self._origin(name)
        landed = self._registry.landed_skills().get(name)
        return LibraryEntry(
            kind=LibraryKind.SKILL,
            name=name,
            description=load_skill_file(folder / SKILL_FILE).description,
            source=LibrarySource.GIT if isinstance(origin, GitSource) else origin,
            git=origin if isinstance(origin, GitSource) else None,
            edited=landed is not None and directory_hash(folder) != landed,
        )

    def _origin(self, name: SkillName) -> LibrarySource | GitSource:
        """Where the library's skill *name* came from: its git source, or shipped or written."""
        git = self._registry.git_skills().get(name)
        if git is not None:
            return git
        if name in self._registry.shipped_skills():
            return LibrarySource.SHIPPED
        return LibrarySource.WRITTEN


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


def _refusal(name: SkillName, folder: Path | None, *, own: bool) -> str | None:
    """Why the library's skill *name*, in *folder*, cannot become a pick, if it cannot.

    *own* says the instance holds a skill of that name already.
    """
    if folder is None:
        return f'The library has no skill "{name}".'
    if own:
        return f'The instance has its own skill "{name}". Rename or delete it to pick this one.'
    if folder.is_symlink() or any(path.is_symlink() for path in folder.rglob("*")):
        return f'The library\'s skill "{name}" holds a link, so it cannot be picked.'
    return None


def skill_names(paths: Iterable[str]) -> set[SkillName]:
    """The skill folders among *paths*, each the path of a file in an instance."""
    return {
        SkillName(parts[1])
        for parts in (PurePosixPath(path).parts for path in paths)
        if len(parts) > 2 and parts[0] == SKILLS_DIR
    }


def _linked(folder: Path) -> bool:
    """Whether *folder* holds a link, whose target the hub must never read."""
    return any(path.is_symlink() for path in folder.rglob("*"))


def _file_hashes(folder: Path, prefix: str) -> dict[str, FileHash]:
    """The hash of each file in *folder*, by its path behind *prefix*."""
    return {
        f"{prefix}{path.relative_to(folder).as_posix()}": file_hash(path.read_bytes())
        for path in folder.rglob("*")
        if path.is_file()
    }


def _state(entry: LockedSkill, held: Path, name: SkillName, library: Path | None) -> PickState:
    """How the pick *name*, held in *held* and locked as *entry*, stands against *library*, its
    library entry's folder."""
    copy = _copy_hash(held, name)
    latest = None if library is None else skill_hash(library)
    if copy is not None and copy == latest:
        return PickState.UP_TO_DATE
    if copy != entry.computed_hash:
        return PickState.EDITED
    if latest is not None and latest != entry.computed_hash:
        return PickState.BEHIND
    return PickState.UP_TO_DATE


def _copy_hash(held: Path, name: SkillName) -> FileHash | None:
    """The hash of the instance's copy of the pick *name* in its skills folder *held*, or None
    where the hub must not read or write it: the agent can write the lock and the folder, so a
    name could lead out of *held*, and a link could lead out of the instance."""
    try:
        validate_name(name)
    except ValueError:
        return None
    copy = held / name
    if held.is_symlink() or copy.is_symlink() or _linked(copy):
        return None
    return skill_hash(copy)


def _linked(folder: Path) -> bool:
    """Whether *folder* holds a link, whose target the hub must never read."""
    return any(path.is_symlink() for path in folder.rglob("*"))


def _copy_in(folder: Path, target: Path, staging: Path) -> None:
    """Copy *folder* to *target*, replacing what is there. The copy is staged in *staging*, so no
    reader sees half of one."""
    with TemporaryDirectory(prefix=".pick-", dir=staging) as temporary:
        staged = Path(temporary) / target.name
        shutil.copytree(folder, staged)
        if target.exists():
            shutil.rmtree(target)
        staged.rename(target)


def _skill_folders(directory: Path) -> dict[SkillName, Path]:
    """Each skill folder in *directory*. A hidden one is a copy being staged."""
    if not directory.is_dir():
        return {}
    return {
        SkillName(path.name): path
        for path in directory.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    }
