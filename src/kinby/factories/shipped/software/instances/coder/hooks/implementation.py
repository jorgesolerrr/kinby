"""Check what the coding client left on the agent branch: its work, committed. Record the words
it wrote for the pull request.

The client's own report never counts (ADR 0076). A client that committed its work and then
wrote a malformed stream passes. One that says it is done with nothing committed fails.
"""

import json
import subprocess
from pathlib import Path

from kinby.plugins.hooks import HookResult, StepEnd, hook

#: Where the coding client writes its reply to each review thread a fix round answers.
REPLIES = ".scratch/review-replies.json"
#: Where the coding client writes the pull request's title and body, by the value each is
#: recorded as (ADR 0083).
WORDS = {"title": ".scratch/pr-title.txt", "body": ".scratch/pr-body.md"}
#: How many uncommitted paths the check names before it counts the rest.
_NAMED_CHANGES = 20


class UncommittedWork(RuntimeError):
    """The step's work is not committed on the run's branch."""


class UnansweredThreads(RuntimeError):
    """The coding client left a review thread without a reply."""


@hook
def check_implementation(end: StepEnd) -> HookResult | None:
    """Fail the step unless the workspace is on the run's branch, the branch has commits ahead
    of its base, and nothing is left uncommitted. Record the pull request title and body the
    step wrote."""
    words = _taken_words(end.workspace)
    branch, base = str(end.results["branch"]), str(end.results["base"])
    _check_committed(end.workspace, branch)
    if _git(end.workspace, "rev-list", "--count", f"origin/{base}..{branch}") == "0":
        raise UncommittedWork(f"Branch {branch} has no commits ahead of {base}.")
    return HookResult(values=words) if words else None


@hook
def check_answers(end: StepEnd) -> HookResult | None:
    """Record a fix round's replies, once each review thread has one and each fixed reply names
    a commit of the run's branch ahead of its base, recorded by its full SHA. Before the pull
    request opens there is no thread to answer.

    The replies file is read once, so a round never takes an earlier round's replies. A fix
    round records the title or body it did not rewrite as empty, so publish never applies an
    earlier round's words again.
    """
    words = _taken_words(end.workspace)
    feedback = end.results.get("feedback")
    if not isinstance(feedback, str):
        return HookResult(values=words) if words else None
    branch, base = str(end.results["branch"]), str(end.results["base"])
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
            or (reply["fixed"] and not isinstance(reply.get("commit"), str))
        ):
            raise UnansweredThreads(
                f'The reply to thread {thread} is not {{"fixed", "reply"}}, with a "commit" when '
                "fixed."
            )
    _check_committed(end.workspace, branch)
    ahead = _git(end.workspace, "rev-list", f"origin/{base}..{branch}").split()
    for thread, reply in replies.items():
        if reply["fixed"]:
            named = reply["commit"]
            reply["commit"] = _resolved(end.workspace, named)
            if reply["commit"] not in ahead:
                raise UncommittedWork(
                    f"The reply to thread {thread} names {named}, which is no commit of {branch} "
                    f"ahead of {base}."
                )
    return HookResult(values={"title": "", "body": "", **words, "replies": json.dumps(replies)})


def _taken_words(workspace: Path) -> dict[str, str]:
    """The pull request title and body the coding client wrote, by value name.

    Each file is read once and removed, whatever the step's checks say next. Runs share the
    workspace, so a file left behind would become another run's words.
    """
    words = {}
    for name, path in WORDS.items():
        file = workspace / path
        if file.is_file():
            words[name] = file.read_text(encoding="utf-8").strip()
            file.unlink()
    return {name: written for name, written in words.items() if written}


def _check_committed(workspace: Path, branch: str) -> None:
    """Fail unless the workspace is on *branch* with nothing left uncommitted."""
    current = _git(workspace, "branch", "--show-current")
    if current != branch:
        raise UncommittedWork(f"The workspace is on {current or 'no branch'}, not on {branch}.")
    changes = _git(workspace, "status", "--porcelain", "--untracked-files=normal").splitlines()
    if changes:
        named = "\n".join(changes[:_NAMED_CHANGES])
        rest = len(changes) - _NAMED_CHANGES
        raise UncommittedWork(
            f"The workspace has changes no commit on {branch} holds:\n{named}"
            + (f"\nAnd {rest} more." if rest > 0 else "")
        )


def _resolved(workspace: Path, named: str) -> str:
    """The full SHA of the commit *named*, or empty when it names none."""
    return subprocess.run(
        ("git", "rev-parse", "--verify", "--quiet", "--end-of-options", f"{named}^{{commit}}"),
        cwd=workspace,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git(workspace: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments), cwd=workspace, capture_output=True, text=True, check=True
    ).stdout.strip()
