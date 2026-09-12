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
    if body.get("action") == "labeled":
        return _ready_label_event(body) is not None
    if body.get("action") == "unlabeled":
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


def labeled_issue_number(signal: dict[str, object]) -> IssueNumber | None:
    """Return the issue named by a ready-label delivery."""
    body = signal.get("body")
    if not isinstance(body, dict) or (delivery := _ready_label_event(body)) is None:
        return None
    issue = delivery.get("issue")
    if (
        not isinstance(issue, dict)
        or "pull_request" in issue
        or not isinstance(number := issue.get("number"), int)
    ):
        return None
    return IssueNumber(number)


def ready_issues(
    repository: GitHubRepository,
    signal: dict[str, object],
) -> tuple[Issue, ...]:
    """Return the sorted candidates for a wake."""
    issues = repository.ready_issues()
    labeled_number = labeled_issue_number(signal)
    if (
        labeled_number is not None
        and all(issue.number != labeled_number for issue in issues)
        and (labeled_issue := repository.ready_issue(labeled_number)) is not None
    ):
        issues = (*issues, labeled_issue)
    return tuple(sorted(issues, key=lambda issue: issue.number))


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


def _ready_label_event(body: dict[str, object]) -> dict[str, object] | None:
    label = body.get("label")
    if (
        body.get("action") != "labeled"
        or not isinstance(label, dict)
        or label.get("name") != READY_LABEL
    ):
        return None
    return body
