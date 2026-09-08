"""Record the workspace at a turn boundary in a shadow repository kinby owns."""

from __future__ import annotations

import asyncio
import logging
from enum import StrEnum
from pathlib import Path
from typing import NewType, Protocol
from uuid import UUID

from kinby.contracts import TreeId

logger = logging.getLogger(__name__)

SNAPSHOTS_DIR = "snapshots.git"
_AUTHOR_NAME = "kinby"
_AUTHOR_EMAIL = "kinby@localhost"

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


class WorkspaceSnapshots:
    """A bare git repository under ``.state`` with the workspace as its work tree."""

    def __init__(self, git_dir: Path, work_tree: Path) -> None:
        self._git_dir = git_dir
        self._work_tree = work_tree
        # Turns on different threads share one workspace, one index and one lock file.
        self._capturing = asyncio.Lock()

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
        await snapshots._create()
        return snapshots

    async def capture(self, ref: SnapshotRef) -> TreeId:
        """Stage the whole work tree, commit it, point *ref* at the commit."""
        async with self._capturing:
            # The index outlives the capture, so a file staged before a .gitignore rule
            # matched it would stay staged. Rebuild from the work tree every time.
            await self._git("read-tree", "--empty")
            await self._git("add", "--all")
            tree = TreeId(await self._git("write-tree"))
            commit = await self._git("commit-tree", tree, "-m", ref)
            await self._git("update-ref", ref, commit)
        return tree

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
        return await _run_git(
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
        await process.wait()
        raise
    except OSError as exc:
        raise SnapshotError(f"git {_subcommand(arguments)} could not run: {exc}") from exc
    if process.returncode != 0:
        reason = stderr.decode(errors="replace").strip()
        raise SnapshotError(f"git {_subcommand(arguments)} failed: {reason}")
    return stdout.decode().strip()


def _subcommand(arguments: tuple[str, ...]) -> str:
    """The verb in a git call, past the --git-dir and --work-tree flags it carries."""
    return next((argument for argument in arguments if not argument.startswith("-")), "git")
