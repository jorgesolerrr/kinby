"""Record the workspace at a turn boundary in a shadow repository kinby owns."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import NewType, Protocol
from uuid import UUID

from kinby.contracts import ChangeStatus, FileChange, TreeId

logger = logging.getLogger(__name__)

SNAPSHOTS_DIR = "snapshots.git"
_AUTHOR_NAME = "kinby"
_AUTHOR_EMAIL = "kinby@localhost"
_CANCEL_WAIT_SECONDS = 5.0

SnapshotRef = NewType("SnapshotRef", str)


class SnapshotBoundary(StrEnum):
    """Which end of a turn a workspace snapshot records."""

    BEFORE = "before"
    AFTER = "after"


def snapshot_ref(thread_id: UUID, turn_id: UUID, boundary: SnapshotBoundary) -> SnapshotRef:
    """The ref a turn's snapshot lives under, so plain git can list it."""
    return SnapshotRef(f"refs/kinby/snapshots/{thread_id}/{turn_id}/{boundary}")


class SnapshotError(Exception):
    """A git command for a workspace snapshot failed."""


class SnapshotStore(Protocol):
    async def capture(self, ref: SnapshotRef) -> TreeId: ...

    async def diff(self, before: TreeId, after: TreeId) -> WorkspaceDiff: ...

    async def preview_restore(self, tree: TreeId) -> WorkspaceDiff: ...

    async def restore(self, tree: TreeId) -> None: ...


@dataclass(frozen=True)
class WorkspaceDiff:
    files: list[FileChange]
    patch: str


class WorkspaceSnapshots:
    """A bare git repository under ``.state`` with the workspace as its work tree."""

    def __init__(self, git_dir: Path, work_tree: Path) -> None:
        self._git_dir = git_dir
        self._work_tree = work_tree
        # Turns on different threads share one workspace, one index and one lock file,
        # so every command that rebuilds the index waits its turn here.
        self._rebuilding_the_index = asyncio.Lock()

    @staticmethod
    async def open(
        state_dir: Path,
        workspace: Path,
        *,
        enabled: bool,
    ) -> WorkspaceSnapshots | None:
        """Return the store, or ``None`` when snapshots are off or git is absent."""
        if not enabled:
            return None
        if not await _git_is_installed():
            logger.warning("Git is not installed, so kinby records no workspace snapshots.")
            return None
        snapshots = WorkspaceSnapshots(state_dir / SNAPSHOTS_DIR, workspace)
        try:
            await snapshots._create()
        except SnapshotError:
            logger.warning(
                "The workspace snapshot store could not be opened.",
                exc_info=True,
            )
            return None
        return snapshots

    async def capture(self, ref: SnapshotRef) -> TreeId:
        """Stage the whole work tree, commit it, point *ref* at the commit."""
        async with self._rebuilding_the_index:
            await self._stage_work_tree()
            tree = TreeId(await self._git("write-tree"))
            commit = await self._git("commit-tree", tree, "-m", ref)
            await self._git("update-ref", ref, commit)
        return tree

    async def restore(self, tree: TreeId) -> None:
        """Make the work tree match *tree*, leaving ignored files where they are."""
        async with self._rebuilding_the_index:
            # read-tree only removes what the index knows, so a file added since the
            # last capture has to be staged first or it would survive the restore.
            await self._stage_work_tree()
            await self._git("read-tree", "--reset", "-u", tree)

    async def preview_restore(self, tree: TreeId) -> WorkspaceDiff:
        """Describe the changes that restoring *tree* would make."""
        async with self._rebuilding_the_index:
            await self._stage_work_tree()
            current = TreeId(await self._git("write-tree"))
            return await self._diff(current, tree)

    async def _stage_work_tree(self) -> None:
        # The index outlives a capture, so a file staged before a .gitignore rule
        # matched it would stay staged. Rebuild from the work tree every time.
        await self._git("read-tree", "--empty")
        await self._git("add", "--all")

    async def diff(self, before: TreeId, after: TreeId) -> WorkspaceDiff:
        """Describe the paths and line changes between two workspace snapshots."""
        return await self._diff(before, after)

    async def _diff(self, before: TreeId, after: TreeId) -> WorkspaceDiff:
        statuses = _parse_statuses(
            await self._git("diff", "--name-status", "-z", "-M", before, after)
        )
        counts = _parse_numstat(await self._git("diff", "--numstat", "-z", "-M", before, after))
        files = [
            FileChange(
                path=path,
                status=status,
                additions=counts[path][0],
                deletions=counts[path][1],
            )
            for path, status in statuses
        ]
        patch = await self._git_output("diff", "-p", "-M", before, after)
        return WorkspaceDiff(files, patch)

    async def _create(self) -> None:
        if self._git_dir.is_dir():
            return
        # init.defaultObjectFormat or GIT_DEFAULT_HASH would otherwise decide the
        # hash, and a TreeId is forty hex characters.
        await _run_git(
            "init",
            "--bare",
            "--quiet",
            "--object-format=sha1",
            str(self._git_dir),
            cwd=None,
        )
        # A restored file must come back byte for byte, whatever the platform,
        # and commit-tree needs an identity the user's git config cannot supply.
        for name, value in (
            ("core.autocrlf", "false"),
            ("core.safecrlf", "false"),
            ("user.name", _AUTHOR_NAME),
            ("user.email", _AUTHOR_EMAIL),
        ):
            await _run_git(f"--git-dir={self._git_dir}", "config", name, value, cwd=None)

    async def _git(self, *arguments: str) -> str:
        return (await self._git_output(*arguments)).strip()

    async def _git_output(self, *arguments: str) -> str:
        return await _run_git_output(
            f"--git-dir={self._git_dir}",
            f"--work-tree={self._work_tree}",
            *arguments,
            cwd=self._work_tree,
        )


async def _git_is_installed() -> bool:
    try:
        await _run_git("--version", cwd=None)
    except SnapshotError:
        return False
    return True


async def _run_git(*arguments: str, cwd: Path | None) -> str:
    return (await _run_git_output(*arguments, cwd=cwd)).strip()


async def _run_git_output(*arguments: str, cwd: Path | None) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            *arguments,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise SnapshotError(f"git {_subcommand(arguments)} could not run: {exc}") from exc
    try:
        stdout, stderr = await process.communicate()
    except asyncio.CancelledError:
        # An interrupt cancels the turn mid-capture. Let git finish and release
        # index.lock, or the capture that closes the turn finds the lock taken.
        # These commands write at most a hex id, so no pipe fills while we wait.
        try:
            await asyncio.wait_for(process.wait(), timeout=_CANCEL_WAIT_SECONDS)
        except TimeoutError:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
            await process.wait()
        raise
    except OSError as exc:
        raise SnapshotError(f"git {_subcommand(arguments)} could not run: {exc}") from exc
    if process.returncode != 0:
        reason = stderr.decode(errors="replace").strip()
        raise SnapshotError(f"git {_subcommand(arguments)} failed: {reason}")
    return stdout.decode()


def _subcommand(arguments: tuple[str, ...]) -> str:
    """The verb in a git call, past the --git-dir and --work-tree flags it carries."""
    return next((argument for argument in arguments if not argument.startswith("-")), "git")


def _parse_statuses(output: str) -> list[tuple[str, ChangeStatus]]:
    tokens = output.split("\0")
    changes: list[tuple[str, ChangeStatus]] = []
    index = 0
    while index < len(tokens) and tokens[index]:
        code = tokens[index]
        path = tokens[index + 1]
        index += 2
        if code.startswith("R"):
            path = tokens[index]
            index += 1
        changes.append((path, _change_status(code)))
    return changes


def _parse_numstat(output: str) -> dict[str, tuple[int, int]]:
    tokens = output.split("\0")
    counts: dict[str, tuple[int, int]] = {}
    index = 0
    while index < len(tokens) and tokens[index]:
        additions, deletions, path = tokens[index].split("\t", 2)
        index += 1
        if not path:
            index += 1
            path = tokens[index]
            index += 1
        counts[path] = (_line_count(additions), _line_count(deletions))
    return counts


def _change_status(code: str) -> ChangeStatus:
    return {
        "A": ChangeStatus.ADDED,
        "M": ChangeStatus.MODIFIED,
        "D": ChangeStatus.DELETED,
        "R": ChangeStatus.RENAMED,
        "T": ChangeStatus.MODIFIED,
    }[code[0]]


def _line_count(value: str) -> int:
    return 0 if value == "-" else int(value)
