"""Prepare, check, push, and open an agent pull request."""

import re
from pathlib import Path

from kinby.factory.clients import PR_BODY, REVIEW_REPLIES, Findings
from kinby.factory.process import CommandError, CommandResult, run_command
from kinby.factory.repository import (
    AGENT_BRANCH_PREFIX,
    BranchName,
    CommitSha,
    GitHubRepository,
    Issue,
    OpenedPullRequest,
    RepositoryMetadata,
    closed_issue_number,
)

GIT_TIMEOUT_SECONDS = 900.0


class WorkspaceFileError(RuntimeError):
    """The pipeline could not read, update, or clear a workspace file."""


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


def checkout_branch(workspace: Path, branch: BranchName) -> None:
    """Check out an existing agent pull request branch without rebasing it."""
    _clean_workspace(workspace)
    _git(workspace, "fetch", "origin")
    _git(
        workspace,
        "switch",
        "--discard-changes",
        "-C",
        branch,
        f"origin/{branch}",
    )
    _clean_workspace(workspace)


def current_commit(workspace: Path) -> CommitSha:
    """Return the checked-out commit."""
    commit = _git(workspace, "rev-parse", "HEAD").stdout.strip()
    if not commit:
        raise CommandError("git rev-parse returned an empty commit")
    return CommitSha(commit)


def push_checked_out_branch(workspace: Path) -> None:
    """Push the checked-out pull request branch without rewriting history."""
    _git(workspace, "push")


def verify_committed_workspace(workspace: Path) -> None:
    """Reject checked changes that are absent from HEAD."""
    status = _git(workspace, "status", "--porcelain", "--untracked-files=all").stdout
    generated = {PR_BODY.as_posix(), REVIEW_REPLIES.as_posix()}
    if any(line[3:] not in generated for line in status.splitlines()):
        raise WorkspaceFileError("Codex left uncommitted workspace changes")


def discard_branch(
    workspace: Path,
    branch: BranchName,
    base_branch: BranchName,
) -> None:
    """Discard a local branch and return the clean workspace to its base."""
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


def discard_branch_for_report(
    workspace: Path,
    branch: BranchName,
    base_branch: BranchName,
    failure_reason: str,
) -> str:
    """Discard a branch and include any cleanup error in its report reason."""
    try:
        discard_branch(workspace, branch, base_branch)
    except (CommandError, WorkspaceFileError) as cleanup_error:
        return f"{failure_reason}; workspace cleanup failed: {cleanup_error}"
    return failure_reason


def open_pull_request(
    repository: GitHubRepository,
    workspace: Path,
    issue: Issue,
    metadata: RepositoryMetadata,
    branch: BranchName,
    base_branch: BranchName,
    open_findings: Findings | None,
) -> OpenedPullRequest:
    """Push the checked branch and open its pull request."""
    _git(workspace, "push", "-u", "origin", branch)
    body_file = workspace / PR_BODY
    try:
        body = body_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise WorkspaceFileError(f"could not read pull request body: {exc}") from exc
    if closed_issue_number(body.partition("\n")[0]) == issue.number:
        body = body.partition("\n")[2].lstrip()
    findings = _open_findings(open_findings)
    try:
        body_file.write_text(
            f"Closes #{issue.number}\n\n{body.rstrip()}{findings}\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise WorkspaceFileError(f"could not update pull request body: {exc}") from exc
    return repository.open_pull_request(
        branch=branch,
        base_branch=base_branch,
        title=issue.title,
        body_file=body_file,
        reviewer=metadata.maintainer,
    )


def _open_findings(findings: Findings | None) -> str:
    if findings is None:
        return (
            "\n\n## Review status\n\n"
            "Adversarial review was not run. Review happens on this pull request."
        )
    items = tuple(f"- [hard] {item}" for item in findings.hard) + tuple(
        f"- [suggestion] {item}" for item in findings.suggestions
    )
    if not items:
        return ""
    return "\n\n## Open review findings\n\n" + "\n".join(items)


def _clean_workspace(workspace: Path) -> None:
    _git(workspace, "reset", "--hard")
    _git(workspace, "clean", "-fd")
    for path in (PR_BODY, REVIEW_REPLIES):
        try:
            (workspace / path).unlink(missing_ok=True)
        except OSError as exc:
            raise WorkspaceFileError(f"could not clear {path}: {exc}") from exc


def _git(workspace: Path, *arguments: str) -> CommandResult:
    return run_command(
        ("git", *arguments),
        cwd=workspace,
        timeout_seconds=GIT_TIMEOUT_SECONDS,
    )
