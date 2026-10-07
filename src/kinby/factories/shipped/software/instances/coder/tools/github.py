"""The software factory's work on GitHub: the scan, the agent branch, and the pull request.

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

from kinby.plugins.intake import hand_over
from kinby.plugins.tools import ToolContext, tool

READY_LABEL = "ready-for-agent"
READY_FOR_HUMAN_LABEL = "ready-for-human"
#: Every agent pull request's branch starts with it.
AGENT_BRANCH_PREFIX = "agent/"
#: Where the coding client writes what the factory reads, kept out of every commit.
SCRATCH = ".scratch"
PR_BODY = f"{SCRATCH}/pr-body.md"
#: What a pull request says when no adversarial review ran before it opened (ADR 0044).
REVIEW_NOT_RUN = (
    "\n\n## Review status\n\nAdversarial review was not run. Review happens on this pull request."
)
GITHUB_API_VERSION = "2026-03-10"
#: How long one gh or git command may run, in seconds.
_COMMAND_TIMEOUT = 900
#: Who may write ticket text the coding client reads (ADR 0073), by GitHub's author_association.
_TRUSTED = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
_CLOSES_ISSUE = re.compile(r"(?im)^Closes #(\d+)\s*$")
_ISSUE_URL_NUMBER = re.compile(r"/issues/(\d+)$")


class CommandFailed(RuntimeError):
    """A gh or git command exited with a code other than zero."""


class MissingPullRequestBody(RuntimeError):
    """The coding client wrote no body for the pull request."""


class UntrustedTicket(RuntimeError):
    """Someone other than the repository's owner, a member or a collaborator wrote ticket text."""


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    parent: int | None
    #: Its author, when not the repository's owner, a member or a collaborator.
    untrusted_author: str | None


@dataclass(frozen=True)
class Repository:
    default_branch: str
    #: Who reviews agent pull requests: the owner of a user's repository. GitHub cannot ask an
    #: organization for a review, so an organization's repository has none.
    maintainer: str | None


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
    carries one issue at a time.
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
def open_pull_request(
    issue: int, branch: str, base: str, context: ToolContext, *, reviewed: bool = False
) -> dict[str, int]:
    """Push the branch and open its pull request, which closes the issue, with the body the
    coding client wrote.

    The body says so when no review step found the change clean. A sub-issue's pull request
    joins the stack of its siblings' pull requests. The repository's maintainer reviews it.
    """
    workspace = context.workspace
    body_file = workspace / PR_BODY
    written = body_file.read_text(encoding="utf-8").strip() if body_file.is_file() else ""
    if not written:
        raise MissingPullRequestBody(f"The coding client wrote no pull request body to {PR_BODY}.")
    first, _, rest = written.partition("\n")
    if (closes := _CLOSES_ISSUE.fullmatch(first)) is not None and int(closes[1]) == issue:
        written = rest.strip()
    review_status = "" if reviewed else REVIEW_NOT_RUN
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
        found.title,
        "--body",
        f"Closes #{issue}\n\n{written}{review_status}",
        *(() if maintainer is None else ("--reviewer", maintainer)),
    )
    number = int(url.strip().rsplit("/", 1)[1])
    if siblings:
        _stack(workspace, siblings, number)
    return {"pr": number}


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


def _can_change_eligibility(signal: dict[str, object]) -> bool:
    """Whether a wake can change which issue is eligible. One with no delivery can."""
    body = signal.get("body")
    if not isinstance(body, dict):
        return True
    if "comment" in body:
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
    """The open agent pull requests, newest first."""
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


def _pages(workspace: Path, endpoint: str, *fields: str) -> list[dict]:
    """Every item of a listing, across its pages."""
    arguments = ["api", "--method", "GET", "-H", f"X-GitHub-Api-Version: {GITHUB_API_VERSION}"]
    arguments += ["--paginate", "--slurp", endpoint, "-f", "per_page=100"]
    for field in fields:
        arguments += ["-f", field]
    return [item for page in json.loads(_gh(workspace, *arguments)) for item in page]


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
