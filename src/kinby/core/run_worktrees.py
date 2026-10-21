"""Run worktrees: the checkout of the workspace each factory run works in, on its own branch.

A run worktree lives under the instance directory, so it outlives the container and keeps one
path for its run's whole life. Every run worktree shares the workspace's clone: its branches,
remotes and excludes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from kinby.core.errors import CoreError
from kinby.instance import Instance

#: How long one git command may run, in seconds.
_GIT_TIMEOUT = 900


class RunWorktreeError(CoreError):
    """A run worktree could not be cut, found or removed."""


def run_worktree(instance: Instance, branch: str) -> Path:
    """The run worktree of *branch*, cut again when it is missing.

    A missing one is cut from the local branch, or from origin's when there is none. It fails
    unless the run worktree is on *branch*.
    """
    worktree = _path(instance, branch)
    workspace = instance.manifest.workspace.path
    if worktree not in _worktrees(workspace):
        _move_off(workspace, branch)
        _git(workspace, "worktree", "add", str(worktree), branch)
    current = _git(worktree, "branch", "--show-current")
    if current != branch:
        raise RunWorktreeError(f"The run worktree of {branch} is on {current or 'no branch'}.")
    return worktree


def cut_run_worktree(instance: Instance, branch: str, start: str) -> Path:
    """A fresh run worktree of *branch*, at *start*. One already there goes, with all it held."""
    worktree = _path(instance, branch)
    workspace = instance.manifest.workspace.path
    if worktree in _worktrees(workspace):
        _git(workspace, "worktree", "remove", "--force", str(worktree))
    _move_off(workspace, branch)
    _git(workspace, "worktree", "add", "-B", branch, str(worktree), start)
    return worktree


def remove_run_worktree(instance: Instance, branch: str) -> None:
    """Remove *branch*'s run worktree and its local branch, whichever exist.

    A branch another checkout is on stays.
    """
    worktree = _path(instance, branch)
    workspace = instance.manifest.workspace.path
    held = _worktrees(workspace)
    if held.pop(worktree, None) is not None:
        _git(workspace, "worktree", "remove", "--force", str(worktree))
    if branch not in held.values() and _git(workspace, "branch", "--list", branch):
        _git(workspace, "branch", "-D", branch)


def _path(instance: Instance, branch: str) -> Path:
    if branch.startswith("-") or Path(branch).is_absolute() or ".." in Path(branch).parts:
        raise RunWorktreeError(f'"{branch}" names no run worktree.')
    return (instance.path / "worktrees" / branch).resolve()


def _worktrees(workspace: Path) -> dict[Path, str]:
    """The branch each checkout of the clone is on, by its path. A detached one is on ""."""
    _git(workspace, "worktree", "prune")
    held: dict[Path, str] = {}
    path = workspace
    for line in _git(workspace, "worktree", "list", "--porcelain").splitlines():
        key, _, value = line.partition(" ")
        if key == "worktree":
            path = Path(value).resolve()
            held[path] = ""
        elif key == "branch":
            held[path] = value.removeprefix("refs/heads/")
    return held


def _move_off(workspace: Path, branch: str) -> None:
    """Detach the workspace at origin/HEAD when it is on *branch*, which a run worktree then
    takes. Changes no commit holds keep it where it is, and fail."""
    if _git(workspace, "branch", "--show-current") != branch:
        return
    changes = _git(workspace, "status", "--porcelain", "--untracked-files=normal")
    if changes:
        raise RunWorktreeError(
            f"The workspace is on {branch} with changes no commit holds, so no run worktree is "
            f"cut from it:\n{changes}"
        )
    _git(workspace, "switch", "--detach", "origin/HEAD")


def _git(directory: Path, *arguments: str) -> str:
    """What a git command printed, without its trailing whitespace. One that exits with another
    code than zero raises."""
    try:
        ran = subprocess.run(
            ("git", *arguments),
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except OSError as exc:
        raise RunWorktreeError(f'"git {" ".join(arguments)}" could not start: {exc}') from exc
    if ran.returncode != 0:
        said = ran.stderr.strip() or ran.stdout.strip()
        raise RunWorktreeError(
            f'"git {" ".join(arguments)}" exited with code {ran.returncode}.\n{said}'.strip()
        )
    return ran.stdout.rstrip()
