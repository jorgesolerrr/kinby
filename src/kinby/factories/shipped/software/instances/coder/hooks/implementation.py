"""Check what the coding client left on the agent branch: its work, committed.

The client's own report never counts (ADR 0076). A client that committed its work and then
wrote a malformed stream passes. One that says it is done with nothing committed fails.
"""

import subprocess
from pathlib import Path

from kinby.plugins.hooks import HookResult, StepEnd, hook


class UncommittedWork(RuntimeError):
    """The step's work is not committed on the run's branch."""


@hook
def check_implementation(end: StepEnd) -> HookResult | None:
    """Fail the step unless the workspace is on the run's branch, the branch has commits ahead
    of its base, and nothing is left uncommitted."""
    branch, base = str(end.results["branch"]), str(end.results["base"])
    current = _git(end.workspace, "branch", "--show-current")
    if current != branch:
        raise UncommittedWork(f"The workspace is on {current or 'no branch'}, not on {branch}.")
    if _git(end.workspace, "rev-list", "--count", f"origin/{base}..{branch}") == "0":
        raise UncommittedWork(f"Branch {branch} has no commits ahead of {base}.")
    changes = _git(end.workspace, "status", "--porcelain", "--untracked-files=all")
    if changes:
        raise UncommittedWork(f"The workspace has changes no commit on {branch} holds:\n{changes}")
    return None


def _git(workspace: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments), cwd=workspace, capture_output=True, text=True, check=True
    ).stdout.strip()
