"""Classify review state and label agent pull requests."""

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Literal

from kinby.factory.report import report_json
from kinby.factory.repository import (
    AGENT_BRANCH_PREFIX,
    BabysitPullRequest,
    CheckRun,
    CheckRunStatus,
    GitHubLogin,
    GitHubRepository,
    IssueNumber,
    LabelName,
    PullRequestNumber,
    PullRequestUrl,
    ReviewThread,
)
from kinby.plugins import ToolContext, tool

MERGE_READY_LABEL = LabelName("merge-ready")
READY_FOR_HUMAN_LABEL = LabelName("ready-for-human")


class BabysitOutcome(StrEnum):
    """The outcome of one babysitting action."""

    MERGE_READY = "merge_ready"
    ROUND_LIMIT = "round_limit"


@dataclass(frozen=True)
class BabysitReport:
    """A babysitting result that changed a pull request label."""

    pull_request_number: PullRequestNumber
    pull_request_url: PullRequestUrl
    issue_number: IssueNumber
    outcome: Literal[BabysitOutcome.MERGE_READY, BabysitOutcome.ROUND_LIMIT]
    round_number: int


def actionable_threads(
    threads: tuple[ReviewThread, ...],
    coder: GitHubLogin,
) -> tuple[ReviewThread, ...]:
    """Return unresolved threads whose last comment is not the coder's."""
    return tuple(
        thread
        for thread in threads
        if not thread.resolved and thread.comments and thread.comments[-1].author != coder
    )


def is_waiting(checks: tuple[CheckRun, ...]) -> bool:
    """Return whether any check run is queued or in progress."""
    return any(
        check.status in {CheckRunStatus.QUEUED, CheckRunStatus.IN_PROGRESS} for check in checks
    )


def is_merge_ready(pull_request: BabysitPullRequest, coder: GitHubLogin) -> bool:
    """Return whether a reviewed pull request has nothing left to answer."""
    reviewed_head = any(
        review.commit == pull_request.head for review in pull_request.reviews
    ) or any(
        comment.commit == pull_request.head
        for thread in pull_request.threads
        for comment in thread.comments
    )
    return (
        reviewed_head
        and not actionable_threads(pull_request.threads, coder)
        and not is_waiting(pull_request.checks)
    )


@tool(write=True)
def babysit_pull_request(
    signal: dict[str, object],
    context: ToolContext,
    round_limit: int = 3,
) -> str | None:
    """Scan agent pull requests and label a completed babysitting outcome."""
    repository = GitHubRepository(context.workspace)
    if not _signal_warrants_scan(signal, repository):
        return None
    coder = repository.current_login()
    coordinates = repository.coordinates()
    pull_requests = repository.babysit_pull_requests(coder, coordinates)
    first_report: BabysitReport | None = None
    for pull_request in pull_requests:
        if is_merge_ready(pull_request, coder) and MERGE_READY_LABEL not in pull_request.labels:
            report = _label_report(pull_request, BabysitOutcome.MERGE_READY)
            repository.label_pull_request(pull_request.number, MERGE_READY_LABEL)
            if coder != pull_request.author:
                repository.request_review(pull_request.number, coordinates.owner)
            if first_report is None:
                first_report = report
        if (
            pull_request.round_count >= round_limit
            and actionable_threads(pull_request.threads, coder)
            and READY_FOR_HUMAN_LABEL not in pull_request.labels
        ):
            report = _label_report(pull_request, BabysitOutcome.ROUND_LIMIT)
            repository.label_pull_request(pull_request.number, READY_FOR_HUMAN_LABEL)
            if first_report is None:
                first_report = report
    return report_json(asdict(first_report)) if first_report is not None else None


def _signal_warrants_scan(
    signal: dict[str, object],
    repository: GitHubRepository,
) -> bool:
    if not signal:
        return True
    body = signal.get("body")
    if not isinstance(body, dict):
        return False
    pull_request = body.get("pull_request")
    if isinstance(pull_request, dict):
        return _pull_request_is_agent(pull_request)
    issue = body.get("issue")
    if not isinstance(issue, dict) or not isinstance(
        nested_pull_request := issue.get("pull_request"), dict
    ):
        return False
    if "head" in nested_pull_request:
        return _pull_request_is_agent(nested_pull_request)
    number = issue.get("number")
    if not isinstance(number, int):
        return False
    branch = repository.pull_request_branch(PullRequestNumber(number))
    return branch.startswith(AGENT_BRANCH_PREFIX)


def _pull_request_is_agent(value: dict[object, object]) -> bool:
    head = value.get("head")
    return (
        isinstance(head, dict)
        and isinstance(branch := head.get("ref"), str)
        and branch.startswith(AGENT_BRANCH_PREFIX)
    )


def _label_report(
    pull_request: BabysitPullRequest,
    outcome: Literal[BabysitOutcome.MERGE_READY, BabysitOutcome.ROUND_LIMIT],
) -> BabysitReport:
    issue = pull_request.closed_issue
    if issue is None:
        raise ValueError("agent pull request body does not close an issue")
    return BabysitReport(
        pull_request_number=pull_request.number,
        pull_request_url=pull_request.url,
        issue_number=issue,
        outcome=outcome,
        round_number=pull_request.round_count,
    )
