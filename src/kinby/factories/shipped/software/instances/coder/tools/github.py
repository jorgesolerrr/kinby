"""The software factory's work on GitHub: the scan, the agent branch, the pull request, and
babysitting it through review.

Every command runs in the workspace, the repository's clone, which stays on the default branch.
Each run works in its own run worktree of it, which shares its branches and remotes. gh runs as
the GH_TOKEN the instance holds.
"""

import asyncio
import json
import logging
import re
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from kinby.contracts import FactoryRunStatus
from kinby.core.run_worktrees import (
    cut_run_worktree,
    remove_run_worktree,
    run_worktree,
    sweep_run_worktrees,
)
from kinby.factories.file import FAILURE
from kinby.plugins.hooks import HookResult
from kinby.plugins.intake import hand_over
from kinby.plugins.tools import ToolContext, tool

READY_LABEL = "ready-for-agent"
READY_FOR_HUMAN_LABEL = "ready-for-human"
MERGE_READY_LABEL = "merge-ready"
#: What the merge-ready label says, when assess creates it in a repository that lacks it.
MERGE_READY_DESCRIPTION = "Checks passed and reviewed; ready for the maintainer to merge"
#: Every agent pull request's branch starts with it.
AGENT_BRANCH_PREFIX = "agent/"
#: Where the coding client writes what the factory reads, kept out of every commit.
SCRATCH = ".scratch"
#: What a pull request says when no adversarial review ran before it opened (ADR 0044).
REVIEW_NOT_RUN = (
    "\n\n## Review status\n\nAdversarial review was not run. Review happens on this pull request."
)
GITHUB_API_VERSION = "2026-03-10"
#: What a pull request is told when it is labeled merge-ready with no review of its head.
NO_REVIEW = (
    "No reviewer answered on this head within babysitting's quiet time, so it is labeled "
    "merge-ready without a review of it."
)
#: How long one gh or git command may run, in seconds.
_COMMAND_TIMEOUT = 900
#: Who may write ticket text and review feedback the coding client reads (ADRs 0073 and 0079), by
#: GitHub's author_association.
_TRUSTED = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
#: The review apps whose feedback babysitting answers, whatever their association (ADR 0079).
_TRUSTED_APPS = frozenset({"greptile-apps", "chatgpt-codex-connector"})
#: The apps whose ticket text the coding client reads, whatever their association (ADR 0094).
_TICKET_APPS = frozenset({"linear-code"})
#: The conclusions of a finished check run that leave a pull request merge-ready.
_PASSED = frozenset({"success", "neutral", "skipped", "stale"})
#: The conclusions of a failed check run that a coding session can repair.
_REPAIRABLE = frozenset({"failure", "timed_out"})
_REVIEW_THREADS = """
query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $endCursor) {
        nodes {
          id
          isResolved
          path
          line
          comments(last: 100) { nodes { author { __typename login } authorAssociation body } }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""
_REPLY = """
mutation($thread: ID!, $body: String!) {
  addPullRequestReviewThreadReply(input: {pullRequestReviewThreadId: $thread, body: $body}) {
    comment { id }
  }
}
"""
_RESOLVE = """
mutation($thread: ID!) {
  resolveReviewThread(input: {threadId: $thread}) { thread { id } }
}
"""
_AGENT_BRANCH = re.compile(rf"{AGENT_BRANCH_PREFIX}(\d+)-")
_CLOSES_ISSUE = re.compile(r"(?im)^Closes #(\d+)\s*$")
_ISSUE_URL_NUMBER = re.compile(r"/issues/(\d+)$")

_logger = logging.getLogger(__name__)


class CommandFailed(RuntimeError):
    """A gh or git command exited with a code other than zero."""


class MissingPullRequestBody(RuntimeError):
    """The coding client wrote no body for the pull request."""


class UntrustedTicket(RuntimeError):
    """Someone other than the repository's owner, a member, a collaborator or a ticket app wrote
    ticket text."""


class DivergedBlockers(RuntimeError):
    """No agent branch holds every open blocker's agent branch."""


class LostBase(RuntimeError):
    """The branch a pull request stacks on is gone, and the run's commits cannot follow it to
    where it merged."""


class UntrustedFeedback(RuntimeError):
    """Review feedback left to answer has an author babysitting does not trust."""


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    parent: int | None
    #: Its author, when not the repository's owner, a member, a collaborator or a ticket app.
    untrusted_author: str | None
    labels: frozenset[str]


@dataclass(frozen=True)
class Repository:
    default_branch: str
    #: Who reviews agent pull requests: the owner of a user's repository. GitHub cannot ask an
    #: organization for a review, so an organization's repository has none.
    maintainer: str | None


@dataclass(frozen=True)
class ReviewComment:
    author: str
    body: str
    #: Whether its author is the repository's owner, a member, a collaborator or a trusted app.
    trusted: bool


@dataclass(frozen=True)
class ReviewThread:
    id: str
    resolved: bool
    path: str
    line: int | None
    comments: tuple[ReviewComment, ...]


@dataclass(frozen=True)
class AgentPullRequest:
    number: int
    branch: str
    #: The issue its body closes.
    closes: int | None
    #: The stack GitHub keeps it in, if any.
    stack: int | None
    #: Who it asks for a review and has not had one from yet.
    requested: frozenset[str]


@tool(write=True)
async def scan_ready_issues(signal: dict[str, object], context: ToolContext) -> None:
    """Hand the oldest eligible issue to the factory, after a wake that can change which it is.

    An eligible issue is open and labeled ready-for-agent, has no agent pull request, and each
    open issue that blocks it has one in the same stack. The oldest stays eligible until its pull
    request opens or it goes to a human, and handing it again returns its run, so the factory
    implements one issue at a time. Runs babysitting their pull requests go on meanwhile.

    An issue a human labeled ready-for-agent again while it is still ready-for-human is handed
    over too, which retries its run, and loses its ready-for-human label once the retry starts.
    Its blockers must be as an eligible issue's, but it may have an agent pull request, since a
    run can stop after opening one. It is never the oldest, so younger issues don't wait behind
    it.
    """
    if context.routine is None:
        raise ValueError("Only the factory's intake routine scans for its work.")
    if not _can_change_eligibility(signal):
        return
    workspace = context.workspace
    relabeled, eligible = await asyncio.to_thread(
        _eligible_issues, workspace, _labeled_issue(signal)
    )
    for number in relabeled:
        run = await hand_over(context.routine, {"issue": number})
        if run.status is not FactoryRunStatus.NEEDS_HUMAN:
            removed = ("--remove-label", READY_FOR_HUMAN_LABEL)
            await asyncio.to_thread(_gh, workspace, "issue", "edit", str(number), *removed)
    if eligible is not None:
        await hand_over(context.routine, {"issue": eligible})


@tool(write=True)
def prepare_branch(issue: int, run_id: str, context: ToolContext) -> dict[str, str]:
    """Cut a fresh run worktree on the issue's agent branch, once a trusted author wrote all its
    text.

    The branch starts from one that holds every open blocker's agent branch, so its pull request
    stacks on the blockers' work. With no blocker open, it starts from the default branch.
    Whatever a run worktree already on the branch held goes. The workspace only fetches.

    First it sweeps the run worktrees whose issue is closed and whose branch has no open pull
    request, which an end of run missed. A sweep that fails is logged, and prepare goes on.
    """
    workspace = context.workspace
    found = _issue(workspace, issue)
    untrusted = _untrusted_authors(workspace, found)
    if untrusted:
        raise UntrustedTicket(
            f"Ticket text from untrusted authors needs a human: {', '.join(untrusted)}."
        )
    words = re.findall(r"[a-z0-9]+", found.title.lower())
    branch = f"{AGENT_BRANCH_PREFIX}{issue}-{'-'.join(words) or 'issue'}"
    _exclude_scratch(workspace)
    _git(workspace, "fetch", "--prune", "origin")
    try:
        sweep_run_worktrees(context.instance, lambda branch: _run_over(workspace, branch))
    except Exception:
        _logger.exception("The sweep of run worktrees whose issue is closed failed.")
    base = _base(workspace, found)
    cut_run_worktree(context.instance, branch, f"origin/{base}", run_id)
    return {"branch": branch, "base": base}


@tool(write=True)
def publish_pull_request(
    issue: int,
    branch: str,
    base: str,
    run_id: str,
    context: ToolContext,
    *,
    reviewed: bool = False,
    pr: int | None = None,
    replies: str | None = None,
    title: str = "",
    body: str = "",
) -> dict[str, int] | str:
    """Push the branch. The first time, open its pull request; after a fix round, apply the title
    and body the session rewrote, and reply on each review thread the round answered.

    *title* and *body* are the words this run's coding session wrote, as its steps recorded them
    (ADR 0083). An empty one keeps that part of the pull request as it is.

    A pull request opens on its base, or on where that base landed once it merged.
    """
    workspace = context.workspace
    if pr is None:
        if not body:
            raise MissingPullRequestBody("The coding session wrote no pull request body.")
        base = _landed_base(context, branch, base, run_id)
        return _open_pull_request(workspace, issue, branch, base, title, body, reviewed=reviewed)
    answered = json.loads(replies or "{}")
    return _push_fix_round(workspace, issue, branch, pr, answered, title, body, reviewed=reviewed)


@tool(write=True)
def assess_pull_request(pr: int, context: ToolContext, *, quiet: bool = False) -> HookResult:
    """Read the pull request after a wake, and say what babysitting does next.

    Merged, the run is done; closed without a merge, it stops. Merge-ready, once a trusted
    reviewer reviewed its head with no thread left to answer and no check still running or
    failed: it gets the merge-ready label and a review request for the maintainer, unless it holds
    them already, the label created first in a repository that lacks it. A trusted review app's
    successful check run on the head is a review of it, and its failed one is no failed check.
    After a *quiet* wait, one no reviewer reviewed is merge-ready too, and a comment says so,
    unless one already does. Threads only trusted authors wrote are feedback for a fix round.
    Another author's thread needs a human. With no thread to answer, checks that failed or timed
    out go to a check repair. Anything else waits for the next wake. Nothing here ever merges.

    Only the newest run of each check on the head counts.
    """
    workspace = context.workspace
    pull = _api(workspace, f"repos/{{owner}}/{{repo}}/pulls/{pr}")
    if pull["state"] == "closed":
        if pull["merged"]:
            return HookResult(values={"merge_ready": True}, outcome="merged")
        return HookResult(outcome="closed")
    head = pull["head"]["sha"]
    checks = _newest_runs(
        _pages(workspace, f"repos/{{owner}}/{{repo}}/commits/{head}/check-runs", key="check_runs")
    )
    if any(check["status"] != "completed" for check in checks):
        return HookResult(outcome="waiting")
    coder = _gh(workspace, "api", "user", "--jq", ".login").strip()
    actionable = [
        thread
        for thread in _review_threads(workspace, pr)
        if not thread.resolved and thread.comments and thread.comments[-1].author != coder
    ]
    answerable = [
        thread
        for thread in actionable
        if all(comment.trusted or comment.author == coder for comment in thread.comments)
    ]
    if answerable:
        feedback = [
            {
                "id": thread.id,
                "path": thread.path,
                "line": thread.line,
                "comments": [
                    {"author": comment.author, "body": comment.body} for comment in thread.comments
                ],
            }
            for thread in answerable
        ]
        return HookResult(values={"feedback": json.dumps(feedback), "pr": pr}, outcome="feedback")
    if actionable:
        untrusted = {
            comment.author
            for thread in actionable
            for comment in thread.comments
            if not comment.trusted and comment.author != coder
        }
        raise UntrustedFeedback(
            f"Review feedback from untrusted authors needs a human: {', '.join(sorted(untrusted))}."
        )
    failed = [
        check
        for check in checks
        if check["conclusion"] not in _PASSED and not _from_trusted_app(check)
    ]
    if repairable := [check for check in failed if check["conclusion"] in _REPAIRABLE]:
        failure = "Checks failed on the pull request's head:" + "".join(
            f"\n- {check['name']} ({check['conclusion']}): {check['html_url']}"
            for check in repairable
        )
        return HookResult(
            values={FAILURE: failure, "pr": pr}, outcome="checks-failed", summary=failure
        )
    if failed:
        return HookResult(outcome="waiting")
    reviews = _pages(workspace, f"repos/{{owner}}/{{repo}}/pulls/{pr}/reviews")
    reviewed = any(
        check["conclusion"] == "success" and _from_trusted_app(check) for check in checks
    ) or any(
        review["commit_id"] == head
        and (user := review["user"] or {"login": "ghost", "type": "User"})["login"] != coder
        and _trusted(review["author_association"], _bot_app(user["login"], user["type"]))
        for review in reviews
    )
    if not reviewed:
        if not quiet:
            return HookResult(outcome="waiting")
        comments = _pages(workspace, f"repos/{{owner}}/{{repo}}/issues/{pr}/comments")
        if all(comment["body"] != NO_REVIEW for comment in comments):
            _gh(workspace, "pr", "comment", str(pr), "--body", NO_REVIEW)
    maintainer = _repository(workspace).maintainer
    labels = {label["name"] for label in pull["labels"]}
    if MERGE_READY_LABEL not in labels and not _has_merge_ready_label(workspace):
        described = ("--description", MERGE_READY_DESCRIPTION)
        try:
            _gh(workspace, "label", "create", MERGE_READY_LABEL, *described)
        except CommandFailed:
            if not _has_merge_ready_label(workspace):
                raise
    asked = {pull["user"]["login"], *(user["login"] for user in pull["requested_reviewers"])}
    edits = [
        *(() if MERGE_READY_LABEL in labels else ("--add-label", MERGE_READY_LABEL)),
        *(() if maintainer is None or maintainer in asked else ("--add-reviewer", maintainer)),
    ]
    if edits:
        _gh(workspace, "pr", "edit", str(pr), *edits)
    return HookResult(values={"merge_ready": True}, outcome="merge-ready")


@tool(write=True)
def report_needs_human(
    issue: int,
    step: str,
    summary: str,
    run_id: str,
    context: ToolContext,
    *,
    branch: str | None = None,
    base: str | None = None,
) -> None:
    """Hand the issue to a human: push the agent branch if it has commits ahead of its base, label
    the issue ready-for-human, and comment why the run stopped, how to get it going again, and
    which open issues are blocked behind it.

    Uncommitted work stays uncommitted in the run worktree. A push that fails is said in the
    comment, so the report still reaches the human.
    """
    workspace = context.workspace
    body = [
        f"The software factory stopped at step `{step}`: {summary}",
        f"Run `{run_id}`. Add `{READY_LABEL}` to retry this step; send back or cancel from the "
        "web app or `hub.sh`.",
    ]
    if branch is not None and base is not None:
        try:
            if _git(workspace, "rev-list", "--count", f"origin/{base}..{branch}").strip() != "0":
                _git(workspace, "push", "--force-with-lease", "-u", "origin", branch)
        except CommandFailed as failed:
            body.append(f"Pushing `{branch}` failed: {failed}")
    if blocked := _blocked_behind(workspace, issue):
        body.append(
            "Blocked behind this issue:"
            + "".join(f"\n- #{found['number']} {found['title']}" for found in blocked)
        )
    _gh(
        workspace,
        "issue",
        "edit",
        str(issue),
        "--remove-label",
        READY_LABEL,
        "--add-label",
        READY_FOR_HUMAN_LABEL,
    )
    _gh(workspace, "issue", "comment", str(issue), "--body", "\n\n".join(body))


@tool(write=True)
def report_ended(
    issue: int,
    run_id: str,
    context: ToolContext,
    *,
    branch: str | None = None,
    no_change: str | None = None,
) -> None:
    """Remove the run's run worktree and local branch, unless a later run cut them again, and
    take ready-for-human off the issue. Then say on the issue why a run that ended made no change,
    and take ready-for-agent off it.

    The issue stays open: closing it is the maintainer's call.
    """
    if branch is not None:
        remove_run_worktree(context.instance, branch, run_id)
    workspace = context.workspace
    if READY_FOR_HUMAN_LABEL in _issue(workspace, issue).labels:
        _gh(workspace, "issue", "edit", str(issue), "--remove-label", READY_FOR_HUMAN_LABEL)
    if no_change is None:
        return
    _gh(workspace, "issue", "edit", str(issue), "--remove-label", READY_LABEL)
    body = (
        f"The software factory made no change for this issue: {no_change}\n\n"
        f"Close it if you agree, or add `{READY_LABEL}` to try again."
    )
    _gh(workspace, "issue", "comment", str(issue), "--body", body)


def _open_pull_request(
    workspace: Path, issue: int, branch: str, base: str, title: str, body: str, *, reviewed: bool
) -> dict[str, int]:
    """Push the branch and open its pull request, which closes the issue, with the title and body
    the coding client wrote. With no title written, it takes the issue's. A pull request the
    branch already has takes the base, title and body instead, and the maintainer's review request
    when it lacks one, so opening twice opens one.

    A pull request based on another agent pull request's branch joins that pull request's stack,
    whatever the label on the issue it closes. The repository's maintainer reviews it. An issue an
    earlier failure handed to a human loses its ready-for-human label.
    """
    found = _issue(workspace, issue)
    pulls = _agent_pull_requests(workspace)
    existing = next((pull for pull in pulls if pull.branch == branch), None)
    under = next((pull for pull in pulls if pull.branch == base), None)
    maintainer = _repository(workspace).maintainer
    _git(workspace, "push", "--force-with-lease", "-u", "origin", branch)
    fields = (
        "--base",
        base,
        "--title",
        title or found.title,
        "--body",
        _body(issue, body, reviewed=reviewed),
    )
    if existing is None:
        reviewer = () if maintainer is None else ("--reviewer", maintainer)
        url = _gh(workspace, "pr", "create", "--head", branch, *fields, *reviewer)
        number = int(url.strip().rsplit("/", 1)[1])
    else:
        number = existing.number
        asked = maintainer is None or maintainer in existing.requested
        reviewer = () if asked else ("--add-reviewer", maintainer)
        _gh(workspace, "pr", "edit", str(number), *fields, *reviewer)
    if under is not None and (existing is None or existing.stack is None):
        _stack(workspace, under, number)
    if READY_FOR_HUMAN_LABEL in found.labels:
        _gh(workspace, "issue", "edit", str(issue), "--remove-label", READY_FOR_HUMAN_LABEL)
    return {"pr": number}


def _landed_base(context: ToolContext, branch: str, base: str, run_id: str) -> str:
    """*base*, or the branch it landed on when its pull request merged and took it from origin.

    A blocker's pull request can merge while the run stacked on it is still coding. The run's
    own commits, those past the blocker's last head, then move onto the branch it merged into, as
    GitHub retargets a pull request already open. A base gone unmerged needs a human, and so do
    commits that conflict there.
    """
    workspace = context.workspace
    head = None
    while not _git(workspace, "ls-remote", "--heads", "origin", f"refs/heads/{base}").strip():
        merged = json.loads(
            _gh(
                workspace,
                *("pr", "list", "--state", "merged", "--head", base),
                *("--json", "headRefOid,baseRefName", "--limit", "1"),
            )
        )
        if not merged:
            raise LostBase(f"Branch {base} is gone from origin, and no pull request of it merged.")
        head = head or merged[0]["headRefOid"]
        base = merged[0]["baseRefName"]
    if head is None:
        return base
    worktree = run_worktree(context.instance, branch, run_id)
    _git(worktree, "fetch", "origin", head, base)
    try:
        _git(worktree, "rebase", "--onto", f"origin/{base}", head, branch)
    except CommandFailed as failed:
        _git(worktree, "rebase", "--abort")
        raise LostBase(f"Branch {branch} does not rebase onto {base}: {failed}") from failed
    return base


def _push_fix_round(
    workspace: Path,
    issue: int,
    branch: str,
    pr: int,
    replies: dict[str, dict],
    title: str,
    body: str,
    *,
    reviewed: bool,
) -> str:
    """Push the fix round's commits and apply the title and body the coding client rewrote, then
    reply on each thread it answered and resolve each it fixed. A fix's reply names the commit
    that holds it."""
    _git(workspace, "push", "--force-with-lease", "-u", "origin", branch)
    edits = [
        *(("--title", title) if title else ()),
        *(("--body", _body(issue, body, reviewed=reviewed)) if body else ()),
    ]
    if edits:
        _gh(workspace, "pr", "edit", str(pr), *edits)
    for thread, reply in replies.items():
        body = f"{reply['commit']}: {reply['reply']}" if reply["fixed"] else reply["reply"]
        _gh(
            workspace,
            "api",
            "graphql",
            "-f",
            f"query={_REPLY}",
            "-f",
            f"thread={thread}",
            "-f",
            f"body={body}",
        )
        if reply["fixed"]:
            _gh(workspace, "api", "graphql", "-f", f"query={_RESOLVE}", "-f", f"thread={thread}")
    return f"Pushed {branch} and replied on {len(replies)} review threads."


def _body(issue: int, written: str, *, reviewed: bool) -> str:
    """The pull request's body: it closes the issue, then holds what the coding client wrote, and
    says so when no review step found the change clean."""
    first, _, rest = written.partition("\n")
    if (closes := _CLOSES_ISSUE.fullmatch(first)) is not None and int(closes[1]) == issue:
        written = rest.strip()
    return f"Closes #{issue}\n\n{written}{'' if reviewed else REVIEW_NOT_RUN}"


def _review_threads(workspace: Path, pr: int) -> list[ReviewThread]:
    """The pull request's review threads, each with its comments, oldest first."""
    pages = json.loads(
        _gh(
            workspace,
            "api",
            "graphql",
            "--paginate",
            "--slurp",
            "-F",
            "owner={owner}",
            "-F",
            "name={repo}",
            "-F",
            f"number={pr}",
            "-f",
            f"query={_REVIEW_THREADS}",
        )
    )
    return [
        ReviewThread(
            id=node["id"],
            resolved=node["isResolved"],
            path=node["path"],
            line=node["line"],
            comments=tuple(_review_comment(comment) for comment in node["comments"]["nodes"]),
        )
        for page in pages
        for node in page["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    ]


def _review_comment(node: dict) -> ReviewComment:
    # A deleted account's comment has no author.
    author = node["author"] or {"__typename": "User", "login": "ghost"}
    app = _bot_app(author["login"], author["__typename"])
    return ReviewComment(
        author=author["login"], body=node["body"], trusted=_trusted(node["authorAssociation"], app)
    )


def _newest_runs(runs: list[dict]) -> list[dict]:
    """The newest run of each check, a check being its app and name (ADR 0109).

    A run not started yet has no start, and is the newest.
    """
    newest: dict[tuple[str | None, str], dict] = {}
    for run in sorted(
        runs, key=lambda run: (run["started_at"] is None, run["started_at"] or "", run["id"])
    ):
        newest[(run["app"] or {}).get("slug"), run["name"]] = run
    return list(newest.values())


def _from_trusted_app(check: dict) -> bool:
    return (check["app"] or {}).get("slug") in _TRUSTED_APPS


def _trusted(association: str, app: str | None) -> bool:
    return association in _TRUSTED or app in _TRUSTED_APPS


def _bot_app(login: str, kind: str) -> str | None:
    """REST gives a bot's login with a `[bot]` suffix, GraphQL without."""
    return login.removesuffix("[bot]") if kind == "Bot" else None


def _can_change_eligibility(signal: dict[str, object]) -> bool:
    """Whether a wake can change which issue is eligible. One with no delivery can."""
    body = signal.get("body")
    if not isinstance(body, dict):
        return True
    if "comment" in body or "review" in body or "check_suite" in body:
        return False
    if body.get("action") in {"labeled", "unlabeled"}:
        return _names_ready_label(body)
    pull_request = body.get("pull_request")
    if not isinstance(pull_request, dict):
        return True
    head = pull_request.get("head")
    return isinstance(head, dict) and str(head.get("ref")).startswith(AGENT_BRANCH_PREFIX)


def _labeled_issue(signal: dict[str, object]) -> int | None:
    """The issue a delivery says was just labeled ready-for-agent, if it says so."""
    body = signal.get("body")
    if (
        not isinstance(body, dict)
        or body.get("action") != "labeled"
        or not _names_ready_label(body)
    ):
        return None
    issue = body.get("issue")
    if not isinstance(issue, dict) or "pull_request" in issue:
        return None
    number = issue.get("number")
    return number if isinstance(number, int) else None


def _names_ready_label(body: dict) -> bool:
    label = body.get("label")
    return isinstance(label, dict) and label.get("name") == READY_LABEL


def _eligible_issues(workspace: Path, labeled: int | None) -> tuple[list[int], int | None]:
    """The issues still labeled ready-for-human that may be handed over, agent pull request or
    not, and the oldest other eligible issue, among those labeled ready-for-agent and *labeled*.

    GitHub's issue list can lag a label, a close or a merge, so an issue is read again on its own
    before it is chosen. Past the oldest, only the issues the list shows ready-for-human are read.
    """
    issues = _ready_issues(workspace)
    confirmed: set[int] = set()
    if labeled is not None and all(issue.number != labeled for issue in issues):
        fetched = _ready_issue(workspace, labeled)
        if fetched is not None:
            issues = sorted([*issues, fetched], key=lambda issue: issue.number)
            confirmed.add(labeled)
    covered = {pull.closes for pull in _agent_pull_requests(workspace)}
    relabeled: list[int] = []
    oldest: int | None = None
    for issue in issues:
        if READY_FOR_HUMAN_LABEL not in issue.labels and (
            issue.number in covered or oldest is not None
        ):
            continue
        stack = issue.parent or issue.number
        if not all(
            blocker["number"] in covered and (_parent(blocker) or blocker["number"]) == stack
            for blocker in _open_blockers(workspace, issue.number)
        ):
            continue
        found = issue if issue.number in confirmed else _ready_issue(workspace, issue.number)
        if found is None:
            continue
        if READY_FOR_HUMAN_LABEL in found.labels:
            relabeled.append(found.number)
        elif oldest is None and found.number not in covered:
            oldest = found.number
    return relabeled, oldest


def _open_blockers(workspace: Path, number: int) -> list[dict]:
    """The open issues that block the issue."""
    blocking = _pages(
        workspace, f"repos/{{owner}}/{{repo}}/issues/{number}/dependencies/blocked_by"
    )
    return [blocker for blocker in blocking if blocker["state"] == "open"]


def _blocked_behind(workspace: Path, number: int) -> list[dict]:
    """The open issues the issue blocks, directly or through other open issues, nearest first."""
    found: dict[int, dict] = {}
    waiting = [number]
    while waiting:
        endpoint = f"repos/{{owner}}/{{repo}}/issues/{waiting.pop(0)}/dependencies/blocking"
        for blocked in _pages(workspace, endpoint):
            if blocked["state"] == "open" and blocked["number"] not in {number, *found}:
                found[blocked["number"]] = blocked
                waiting.append(blocked["number"])
    return list(found.values())


def _run_over(workspace: Path, branch: str) -> bool:
    """Whether *branch* is an agent branch whose issue is closed and that has no open pull
    request. A closed issue ends only a run that needs human, so one babysitting its pull request
    goes on."""
    named = _AGENT_BRANCH.match(branch)
    if named is None:
        return False
    found = _api(workspace, f"repos/{{owner}}/{{repo}}/issues/{named[1]}")
    if found["state"] != "closed":
        return False
    return all(pull.branch != branch for pull in _agent_pull_requests(workspace))


def _ready_issue(workspace: Path, number: int) -> Issue | None:
    """The issue, while it is open and labeled ready-for-agent."""
    found = _api(workspace, f"repos/{{owner}}/{{repo}}/issues/{number}")
    labels = {label["name"] for label in found["labels"]}
    if found["state"] != "open" or READY_LABEL not in labels:
        return None
    return _parsed_issue(found)


def _issue(workspace: Path, number: int) -> Issue:
    found = _api(workspace, f"repos/{{owner}}/{{repo}}/issues/{number}")
    issue = _parsed_issue(found)
    if issue is None:
        raise ValueError(f"#{number} is a pull request, not an issue.")
    return issue


def _parsed_issue(value: dict) -> Issue | None:
    """The issue GitHub answered with, or None for a pull request, which its issues list too."""
    if "pull_request" in value:
        return None
    return Issue(
        number=value["number"],
        title=value["title"],
        parent=_parent(value),
        untrusted_author=_untrusted_author(value),
        labels=frozenset(label["name"] for label in value["labels"]),
    )


def _parent(value: dict) -> int | None:
    """The number of the issue's parent issue, if it has one."""
    url = value.get("parent_issue_url")
    if url is None:
        return None
    number = _ISSUE_URL_NUMBER.search(url)
    if number is None:
        raise ValueError(f"GitHub names {url} as the parent issue, which is not an issue's URL.")
    return int(number[1])


def _untrusted_author(written: dict) -> str | None:
    """Who wrote an issue or a comment, when the repository does not trust them."""
    user = written["user"]
    app = _bot_app(user["login"], user["type"])
    if written["author_association"] in _TRUSTED or app in _TICKET_APPS:
        return None
    return user["login"]


def _untrusted_authors(workspace: Path, issue: Issue) -> list[str]:
    """Who wrote the ticket text the coding client reads, the issue and its parent with their
    comments, without being the repository's owner, a member, a collaborator or a ticket app."""
    issues = [issue] if issue.parent is None else [issue, _issue(workspace, issue.parent)]
    authors = {found.untrusted_author for found in issues}
    for found in issues:
        comments = _pages(workspace, f"repos/{{owner}}/{{repo}}/issues/{found.number}/comments")
        authors.update(_untrusted_author(comment) for comment in comments)
    return sorted(author for author in authors if author is not None)


def _ready_issues(workspace: Path) -> list[Issue]:
    """The open issues labeled ready-for-agent, oldest first."""
    listed = _pages(workspace, "repos/{owner}/{repo}/issues", "state=open", f"labels={READY_LABEL}")
    issues = (_parsed_issue(value) for value in listed)
    return sorted((issue for issue in issues if issue is not None), key=lambda issue: issue.number)


def _agent_pull_requests(workspace: Path) -> list[AgentPullRequest]:
    """The open agent pull requests, newest first.

    A fork's pull request is none, whatever its branch is called: anyone can open one.
    """
    listed = _pages(
        workspace, "repos/{owner}/{repo}/pulls", "state=open", "sort=created", "direction=desc"
    )
    return [
        AgentPullRequest(
            number=value["number"],
            branch=value["head"]["ref"],
            closes=int(closes[1])
            if (closes := _CLOSES_ISSUE.search(value["body"] or ""))
            else None,
            stack=value["stack"]["number"] if value.get("stack") else None,
            requested=frozenset(user["login"] for user in value["requested_reviewers"]),
        )
        for value in listed
        if value["head"]["ref"].startswith(AGENT_BRANCH_PREFIX)
        and (value["head"]["repo"] or {}).get("full_name") == value["base"]["repo"]["full_name"]
    ]


def _sibling_pull_requests(workspace: Path, issue: Issue) -> list[AgentPullRequest]:
    """The agent pull requests of the sub-issue's siblings, oldest first. None for a top-level
    issue."""
    if issue.parent is None:
        return []
    siblings = {found.number for found in _ready_issues(workspace) if found.parent == issue.parent}
    newest_first = [
        pull for pull in _agent_pull_requests(workspace) if pull.closes in siblings - {issue.number}
    ]
    return newest_first[::-1]


def _base(workspace: Path, issue: Issue) -> str:
    """The branch the issue's agent branch starts from, after a fetch: the one agent branch of
    its open blockers, or with several, theirs or a sibling's that holds them all. With no blocker
    open, the default branch.

    Blockers whose branches no agent branch holds together need a human to pick the base.
    """
    blocking = {blocker["number"] for blocker in _open_blockers(workspace, issue.number)}
    pulls = _agent_pull_requests(workspace) if blocking else []
    branches = [pull.branch for pull in pulls if pull.closes in blocking]
    if not branches:
        return _repository(workspace).default_branch
    if len(branches) == 1:
        return branches[0]
    holding = set.intersection(*(_holding(workspace, branch) for branch in branches))
    siblings = [pull.branch for pull in _sibling_pull_requests(workspace, issue)]
    base = next((branch for branch in [*branches, *siblings] if branch in holding), None)
    if base is None:
        raise DivergedBlockers(
            "No agent branch holds every open blocker's agent branch, so a human picks the base: "
            f"{', '.join(sorted(branches))}."
        )
    return base


def _holding(workspace: Path, branch: str) -> set[str]:
    """The branches on origin that hold the branch."""
    held = _git(
        workspace,
        "branch",
        "--remotes",
        "--format=%(refname:lstrip=3)",
        "--contains",
        f"origin/{branch}",
    )
    return set(held.split())


def _stack(workspace: Path, under: AgentPullRequest, number: int) -> None:
    """Add the pull request to the stack of the one *under* it, or stack the two.

    The pull request is open either way, its base the branch of the one under it, so a stack
    GitHub refuses fails nothing.
    """
    if under.stack is None:
        endpoint, pulls = "repos/{owner}/{repo}/stacks", [under.number]
    else:
        endpoint, pulls = f"repos/{{owner}}/{{repo}}/stacks/{under.stack}/add", []
    arguments = ["api", "--method", "POST", "-H", f"X-GitHub-Api-Version: {GITHUB_API_VERSION}"]
    arguments.append(endpoint)
    for pull in (*pulls, number):
        arguments += ["-F", f"pull_requests[]={pull}"]
    with suppress(CommandFailed):
        _gh(workspace, *arguments)


def _repository(workspace: Path) -> Repository:
    found = json.loads(
        _gh(workspace, "repo", "view", "--json", "owner,isInOrganization,defaultBranchRef")
    )
    return Repository(
        default_branch=found["defaultBranchRef"]["name"],
        maintainer=None if found["isInOrganization"] else found["owner"]["login"],
    )


def _has_merge_ready_label(workspace: Path) -> bool:
    labels = _pages(workspace, "repos/{owner}/{repo}/labels")
    return any(label["name"] == MERGE_READY_LABEL for label in labels)


def _exclude_scratch(workspace: Path) -> None:
    """Keep scratch files out of every commit of the workspace's clone, its run worktrees'
    included."""
    exclude = workspace / ".git" / "info" / "exclude"
    excluded = exclude.read_text(encoding="utf-8").splitlines() if exclude.exists() else []
    if f"{SCRATCH}/" not in excluded:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text("\n".join([*excluded, f"{SCRATCH}/", ""]), encoding="utf-8")


def _api(workspace: Path, endpoint: str) -> dict:
    return json.loads(
        _gh(
            workspace,
            "api",
            "--method",
            "GET",
            "-H",
            f"X-GitHub-Api-Version: {GITHUB_API_VERSION}",
            endpoint,
        )
    )


def _pages(workspace: Path, endpoint: str, *fields: str, key: str | None = None) -> list[dict]:
    """Every item of a listing, across its pages. *key* names the list in a page that is an
    object."""
    arguments = ["api", "--method", "GET", "-H", f"X-GitHub-Api-Version: {GITHUB_API_VERSION}"]
    arguments += ["--paginate", "--slurp", endpoint, "-f", "per_page=100"]
    for field in fields:
        arguments += ["-f", field]
    pages = json.loads(_gh(workspace, *arguments))
    return [item for page in pages for item in (page if key is None else page[key])]


def _gh(workspace: Path, *arguments: str) -> str:
    return _run(workspace, "gh", *arguments)


def _git(workspace: Path, *arguments: str) -> str:
    return _run(workspace, "git", *arguments)


def _run(workspace: Path, *command: str) -> str:
    """What the command printed. One that exits with another code than zero raises."""
    ran = subprocess.run(
        command,
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=_COMMAND_TIMEOUT,
        check=False,
    )
    if ran.returncode != 0:
        said = ran.stderr.strip() or ran.stdout.strip() or "no output"
        raise CommandFailed(f"{' '.join(command[:2])} exited with code {ran.returncode}: {said}")
    return ran.stdout
