"""Check what the coding client left on the agent branch: its work, committed.

The client's own report never counts (ADR 0076). A client that committed its work and then
wrote a malformed stream passes. One that says it is done with nothing committed fails.
"""

import json
import subprocess
from pathlib import Path

from kinby.plugins.hooks import HookResult, StepEnd, hook

#: Where the coding client writes its reply to each review thread a fix round answers.
REPLIES = ".scratch/review-replies.json"


class UncommittedWork(RuntimeError):
    """The step's work is not committed on the run's branch."""


class UnansweredThreads(RuntimeError):
    """The coding client left a review thread without a reply."""


@hook
def check_implementation(end: StepEnd) -> HookResult | None:
    """Fail the step unless the workspace is on the run's branch, the branch has commits ahead
    of its base, and nothing is left uncommitted."""
    branch, base = str(end.results["branch"]), str(end.results["base"])
    _check_committed(end.workspace, branch)
    if _git(end.workspace, "rev-list", "--count", f"origin/{base}..{branch}") == "0":
        raise UncommittedWork(f"Branch {branch} has no commits ahead of {base}.")
    return None


@hook
def check_answers(end: StepEnd) -> HookResult | None:
    """Record a fix round's replies, once each review thread has one and its fixes are committed
    on the run's branch. Before the pull request opens there is no thread to answer.

    The replies file is read once, so a round never takes an earlier round's replies.
    """
    feedback = end.results.get("feedback")
    if not isinstance(feedback, str):
        return None
    branch = str(end.results["branch"])
    path = end.workspace / REPLIES
    try:
        replies = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UnansweredThreads(f"{REPLIES} holds no replies: {exc}") from exc
    finally:
        path.unlink(missing_ok=True)
    threads = {thread["id"] for thread in json.loads(feedback)}
    if not isinstance(replies, dict) or set(replies) != threads:
        raise UnansweredThreads(
            f"{REPLIES} does not reply to exactly the threads {', '.join(sorted(threads))}."
        )
    for thread, reply in replies.items():
        if (
            not isinstance(reply, dict)
            or not isinstance(reply.get("fixed"), bool)
            or not isinstance(reply.get("reply"), str)
            or not reply["reply"].strip()
        ):
            raise UnansweredThreads(f'The reply to thread {thread} is not {{"fixed", "reply"}}.')
    _check_committed(end.workspace, branch)
    fixed = any(reply["fixed"] for reply in replies.values())
    if fixed and _git(end.workspace, "rev-list", "--count", f"origin/{branch}..{branch}") == "0":
        raise UncommittedWork(f"The replies say a thread is fixed, but {branch} has no new commit.")
    return HookResult(values={"replies": json.dumps(replies)})


def _check_committed(workspace: Path, branch: str) -> None:
    """Fail unless the workspace is on *branch* with nothing left uncommitted."""
    current = _git(workspace, "branch", "--show-current")
    if current != branch:
        raise UncommittedWork(f"The workspace is on {current or 'no branch'}, not on {branch}.")
    changes = _git(workspace, "status", "--porcelain", "--untracked-files=all")
    if changes:
        raise UncommittedWork(f"The workspace has changes no commit on {branch} holds:\n{changes}")


def _git(workspace: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments), cwd=workspace, capture_output=True, text=True, check=True
    ).stdout.strip()
