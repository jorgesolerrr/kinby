"""Prepare, check, push, and open an agent pull request."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kinby.factory.clients import PR_BODY, Findings
from kinby.factory.process import CommandError, CommandResult, run_command
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
    """Start or resume an agent branch from a clean persistent workspace."""
    _clean_workspace(workspace)
    _git(workspace, "fetch", "origin")
    remote_branch = f"origin/{branch}"
    remote_exists = bool(
        _git(workspace, "branch", "--remotes", "--list", remote_branch).stdout.strip()
    )
    start = remote_branch if remote_exists else f"origin/{base_branch}"
    _git(workspace, "switch", "--discard-changes", "-C", branch, start)
    _clean_workspace(workspace)


def clean_failed_branch(
    workspace: Path,
    branch: BranchName,
    base_branch: BranchName,
) -> None:
    """Remove failed-run edits and return the persistent workspace to its base."""
    _clean_workspace(workspace)
    _git(
        workspace,
        "switch",
        "--discard-changes",
        "-C",
        base_branch,
        f"origin/{base_branch}",
    )
    _git(workspace, "branch", "-D", branch)


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
    open_findings: Findings,
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
    findings = _open_findings(open_findings)
    try:
        body_file.write_text(
            f"Closes #{issue.number}\n\n{body.rstrip()}{findings}\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise PullRequestBodyError(f"could not update pull request body: {exc}") from exc
    return repository.open_pull_request(
        branch=branch,
        base_branch=metadata.default_branch,
        title=issue.title,
        body_file=body_file,
        reviewer=metadata.maintainer,
    )


def _open_findings(findings: Findings) -> str:
    items = tuple(f"- [hard] {item}" for item in findings.hard) + tuple(
        f"- [suggestion] {item}" for item in findings.suggestions
    )
    if not items:
        return ""
    return "\n\n## Open review findings\n\n" + "\n".join(items)


def _clean_workspace(workspace: Path) -> None:
    _git(workspace, "reset", "--hard")
    _git(workspace, "clean", "-fd")
    try:
        (workspace / PR_BODY).unlink(missing_ok=True)
    except OSError as exc:
        raise PullRequestBodyError(f"could not clear pull request body: {exc}") from exc


def _git(workspace: Path, *arguments: str) -> CommandResult:
    return run_command(
        ("git", *arguments),
        cwd=workspace,
        timeout_seconds=GIT_TIMEOUT_SECONDS,
    )
