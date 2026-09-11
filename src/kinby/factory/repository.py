"""Use the GitHub CLI as the delegated pipeline's repository boundary."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import NewType

from kinby.factory.process import run_command

GITHUB_TIMEOUT_SECONDS = 60.0
READY_LABEL = "ready-for-agent"
AGENT_BRANCH_PREFIX = "agent/"
_CLOSES_ISSUE = re.compile(r"(?im)^Closes #(\d+)\s*$")
IssueTitle = NewType("IssueTitle", str)
IssueUrl = NewType("IssueUrl", str)
IssueNumber = NewType("IssueNumber", int)
BranchName = NewType("BranchName", str)
PullRequestUrl = NewType("PullRequestUrl", str)
PullRequestNumber = NewType("PullRequestNumber", int)
GitHubLogin = NewType("GitHubLogin", str)


class RepositoryResponseError(ValueError):
    """A GitHub CLI response does not match the requested shape."""


@dataclass(frozen=True)
class Issue:
    """An open issue ready for the coding client."""

    number: IssueNumber
    title: IssueTitle
    url: IssueUrl


@dataclass(frozen=True)
class AgentPullRequest:
    """An open pull request created by the delegated pipeline."""

    number: PullRequestNumber
    url: PullRequestUrl
    branch: BranchName
    body: str

    @property
    def closed_issue(self) -> IssueNumber | None:
        return closed_issue_number(self.body)


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
        result = self._gh(
            "issue",
            "list",
            "--state",
            "open",
            "--label",
            READY_LABEL,
            "--limit",
            "100",
            "--json",
            "number,title,url",
        )
        return tuple(sorted(_issues(result), key=lambda issue: issue.number))

    def agent_pull_requests(self) -> tuple[AgentPullRequest, ...]:
        result = self._gh(
            "pr",
            "list",
            "--state",
            "open",
            "--limit",
            "100",
            "--json",
            "number,url,headRefName,body",
        )
        return tuple(
            pull_request
            for pull_request in _pull_requests(result)
            if pull_request.branch.startswith(AGENT_BRANCH_PREFIX)
        )

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
    ) -> PullRequestUrl:
        return PullRequestUrl(
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


def closed_issue_number(body: str) -> IssueNumber | None:
    """Return the issue closed by a pull request body."""
    match = _CLOSES_ISSUE.search(body)
    return IssueNumber(int(match.group(1))) if match is not None else None


def _issues(source: str) -> list[Issue]:
    values = json.loads(source)
    if not isinstance(values, list):
        raise RepositoryResponseError("gh issue list returned a non-list JSON value")
    issues: list[Issue] = []
    for value in values:
        if not isinstance(value, dict):
            raise RepositoryResponseError("gh issue list returned a non-object issue")
        number, title, url = value.get("number"), value.get("title"), value.get("url")
        if not isinstance(number, int) or not isinstance(title, str) or not isinstance(url, str):
            raise RepositoryResponseError("gh issue list returned an invalid issue")
        issues.append(Issue(IssueNumber(number), IssueTitle(title), IssueUrl(url)))
    return issues


def _pull_requests(source: str) -> list[AgentPullRequest]:
    values = json.loads(source)
    if not isinstance(values, list):
        raise RepositoryResponseError("gh pr list returned a non-list JSON value")
    pull_requests: list[AgentPullRequest] = []
    for value in values:
        if not isinstance(value, dict):
            raise RepositoryResponseError("gh pr list returned a non-object pull request")
        number = value.get("number")
        url = value.get("url")
        branch = value.get("headRefName")
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
                PullRequestNumber(number), PullRequestUrl(url), BranchName(branch), body
            )
        )
    return pull_requests


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
