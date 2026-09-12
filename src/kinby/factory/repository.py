"""Use the GitHub CLI as the delegated pipeline's repository boundary."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import NewType

from kinby.factory.process import run_command

GITHUB_TIMEOUT_SECONDS = 60.0
GITHUB_API_VERSION = "2026-03-10"
READY_LABEL = "ready-for-agent"
AGENT_BRANCH_PREFIX = "agent/"
_CLOSES_ISSUE = re.compile(r"(?im)^Closes #(\d+)\s*$")
_ISSUE_URL_NUMBER = re.compile(r"/issues/(\d+)$")
_PULL_REQUEST_URL_NUMBER = re.compile(r"/pull/(\d+)$")
IssueTitle = NewType("IssueTitle", str)
IssueUrl = NewType("IssueUrl", str)
IssueNumber = NewType("IssueNumber", int)
BranchName = NewType("BranchName", str)
PullRequestUrl = NewType("PullRequestUrl", str)
PullRequestNumber = NewType("PullRequestNumber", int)
StackNumber = NewType("StackNumber", int)
GitHubLogin = NewType("GitHubLogin", str)


class RepositoryResponseError(ValueError):
    """A GitHub CLI response does not match the requested shape."""


@dataclass(frozen=True)
class Issue:
    """An open issue ready for the coding client."""

    number: IssueNumber
    title: IssueTitle
    url: IssueUrl
    parent: IssueNumber | None = None


@dataclass(frozen=True)
class OpenBlocker:
    """An open issue that blocks another issue."""

    number: IssueNumber
    parent: IssueNumber | None


@dataclass(frozen=True)
class AgentPullRequest:
    """An open pull request created by the delegated pipeline."""

    number: PullRequestNumber
    url: PullRequestUrl
    branch: BranchName
    body: str
    stack: StackNumber | None

    @property
    def closed_issue(self) -> IssueNumber | None:
        return closed_issue_number(self.body)


@dataclass(frozen=True)
class OpenedPullRequest:
    """A pull request created by the delegated pipeline."""

    number: PullRequestNumber
    url: PullRequestUrl


@dataclass(frozen=True)
class RepositoryMetadata:
    """GitHub values needed to open an agent pull request."""

    maintainer: GitHubLogin
    default_branch: BranchName


class GitHubRepository:
    """The GitHub operations owned by one pipeline run."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    def ready_issues(self) -> tuple[Issue, ...]:
        result = self._paginated_api(
            "repos/{owner}/{repo}/issues",
            "state=open",
            f"labels={READY_LABEL}",
        )
        return tuple(sorted(_issues(result), key=lambda issue: issue.number))

    def ready_issue(self, issue: IssueNumber) -> Issue | None:
        """Return an issue when it is currently open and ready for an agent."""
        result = self._gh(
            "api",
            "--method",
            "GET",
            "-H",
            f"X-GitHub-Api-Version: {GITHUB_API_VERSION}",
            f"repos/{{owner}}/{{repo}}/issues/{issue}",
        )
        return _ready_issue(result)

    def agent_pull_requests(self) -> tuple[AgentPullRequest, ...]:
        result = self._paginated_api(
            "repos/{owner}/{repo}/pulls",
            "state=open",
            "sort=created",
            "direction=desc",
        )
        return tuple(
            pull_request
            for pull_request in _pull_requests(result)
            if pull_request.branch.startswith(AGENT_BRANCH_PREFIX)
        )

    def open_blockers(self, issue: IssueNumber) -> tuple[OpenBlocker, ...]:
        """Return every open issue that blocks an issue."""
        result = self._paginated_api(
            f"repos/{{owner}}/{{repo}}/issues/{issue}/dependencies/blocked_by"
        )
        return tuple(_open_blockers(result))

    def issue_body(self, issue: IssueNumber) -> str:
        """Return the source Markdown for an issue."""
        source = self._gh(
            "api",
            "--method",
            "GET",
            "-H",
            f"X-GitHub-Api-Version: {GITHUB_API_VERSION}",
            f"repos/{{owner}}/{{repo}}/issues/{issue}",
            "--jq",
            '.body // ""',
        )
        return source.rstrip("\n")

    def metadata(self) -> RepositoryMetadata:
        return _metadata(self._gh("repo", "view", "--json", "owner,defaultBranchRef"))

    def open_pull_request(
        self,
        *,
        branch: BranchName,
        base_branch: BranchName,
        title: IssueTitle,
        body_file: Path,
        reviewer: GitHubLogin,
    ) -> OpenedPullRequest:
        url = PullRequestUrl(
            self._gh(
                "pr",
                "create",
                "--head",
                branch,
                "--base",
                base_branch,
                "--title",
                title,
                "--body-file",
                str(body_file),
                "--reviewer",
                reviewer,
            ).strip()
        )
        match = _PULL_REQUEST_URL_NUMBER.search(url)
        if match is None:
            raise RepositoryResponseError("gh pr create returned an invalid pull request URL")
        return OpenedPullRequest(PullRequestNumber(int(match.group(1))), url)

    def create_stack(self, pull_requests: tuple[PullRequestNumber, ...]) -> None:
        """Create a stack from pull requests ordered bottom to top."""
        arguments = [
            "api",
            "--method",
            "POST",
            "-H",
            f"X-GitHub-Api-Version: {GITHUB_API_VERSION}",
            "repos/{owner}/{repo}/stacks",
        ]
        for pull_request in pull_requests:
            arguments.extend(("-F", f"pull_requests[]={pull_request}"))
        self._gh(*arguments)

    def extend_stack(
        self,
        stack: StackNumber,
        pull_request: PullRequestNumber,
    ) -> None:
        """Append one pull request to the top of an existing stack."""
        self._gh(
            "api",
            "--method",
            "POST",
            "-H",
            f"X-GitHub-Api-Version: {GITHUB_API_VERSION}",
            f"repos/{{owner}}/{{repo}}/stacks/{stack}/add",
            "-F",
            f"pull_requests[]={pull_request}",
        )

    def mark_ready_for_human(self, issue: IssueNumber) -> None:
        self._gh(
            "issue",
            "edit",
            str(issue),
            "--remove-label",
            READY_LABEL,
            "--add-label",
            "ready-for-human",
        )

    def _gh(self, *arguments: str) -> str:
        return run_command(
            ("gh", *arguments),
            cwd=self._workspace,
            timeout_seconds=GITHUB_TIMEOUT_SECONDS,
        ).stdout

    def _paginated_api(self, endpoint: str, *fields: str) -> str:
        arguments = [
            "api",
            "--method",
            "GET",
            "-H",
            f"X-GitHub-Api-Version: {GITHUB_API_VERSION}",
            "--paginate",
            "--slurp",
            endpoint,
            "-f",
            "per_page=100",
        ]
        for field in fields:
            arguments.extend(("-f", field))
        return self._gh(*arguments)


def closed_issue_number(body: str) -> IssueNumber | None:
    """Return the issue closed by a pull request body."""
    match = _CLOSES_ISSUE.search(body)
    return IssueNumber(int(match.group(1))) if match is not None else None


def _issues(source: str) -> list[Issue]:
    values = _page_items(source, "issue list")
    issues: list[Issue] = []
    for value in values:
        if (issue := _issue(value)) is not None:
            issues.append(issue)
    return issues


def _ready_issue(source: str) -> Issue | None:
    value = json.loads(source)
    if not isinstance(value, dict):
        raise RepositoryResponseError("gh issue get returned a non-object issue")
    state = value.get("state")
    labels = value.get("labels")
    if (
        not isinstance(state, str)
        or not isinstance(labels, list)
        or not all(
            isinstance(label, dict) and isinstance(label.get("name"), str) for label in labels
        )
    ):
        raise RepositoryResponseError("gh issue get returned an invalid issue")
    if state != "open" or not any(label["name"] == READY_LABEL for label in labels):
        return None
    return _issue(value)


def _issue(value: object) -> Issue | None:
    if not isinstance(value, dict):
        raise RepositoryResponseError("gh returned a non-object issue")
    if "pull_request" in value:
        return None
    number = value.get("number")
    title = value.get("title")
    url = value.get("html_url", value.get("url"))
    if not isinstance(number, int) or not isinstance(title, str) or not isinstance(url, str):
        raise RepositoryResponseError("gh returned an invalid issue")
    return Issue(
        IssueNumber(number),
        IssueTitle(title),
        IssueUrl(url),
        _parent_number(value.get("parent_issue_url")),
    )


def _pull_requests(source: str) -> list[AgentPullRequest]:
    values = _page_items(source, "pull request list")
    pull_requests: list[AgentPullRequest] = []
    for value in values:
        if not isinstance(value, dict):
            raise RepositoryResponseError("gh pr list returned a non-object pull request")
        number = value.get("number")
        url = value.get("html_url", value.get("url"))
        head = value.get("head")
        branch = value.get("headRefName")
        if isinstance(head, dict):
            branch = head.get("ref")
        body = value.get("body")
        if (
            not isinstance(number, int)
            or not isinstance(url, str)
            or not isinstance(branch, str)
            or not isinstance(body, str)
        ):
            raise RepositoryResponseError("gh pr list returned an invalid pull request")
        pull_requests.append(
            AgentPullRequest(
                PullRequestNumber(number),
                PullRequestUrl(url),
                BranchName(branch),
                body,
                _stack_number(value.get("stack")),
            )
        )
    return pull_requests


def _open_blockers(source: str) -> list[OpenBlocker]:
    blockers: list[OpenBlocker] = []
    for value in _page_items(source, "blocked-by list"):
        if not isinstance(value, dict):
            raise RepositoryResponseError("gh blocked-by list returned a non-object issue")
        number = value.get("number")
        state = value.get("state")
        if not isinstance(number, int) or not isinstance(state, str):
            raise RepositoryResponseError("gh blocked-by list returned an invalid issue")
        if state == "open":
            blockers.append(
                OpenBlocker(
                    IssueNumber(number),
                    _parent_number(value.get("parent_issue_url")),
                )
            )
    return blockers


def _page_items(source: str, operation: str) -> list[object]:
    values = json.loads(source)
    if not isinstance(values, list):
        raise RepositoryResponseError(f"gh {operation} returned a non-list JSON value")
    if not values or not all(isinstance(page, list) for page in values):
        return values
    return [item for page in values for item in page]


def _parent_number(value: object) -> IssueNumber | None:
    if value is None:
        return None
    if not isinstance(value, str) or (match := _ISSUE_URL_NUMBER.search(value)) is None:
        raise RepositoryResponseError("gh returned an invalid parent issue URL")
    return IssueNumber(int(match.group(1)))


def _stack_number(value: object) -> StackNumber | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not isinstance(number := value.get("number"), int):
        raise RepositoryResponseError("gh returned an invalid pull request stack")
    return StackNumber(number)


def _metadata(source: str) -> RepositoryMetadata:
    value = json.loads(source)
    if not isinstance(value, dict):
        raise RepositoryResponseError("gh repo view returned a non-object JSON value")
    owner = value.get("owner")
    default_branch = value.get("defaultBranchRef")
    if not isinstance(owner, dict) or not isinstance(default_branch, dict):
        raise RepositoryResponseError("gh repo view returned invalid repository metadata")
    maintainer = owner.get("login")
    branch = default_branch.get("name")
    if not isinstance(maintainer, str) or not isinstance(branch, str):
        raise RepositoryResponseError("gh repo view returned invalid repository metadata")
    return RepositoryMetadata(GitHubLogin(maintainer), BranchName(branch))
