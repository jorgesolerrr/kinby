"""Decide whether a routine wake can change issue eligibility."""

from kinby.factory.repository import AGENT_BRANCH_PREFIX, READY_LABEL, AgentPullRequest, Issue


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


def oldest_issue_without_agent_pr(
    issues: tuple[Issue, ...],
    pull_requests: tuple[AgentPullRequest, ...],
) -> Issue | None:
    """Return the lowest-numbered issue without an open agent pull request."""
    covered = {
        pull_request.closed_issue
        for pull_request in pull_requests
        if pull_request.closed_issue is not None
    }
    return next(
        (issue for issue in issues if issue.number not in covered),
        None,
    )
