"""Default hooks: record what most factories hand from step to step."""

import json
import subprocess

from kinby.plugins.hooks import HookResult, StepEnd, hook


@hook
def record_branch(end: StepEnd) -> HookResult | None:
    """Record the branch the workspace is on as ``branch``. A detached head records nothing."""
    found = subprocess.run(
        ("git", "symbolic-ref", "--short", "HEAD"),
        cwd=end.workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    if found.returncode != 0:
        return None
    return HookResult(values={"branch": found.stdout.strip()})


@hook
def find_pull_request(end: StepEnd) -> HookResult | None:
    """Record the pull request of the run's ``branch`` as ``pr``, with the GitHub CLI.

    Without a recorded branch, it looks for the pull request of the workspace's branch. A branch
    with no pull request records nothing.
    """
    branch = end.results.get("branch")
    found = subprocess.run(
        ("gh", "pr", "view", *([branch] if isinstance(branch, str) else []), "--json", "number"),
        cwd=end.workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    if found.returncode != 0:
        return None
    return HookResult(values={"pr": json.loads(found.stdout)["number"]})


HOOKS = (record_branch, find_pull_request)
