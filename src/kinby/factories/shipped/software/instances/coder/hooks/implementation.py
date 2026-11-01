"""Check what the coding client left on the agent branch: its work, committed. Record the words
it wrote for the pull request.

The client's own report never counts (ADR 0076). A client that committed its work and then
wrote a malformed stream passes. One that says it is done with nothing committed fails, unless
it wrote why the ticket needs no change.
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
#: Where the coding client writes why the ticket needs no change, which it records as
#: ``no_change``.
NO_CHANGE = ".scratch/no-change.md"
#: How many uncommitted paths the check names before it counts the rest.
_NAMED_CHANGES = 20


class UncommittedWork(RuntimeError):
    """The step's work is not committed on the run's branch."""


class LostBase(RuntimeError):
    """The run's base is gone from origin, and no pull request of it merged."""


class ChangeAndNoChange(RuntimeError):
    """The coding client says the ticket needs no change, and committed a change."""


class UnansweredThreads(RuntimeError):
    """The coding client left a review thread without a reply."""


@hook
def check_implementation(end: StepEnd) -> HookResult | None:
    """Fail the step unless the workspace is on the run's branch, the branch has commits ahead
    of its base, and nothing is left uncommitted. Record the pull request title and body the
    step wrote.

    A step that wrote why the ticket needs no change ends with the outcome ``no-change``, and
    fails when the branch has commits ahead of its base, which the cancelled run would never
    publish.
    """
    words = _taken_words(end.workspace)
    reason = _taken(end.workspace / NO_CHANGE)
    branch, base = str(end.results["branch"]), str(end.results["base"])
    _check_committed(end.workspace, branch)
    ahead = bool(_ahead(end.workspace, branch, base))
    if reason and ahead:
        raise ChangeAndNoChange(
            f"{NO_CHANGE} says the ticket needs no change, but branch {branch} has commits "
            f"ahead of {base}."
        )
    if reason:
        return HookResult(values={**words, "no_change": reason}, outcome="no-change")
    if not ahead:
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
    ahead = _ahead(end.workspace, branch, base)
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

    Each file is read once and removed, whatever the step's checks say next, so a file left
    behind never becomes a later step's words.
    """
    words = {name: _taken(workspace / path) for name, path in WORDS.items()}
    return {name: written for name, written in words.items() if written}


def _taken(file: Path) -> str:
    """What *file* holds, stripped, once it is removed. Empty when there is no file."""
    if not file.is_file():
        return ""
    written = file.read_text(encoding="utf-8").strip()
    file.unlink()
    return written


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


def _ahead(workspace: Path, branch: str, base: str) -> list[str]:
    """The commits of *branch* past its base, or, once the base merged and left origin, past both
    its last head and the branch it landed on.

    The coder's github tools count them the same way. A plugin file loads alone, so each keeps
    its own copy.
    """
    merged = _merged_base(workspace, base)
    past = (f"origin/{base}",) if merged is None else (merged[0], f"origin/{merged[1]}")
    return _git(workspace, "rev-list", branch, *(f"^{ref}" for ref in past)).split()


def _merged_base(workspace: Path, base: str) -> tuple[str, str] | None:
    """The last head of *base*'s merged pull request and the branch it landed on, both fetched,
    once *base* is gone from origin. None while origin has *base*."""
    head = None
    while not _git(workspace, "ls-remote", "--heads", "origin", f"refs/heads/{base}"):
        merged = json.loads(
            _run(
                workspace,
                *("gh", "pr", "list", "--state", "merged", "--head", base),
                *("--json", "headRefOid,baseRefName", "--limit", "1"),
            )
        )
        if not merged:
            raise LostBase(f"Branch {base} is gone from origin, and no pull request of it merged.")
        head = head or merged[0]["headRefOid"]
        base = merged[0]["baseRefName"]
    if head is None:
        return None
    _git(workspace, "fetch", "origin", head, base)
    return head, base


def _resolved(workspace: Path, named: str) -> str:
    """The full SHA of the commit *named*, or empty when it names none."""
    return subprocess.run(
        ("git", "rev-parse", "--verify", "--quiet", "--end-of-options", f"{named}^{{commit}}"),
        cwd=workspace,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git(workspace: Path, *arguments: str) -> str:
    return _run(workspace, "git", *arguments)


def _run(workspace: Path, *command: str) -> str:
    return subprocess.run(
        command, cwd=workspace, capture_output=True, text=True, check=True
    ).stdout.strip()
