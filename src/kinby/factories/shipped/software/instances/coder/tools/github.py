"""The software factory's work on GitHub: the scan, the agent branch, the pull request, and
babysitting it through review.

Every command runs in the workspace, the repository's clone. gh runs as the GH_TOKEN the
instance holds.
"""

import asyncio
import json
import re
import shutil
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from kinby.plugins.hooks import HookResult
from kinby.plugins.intake import hand_over
from kinby.plugins.tools import ToolContext, tool

READY_LABEL = "ready-for-agent"
READY_FOR_HUMAN_LABEL = "ready-for-human"
MERGE_READY_LABEL = "merge-ready"
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
_CLOSES_ISSUE = re.compile(r"(?im)^Closes #(\d+)\s*$")
_ISSUE_URL_NUMBER = re.compile(r"/issues/(\d+)$")


class CommandFailed(RuntimeError):
    """A gh or git command exited with a code other than zero."""


class MissingPullRequestBody(RuntimeError):
    """The coding client wrote no body for the pull request."""


class UntrustedTicket(RuntimeError):
    """Someone other than the repository's owner, a member or a collaborator wrote ticket text."""


class UntrustedFeedback(RuntimeError):
    """Review feedback left to answer has an author babysitting does not trust."""


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    parent: int | None
    #: Its author, when not the repository's owner, a member or a collaborator.
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


@tool(write=True)
async def scan_ready_issues(signal: dict[str, object], context: ToolContext) -> None:
    """Hand the oldest eligible issue to the factory, after a wake that can change which it is.

    An eligible issue is open and labeled ready-for-agent, has no agent pull request, and each
    open issue that blocks it has one in the same stack. The oldest stays eligible until its pull
    request opens or it goes to a human, and handing it again returns its run, so the factory
    implements one issue at a time. Runs babysitting their pull requests go on meanwhile.
    """
    if context.routine is None:
        raise ValueError("Only the factory's intake routine scans for its work.")
    if not _can_change_eligibility(signal):
        return
    eligible = await asyncio.to_thread(
        _oldest_eligible_issue, context.workspace, _labeled_issue(signal)
    )
    if eligible is not None:
        await hand_over(context.routine, {"issue": eligible})


@tool(write=True)
def prepare_branch(issue: int, context: ToolContext) -> dict[str, str]:
    """Check out a fresh agent branch for the issue, once a trusted author wrote all its text.

    A sub-issue's branch starts from its newest sibling's agent branch, so its pull request
    stacks on that one. Any other starts from the default branch. Whatever an earlier run left
    uncommitted in the workspace goes.
    """
    workspace = context.workspace
    found = _issue(workspace, issue)
    untrusted = _untrusted_authors(workspace, found)
    if untrusted:
        raise UntrustedTicket(
            f"Ticket text from untrusted authors needs a human: {', '.join(untrusted)}."
        )
    siblings = _sibling_pull_requests(workspace, found)
    base = siblings[-1].branch if siblings else _repository(workspace).default_branch
    words = re.findall(r"[a-z0-9]+", found.title.lower())
    branch = f"{AGENT_BRANCH_PREFIX}{issue}-{'-'.join(words) or 'issue'}"
    _clean(workspace)
    _git(workspace, "fetch", "--prune", "origin")
    _git(workspace, "switch", "--discard-changes", "-C", branch, f"origin/{base}")
    return {"branch": branch, "base": base}


@tool(write=True)
def publish_pull_request(
    issue: int,
    branch: str,
    base: str,
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
    """
    workspace = context.workspace
    if pr is None:
        return _open_pull_request(workspace, issue, branch, base, title, body, reviewed=reviewed)
    answered = json.loads(replies or "{}")
    return _push_fix_round(workspace, issue, branch, pr, answered, title, body, reviewed=reviewed)


@tool(write=True)
def assess_pull_request(pr: int, context: ToolContext, *, quiet: bool = False) -> HookResult:
    """Read the pull request after a wake, and say what babysitting does next.

    Merged, the run is done; closed without a merge, it stops. Merge-ready, once a trusted
    reviewer reviewed its head with no thread left to answer and no check still running: it gets
    the merge-ready label and a review request for the maintainer. A trusted review app's
    successful check run on the head is a review of it. After a *quiet* wait, one no reviewer
    reviewed is merge-ready too, and a comment says so. Threads only trusted authors wrote are
    feedback for a fix round. Another author's thread needs a human. Anything else waits for the
    next wake. Nothing here ever merges.
    """
    workspace = context.workspace
    pull = _api(workspace, f"repos/{{owner}}/{{repo}}/pulls/{pr}")
    if pull["state"] == "closed":
        if pull["merged"]:
            return HookResult(values={"merge_ready": True}, outcome="merged")
        return HookResult(outcome="closed")
    head = pull["head"]["sha"]
    checks = _pages(
        workspace, f"repos/{{owner}}/{{repo}}/commits/{head}/check-runs", key="check_runs"
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
    reviews = _pages(workspace, f"repos/{{owner}}/{{repo}}/pulls/{pr}/reviews")
    reviewed = any(
        check["conclusion"] == "success" and (check["app"] or {}).get("slug") in _TRUSTED_APPS
        for check in checks
    ) or any(
        review["commit_id"] == head
        and (user := review["user"] or {"login": "ghost", "type": "User"})["login"] != coder
        and _trusted(review["author_association"], _bot_app(user["login"], user["type"]))
        for review in reviews
    )
    if not reviewed:
        if not quiet:
            return HookResult(outcome="waiting")
        _gh(workspace, "pr", "comment", str(pr), "--body", NO_REVIEW)
    maintainer = _repository(workspace).maintainer
    asked = () if maintainer in {None, pull["user"]["login"]} else ("--add-reviewer", maintainer)
    _gh(workspace, "pr", "edit", str(pr), "--add-label", MERGE_READY_LABEL, *asked)
    return HookResult(values={"merge_ready": True}, outcome="merge-ready")


@tool(write=True)
def report_needs_human(issue: int, step: str, summary: str, context: ToolContext) -> None:
    """Hand the issue to a human: label it ready-for-human and comment why the run stopped.

    The agent branch stays as it is, with everything committed on it.
    """
    workspace = context.workspace
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
    body = f"The software factory stopped at step `{step}` and needs a human.\n\n{summary}"
    _gh(workspace, "issue", "comment", str(issue), "--body", body)


def _open_pull_request(
    workspace: Path, issue: int, branch: str, base: str, title: str, body: str, *, reviewed: bool
) -> dict[str, int]:
    """Push the branch and open its pull request, which closes the issue, with the title and body
    the coding client wrote. With no title written, it takes the issue's.

    A sub-issue's pull request joins the stack of its siblings' pull requests. The repository's
    maintainer reviews it. An issue an earlier failure handed to a human loses its
    ready-for-human label.
    """
    if not body:
        raise MissingPullRequestBody("The coding session wrote no pull request body.")
    found = _issue(workspace, issue)
    siblings = _sibling_pull_requests(workspace, found)
    maintainer = _repository(workspace).maintainer
    _git(workspace, "push", "--force-with-lease", "-u", "origin", branch)
    url = _gh(
        workspace,
        "pr",
        "create",
        "--head",
        branch,
        "--base",
        base,
        "--title",
        title or found.title,
        "--body",
        _body(issue, body, reviewed=reviewed),
        *(() if maintainer is None else ("--reviewer", maintainer)),
    )
    number = int(url.strip().rsplit("/", 1)[1])
    if siblings:
        _stack(workspace, siblings, number)
    if READY_FOR_HUMAN_LABEL in found.labels:
        _gh(workspace, "issue", "edit", str(issue), "--remove-label", READY_FOR_HUMAN_LABEL)
    return {"pr": number}


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
    commit = _git(workspace, "rev-parse", branch).strip()
    for thread, reply in replies.items():
        body = f"{commit}: {reply['reply']}" if reply["fixed"] else reply["reply"]
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


def _oldest_eligible_issue(workspace: Path, labeled: int | None) -> int | None:
    """The oldest eligible issue, among those labeled ready-for-agent and *labeled*.

    GitHub's issue list can lag a label, a close or a merge, so the issue is read again on its
    own before it is chosen.
    """
    issues = _ready_issues(workspace)
    confirmed: set[int] = set()
    if labeled is not None and all(issue.number != labeled for issue in issues):
        fetched = _ready_issue(workspace, labeled)
        if fetched is not None:
            issues = sorted([*issues, fetched], key=lambda issue: issue.number)
            confirmed.add(labeled)
    covered = {pull.closes for pull in _agent_pull_requests(workspace)}
    for issue in issues:
        if issue.number in covered:
            continue
        blocking = _pages(
            workspace, f"repos/{{owner}}/{{repo}}/issues/{issue.number}/dependencies/blocked_by"
        )
        stack = issue.parent or issue.number
        if not all(
            blocker["number"] in covered and (_parent(blocker) or blocker["number"]) == stack
            for blocker in blocking
            if blocker["state"] == "open"
        ):
            continue
        if issue.number in confirmed or _ready_issue(workspace, issue.number) is not None:
            return issue.number
    return None


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
    return None if written["author_association"] in _TRUSTED else written["user"]["login"]


def _untrusted_authors(workspace: Path, issue: Issue) -> list[str]:
    """Who wrote the ticket text the coding client reads, the issue and its parent with their
    comments, without being the repository's owner, a member or a collaborator."""
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


def _stack(workspace: Path, siblings: list[AgentPullRequest], number: int) -> None:
    """Add the pull request to the stack of its siblings', or stack it on top of them all.

    The pull request is open either way, its base the newest sibling's branch, so a stack GitHub
    refuses fails nothing.
    """
    newest = siblings[-1]
    if newest.stack is None:
        endpoint, pulls = "repos/{owner}/{repo}/stacks", [pull.number for pull in siblings]
    else:
        endpoint, pulls = f"repos/{{owner}}/{{repo}}/stacks/{newest.stack}/add", []
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


def _clean(workspace: Path) -> None:
    """Drop whatever the workspace holds beyond its commits, the scratch files among it, and
    keep scratch files out of every commit from now on."""
    exclude = workspace / ".git" / "info" / "exclude"
    excluded = exclude.read_text(encoding="utf-8").splitlines() if exclude.exists() else []
    if f"{SCRATCH}/" not in excluded:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text("\n".join([*excluded, f"{SCRATCH}/", ""]), encoding="utf-8")
    _git(workspace, "reset", "--hard")
    _git(workspace, "clean", "-fd")
    shutil.rmtree(workspace / SCRATCH, ignore_errors=True)


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
