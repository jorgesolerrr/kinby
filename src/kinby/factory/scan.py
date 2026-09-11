"""Decide whether a routine wake can change issue eligibility."""

from kinby.factory.repository import (
    AGENT_BRANCH_PREFIX,
    READY_LABEL,
    AgentPullRequest,
    GitHubRepository,
    Issue,
    IssueNumber,
    OpenBlocker,
)


def payload_can_change_eligibility(signal: dict[str, object]) -> bool:
    """Return whether a wake warrants a GitHub scan."""
    body = signal.get("body")
    if not isinstance(body, dict):
        return True
    if "comment" in body:
        return False
    if body.get("action") in {"labeled", "unlabeled"}:
        label = body.get("label")
        return isinstance(label, dict) and label.get("name") == READY_LABEL
    pull_request = body.get("pull_request")
    if not isinstance(pull_request, dict):
        return True
    head = pull_request.get("head")
    return (
        isinstance(head, dict)
        and isinstance(branch := head.get("ref"), str)
        and branch.startswith(AGENT_BRANCH_PREFIX)
    )


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
