"""Prepare, check, push, and open an agent pull request."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kinby.factory.clients import PR_BODY
from kinby.factory.process import CommandError, run_command
from kinby.factory.repository import (
    AGENT_BRANCH_PREFIX,
    BranchName,
    GitHubRepository,
    Issue,
    PullRequestUrl,
    RepositoryMetadata,
    closed_issue_number,
)

CHECKS = (
    ("uv", "run", "ruff", "check", "."),
    ("uv", "run", "ruff", "format", "--check", "."),
    ("uv", "run", "ty", "check"),
    ("uv", "run", "pytest"),
)
CHECK_TIMEOUT_SECONDS = 900.0
GIT_TIMEOUT_SECONDS = 900.0


@dataclass(frozen=True)
class ChecksPassed:
    passed: Literal[True] = True
    failed: None = None


@dataclass(frozen=True)
class ChecksFailed:
    failed: str
    passed: Literal[False] = False


class RepositoryCheckFailed(RuntimeError):
    """One required repository check failed."""

    def __init__(self, command: tuple[str, ...], reason: str) -> None:
        self.command = command
        super().__init__(reason)


class PullRequestBodyError(RuntimeError):
    """The pipeline could not read or update the pull request body."""


def branch_name(issue: Issue) -> BranchName:
    """Return the stable agent branch for an issue."""
    words = re.findall(r"[a-z0-9]+", issue.title.lower())
    slug = "-".join(words) or "issue"
    return BranchName(f"{AGENT_BRANCH_PREFIX}{issue.number}-{slug}")


def prepare_branch(workspace: Path, branch: BranchName, base_branch: BranchName) -> None:
    """Create an agent branch from the latest remote base."""
    _git(workspace, "fetch", "origin", base_branch)
    _git(workspace, "switch", "-c", branch, f"origin/{base_branch}")


def run_checks(workspace: Path) -> ChecksPassed:
    """Run all repository checks in their required order."""
    for command in CHECKS:
        try:
            run_command(command, cwd=workspace, timeout_seconds=CHECK_TIMEOUT_SECONDS)
        except CommandError as exc:
            raise RepositoryCheckFailed(command, str(exc)) from exc
    return ChecksPassed()


def open_pull_request(
    repository: GitHubRepository,
    workspace: Path,
    issue: Issue,
    metadata: RepositoryMetadata,
    branch: BranchName,
) -> PullRequestUrl:
    """Push the checked branch and open its pull request."""
    _git(workspace, "push", "-u", "origin", branch)
    body_file = workspace / PR_BODY
    try:
        body = body_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise PullRequestBodyError(f"could not read pull request body: {exc}") from exc
    if closed_issue_number(body.partition("\n")[0]) == issue.number:
        body = body.partition("\n")[2].lstrip()
    try:
        body_file.write_text(f"Closes #{issue.number}\n\n{body}\n", encoding="utf-8")
    except OSError as exc:
        raise PullRequestBodyError(f"could not update pull request body: {exc}") from exc
    return repository.open_pull_request(
        branch=branch,
        base_branch=metadata.default_branch,
        title=issue.title,
        body_file=body_file,
        reviewer=metadata.maintainer,
    )


def _git(workspace: Path, *arguments: str) -> None:
    run_command(
        ("git", *arguments),
        cwd=workspace,
        timeout_seconds=GIT_TIMEOUT_SECONDS,
    )
