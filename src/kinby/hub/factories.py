"""The hub's factories: its own folders, over the ones kinby ships."""

from __future__ import annotations

import ast
import asyncio
import logging
import shutil
from collections.abc import Collection, Iterable, Iterator, Mapping
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
    FactoryStepDeleteCommand,
    FactoryStepOffSetCommand,
    FactoryStepSaveCommand,
    FactorySummary,
    FactoryVersionDiff,
    FactoryVersionDiffCommand,
    FactoryVersionListCommand,
    FactoryVersionListResult,
    FactoryVersionSource,
    FileHash,
    GitCommit,
)
from kinby.core.errors import FactoryCheckFailed, FactoryNotFound, InvalidConfig, StaleWrite
from kinby.factories.check import check_factory, off_dependents
from kinby.factories.file import (
    FACTORY_FILE,
    VERSIONS_DIR,
    FactoryPath,
    WorkbenchAgentStep,
    WorkbenchFactory,
    parse_factory_file,
    read_factory_file,
)
from kinby.factories.file_writes import (
    StepNotDeclared,
    with_step,
    with_step_off,
    without_step,
)
from kinby.hub.factory_versions import FactoryVersions
from kinby.instance.config_changes import files_hash
from kinby.instance.layout import HOOKS_DIR, TOOLS_DIR

_logger = logging.getLogger(__name__)
_BYTECODE = shutil.ignore_patterns("__pycache__")


class FactoryStore:
    """Serve each factory by name. A hub folder hides the shipped folder of the same name.

    Every save that passes the check is a version of the factory. A change made by hand in a hub
    folder becomes one the next time the hub reads the factory, or is set aside when it fails the
    check, so the last good version stays in place. The hub keeps a tree of each version a run
    keeps beside its folders, for the workbenches that mount them.
    """

    def __init__(self, directory: Path, shipped: Path) -> None:
        self._directory = directory
        self._shipped = shipped
        self._versions = FactoryVersions(directory)
        self._edit_lock = asyncio.Lock()
        #: Each version's factory file, read once, since a version never changes.
        self._version_files: dict[tuple[FactoryName, GitCommit], WorkbenchFactory] = {}

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
            declared_in=_declared_in(folder),
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

    async def version(self, name: FactoryName) -> GitCommit | None:
        """The factory's latest version, with a change by hand in its folder taken in first. None
        for a factory kinby ships that the hub holds no version of."""
        async with self._edit_lock:
            return await asyncio.to_thread(self._latest, name)

    def _latest(self, name: FactoryName) -> GitCommit | None:
        self._take_in(name)
        _, source = self._folder(name)
        return self._current(name, source)

    def file(self, name: FactoryName, version: GitCommit | None = None) -> WorkbenchFactory:
        """The factory's file as *version* holds it, or without one as the hub keeps it: the last
        version that passed the check."""
        if version is None:
            folder, _ = self._folder(name)
            return read_factory_file(folder)
        if (name, version) not in self._version_files:
            text = self._versions.show(name, version, FACTORY_FILE)
            self._version_files[name, version] = parse_factory_file(text)
        return self._version_files[name, version]

    def prompt(self, name: FactoryName, path: FactoryPath, version: GitCommit | None = None) -> str:
        """The text of a prompt file as *version* holds it, read in the version's tree so a link
        resolves as the check resolved it, or without one from the last version of the factory
        that passed the check."""
        folder = self.keep_tree(name, version) if version is not None else self._folder(name)[0]
        return (folder / path).read_text(encoding="utf-8")

    def keep_tree(self, name: FactoryName, version: GitCommit) -> Path:
        """The factory's folder as *version* holds it, kept without write permissions where a
        workbench's factories mount finds it."""
        tree = self._directory / VERSIONS_DIR / name / version
        if tree.is_dir():
            return tree
        with self._staging(name) as staged:
            self._versions.extract(name, version, staged)
            for path in staged.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    path.chmod(path.stat().st_mode & ~0o222)
            tree.parent.mkdir(parents=True, exist_ok=True)
            staged.rename(tree)
        return tree

    def drop_trees(self, kept: Collection[tuple[FactoryName, GitCommit]]) -> None:
        """Remove each version's tree but those of the factories and versions in *kept*."""
        for tree in (self._directory / VERSIONS_DIR).glob("*/*"):
            if (tree.parent.name, tree.name) not in kept:
                shutil.rmtree(tree)

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
            _in_folder("files", path)
        async with self._edit_lock:
            await asyncio.to_thread(self._edit, command)
        return await self.get(FactoryGetCommand(name=command.name))

    def _edit(self, command: FactoryEditCommand) -> None:
        self._write(command.name, self._read_as(command.name, command.hash), command.files)

    async def save_step(self, command: FactoryStepSaveCommand) -> FactoryResult:
        """Declare one step of the factory the client read as the command says, with its prompt
        and its hook, and keep them if the check passes."""
        prompt = command.step.get("prompt")
        if command.prompt is not None:
            if not isinstance(prompt, str):
                raise InvalidConfig({"prompt": "The step names no prompt to write."})
            _in_folder("step.prompt", prompt)
        async with self._edit_lock:
            await asyncio.to_thread(self._save_step, command)
        return await self.get(FactoryGetCommand(name=command.name))

    def _save_step(self, command: FactoryStepSaveCommand) -> None:
        folder, _ = self._folder(command.name)
        self._read_as(command.name, command.hash)
        try:
            files = {
                FACTORY_FILE: with_step(
                    (folder / FACTORY_FILE).read_text(encoding="utf-8"), command.step
                )
            }
        except StepNotDeclared as exc:
            raise InvalidConfig({"step": str(exc)}) from None
        if command.prompt is not None:
            files[str(command.step["prompt"])] = command.prompt
        if command.hook is not None:
            hook = command.step.get("check", command.step.get("call"))
            declared = _declared_in(folder).get(hook) if isinstance(hook, str) else None
            if declared is None:
                raise InvalidConfig({"hook": "No file of the factory declares the step's hook."})
            files[declared] = command.hook
        self._write(command.name, folder, files)

    async def set_step_off(self, command: FactoryStepOffSetCommand) -> FactoryResult:
        """Turn one step of the factory the client read off or on, if the check passes."""
        async with self._edit_lock:
            await asyncio.to_thread(self._set_step_off, command)
        return await self.get(FactoryGetCommand(name=command.name))

    def _set_step_off(self, command: FactoryStepOffSetCommand) -> None:
        folder, _ = self._folder(command.name)
        self._read_as(command.name, command.hash)
        text = (folder / FACTORY_FILE).read_text(encoding="utf-8")
        try:
            turned = with_step_off(text, command.step, off=command.off)
        except StepNotDeclared as exc:
            raise InvalidConfig({"step": str(exc)}) from None
        try:
            self._write(command.name, folder, {FACTORY_FILE: turned})
        except FactoryCheckFailed as exc:
            raise FactoryCheckFailed(exc.problems, _needing_off(turned)) from None

    async def delete_step(self, command: FactoryStepDeleteCommand) -> FactoryResult:
        """Delete one off step of the factory the client read, with its prompt file unless another
        step uses it, if the check passes."""
        async with self._edit_lock:
            await asyncio.to_thread(self._delete_step, command)
        return await self.get(FactoryGetCommand(name=command.name))

    def _delete_step(self, command: FactoryStepDeleteCommand) -> None:
        folder, _ = self._folder(command.name)
        self._read_as(command.name, command.hash)
        steps = read_factory_file(folder).steps
        step = next((step for step in steps if step.id == command.step), None)
        if step is None:
            raise InvalidConfig({"step": f'The factory declares no step "{command.step}".'})
        if not step.off:
            raise InvalidConfig({"step": f'Step "{step.id}" is on. Turn it off to delete it.'})
        prompts = [other.prompt for other in steps if isinstance(other, WorkbenchAgentStep)]
        unused = (
            [step.prompt]
            if isinstance(step, WorkbenchAgentStep) and prompts.count(step.prompt) == 1
            else []
        )
        text = (folder / FACTORY_FILE).read_text(encoding="utf-8")
        self._write(command.name, folder, {FACTORY_FILE: without_step(text, step.id)}, unused)

    def _read_as(self, name: FactoryName, read: FileHash | None) -> Path | None:
        """The factory's folder, or None for a factory no source has, unless it changed since the
        client read it as *read*."""
        self._take_in(name)
        current = self._folders().get(name)
        folder = None if current is None else current[0]
        if (None if folder is None else files_hash(_factory_files(folder))) != read:
            raise StaleWrite(f'Factory "{name}" changed since it was read. Read it again.')
        return folder

    def _write(
        self,
        name: FactoryName,
        folder: Path | None,
        files: Mapping[str, str],
        removed: Iterable[str] = (),
    ) -> None:
        """Write *files* over the factory in *folder*, or a new one, without the files *removed*,
        and keep them as a web version if the check passes."""
        with self._staging(name) as staged:
            if folder is None:
                staged.mkdir()
            else:
                shutil.copytree(folder, staged, ignore=_BYTECODE)
            for path, content in files.items():
                target = staged / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            for path in removed:
                (staged / path).unlink(missing_ok=True)
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


def _needing_off(text: str) -> dict[str, str]:
    """What each step that's on in the factory file *text* needs of an off step, by its id."""
    needing: dict[str, list[str]] = {}
    for step, problem in off_dependents(parse_factory_file(text)):
        needing.setdefault(step, []).append(problem)
    return {step: " ".join(problems) for step, problems in needing.items()}


def _in_folder(field: str, path: str) -> None:
    relative = PurePosixPath(path)
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise InvalidConfig({field: f'"{path}" is not a path in the factory\'s folder.'})


def _declared_in(folder: Path) -> dict[str, str]:
    """The file of *folder* that declares each of its checks and tools, by name: each decorated
    function of its hooks and tools folders. A later file wins, as the hub loads them."""
    declared: dict[str, str] = {}
    for path in (
        *sorted((folder / HOOKS_DIR).glob("*.py")),
        *sorted((folder / TOOLS_DIR).glob("*.py")),
    ):
        try:
            module = ast.parse(path.read_bytes())
        except SyntaxError, ValueError:
            continue
        declared |= {
            node.name: path.relative_to(folder).as_posix()
            for node in module.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.decorator_list
        }
    return declared


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
