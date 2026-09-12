"""Decide whether a routine wake can change issue eligibility."""

from dataclasses import dataclass

from kinby.factory.repository import (
    AGENT_BRANCH_PREFIX,
    READY_LABEL,
    AgentPullRequest,
    GitHubRepository,
    Issue,
    IssueNumber,
    OpenBlocker,
)


@dataclass(frozen=True)
class ScanRequest:
    """A wake that warrants a scan, with its labeled issue when known."""

    labeled_issue: IssueNumber | None


@dataclass(frozen=True)
class SkipScan:
    """A wake that cannot change issue eligibility."""


def scan_request(signal: dict[str, object]) -> ScanRequest | SkipScan:
    """Parse a wake into the scan it warrants."""
    body = signal.get("body")
    if not isinstance(body, dict):
        return ScanRequest(None)
    if "comment" in body:
        return SkipScan()
    action = body.get("action")
    if action in {"labeled", "unlabeled"}:
        if not _names_ready_label(body):
            return SkipScan()
        labeled_issue = _labeled_issue_number(body) if action == "labeled" else None
        return ScanRequest(labeled_issue)
    pull_request = body.get("pull_request")
    if not isinstance(pull_request, dict):
        return ScanRequest(None)
    head = pull_request.get("head")
    if (
        isinstance(head, dict)
        and isinstance(branch := head.get("ref"), str)
        and branch.startswith(AGENT_BRANCH_PREFIX)
    ):
        return ScanRequest(None)
    return SkipScan()


def ready_issues(
    repository: GitHubRepository,
    labeled_number: IssueNumber | None,
) -> tuple[Issue, ...]:
    """Return the sorted candidates for a wake."""
    issues = repository.ready_issues()
    if (
        labeled_number is not None
        and all(issue.number != labeled_number for issue in issues)
        and (labeled_issue := repository.ready_issue(labeled_number)) is not None
    ):
        return tuple(sorted((*issues, labeled_issue), key=lambda issue: issue.number))
    return issues


def oldest_eligible_issue(
    repository: GitHubRepository,
    issues: tuple[Issue, ...],
    pull_requests: tuple[AgentPullRequest, ...],
) -> Issue | None:
    """Return the lowest-numbered issue whose blockers are covered in its stack."""
    covered = {
        pull_request.closed_issue
        for pull_request in pull_requests
        if pull_request.closed_issue is not None
    }
    for issue in issues:
        if issue.number in covered:
            continue
        blockers = repository.open_blockers(issue.number)
        if all(_covered_in_same_stack(issue, blocker, covered) for blocker in blockers):
            return issue
    return None


def sibling_pull_requests(
    issue: Issue,
    issues: tuple[Issue, ...],
    pull_requests: tuple[AgentPullRequest, ...],
) -> tuple[AgentPullRequest, ...]:
    """Return a sub-issue's sibling pull requests from oldest to newest."""
    if issue.parent is None:
        return ()
    sibling_numbers = {sibling.number for sibling in issues if sibling.parent == issue.parent}
    newest_first = tuple(
        pull_request
        for pull_request in pull_requests
        if pull_request.closed_issue in sibling_numbers
    )
    return tuple(reversed(newest_first))


def _covered_in_same_stack(
    issue: Issue,
    blocker: OpenBlocker,
    covered: set[IssueNumber],
) -> bool:
    return blocker.number in covered and _stack(issue.number, issue.parent) == _stack(
        blocker.number, blocker.parent
    )


def _stack(issue: IssueNumber, parent: IssueNumber | None) -> IssueNumber:
    return parent if parent is not None else issue


def _names_ready_label(body: dict[str, object]) -> bool:
    label = body.get("label")
    return isinstance(label, dict) and label.get("name") == READY_LABEL


def _labeled_issue_number(body: dict[str, object]) -> IssueNumber | None:
    issue = body.get("issue")
    if not isinstance(issue, dict) or "pull_request" in issue:
        return None
    number = issue.get("number")
    return IssueNumber(number) if isinstance(number, int) else None
