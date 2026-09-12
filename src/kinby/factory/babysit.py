"""Classify review state and label one agent pull request."""

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Literal

from kinby.factory.report import report_json
from kinby.factory.repository import (
    AGENT_BRANCH_PREFIX,
    READY_FOR_HUMAN_LABEL,
    BabysitPullRequest,
    BranchName,
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


class BabysitOutcome(StrEnum):
    """The outcome of one babysitting action."""

    MERGE_READY = "merge_ready"
    ROUND_LIMIT = "round_limit"


@dataclass(frozen=True)
class BabysitReport:
    """The result of one pull request label change."""

    pull_request_number: PullRequestNumber
    pull_request_url: PullRequestUrl
    issue_number: IssueNumber
    outcome: BabysitOutcome
    round_number: int


@dataclass(frozen=True)
class ScheduledWake:
    """An hourly wake that always scans agent pull requests."""


@dataclass(frozen=True)
class PullRequestWake:
    """A pull request signal that carries its head branch."""

    branch: BranchName


@dataclass(frozen=True)
class PullRequestCommentWake:
    """A pull request comment signal whose branch GitHub must resolve."""

    pull_request: PullRequestNumber


@dataclass(frozen=True)
class IgnoredWake:
    """A signal unrelated to an agent pull request."""


type BabysitWake = ScheduledWake | PullRequestWake | PullRequestCommentWake | IgnoredWake


@dataclass(frozen=True)
class BabysitAction:
    """The oldest pull request label change selected by a scan."""

    pull_request: BabysitPullRequest
    outcome: BabysitOutcome
    label: LabelName


_OUTCOME_LABEL = {
    BabysitOutcome.MERGE_READY: MERGE_READY_LABEL,
    BabysitOutcome.ROUND_LIMIT: READY_FOR_HUMAN_LABEL,
}


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
    head = pull_request.listed.head
    reviewed_head = any(review.commit == head for review in pull_request.reviews) or any(
        comment.commit == head for thread in pull_request.threads for comment in thread.comments
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
    """Scan agent pull requests and label the oldest completed outcome."""
    repository = GitHubRepository(context.workspace)
    if not _wake_warrants_scan(parse_babysit_signal(signal), repository):
        return None
    coder = repository.current_login()
    action = select_babysit_action(repository.babysit_pull_requests(coder), coder, round_limit)
    if action is None:
        return None
    listed = action.pull_request.listed
    repository.label_pull_request(listed.number, action.label)
    if action.outcome is BabysitOutcome.MERGE_READY and coder != listed.author:
        repository.request_owner_review(listed.number)
    return report_json(asdict(_label_report(action.pull_request, action.outcome)))


def parse_babysit_signal(signal: dict[str, object]) -> BabysitWake:
    """Parse raw routine input into one babysitting wake."""
    if not signal:
        return ScheduledWake()
    body = signal.get("body")
    if not isinstance(body, dict):
        return IgnoredWake()
    pull_request = body.get("pull_request")
    if isinstance(pull_request, dict):
        branch = _pull_request_branch(pull_request)
        return PullRequestWake(branch) if branch is not None else IgnoredWake()
    issue = body.get("issue")
    if not isinstance(issue, dict) or not isinstance(
        nested_pull_request := issue.get("pull_request"), dict
    ):
        return IgnoredWake()
    if "head" in nested_pull_request:
        branch = _pull_request_branch(nested_pull_request)
        return PullRequestWake(branch) if branch is not None else IgnoredWake()
    number = issue.get("number")
    return (
        PullRequestCommentWake(PullRequestNumber(number))
        if isinstance(number, int)
        else IgnoredWake()
    )


def _pull_request_branch(value: dict[object, object]) -> BranchName | None:
    head = value.get("head")
    branch = head.get("ref") if isinstance(head, dict) else None
    return BranchName(branch) if isinstance(branch, str) else None


def _wake_warrants_scan(wake: BabysitWake, repository: GitHubRepository) -> bool:
    match wake:
        case ScheduledWake():
            return True
        case PullRequestWake(branch):
            return branch.startswith(AGENT_BRANCH_PREFIX)
        case PullRequestCommentWake(pull_request):
            return repository.pull_request_branch(pull_request).startswith(AGENT_BRANCH_PREFIX)
        case IgnoredWake():
            return False


def select_babysit_action(
    pull_requests: tuple[BabysitPullRequest, ...],
    coder: GitHubLogin,
    round_limit: int,
) -> BabysitAction | None:
    """Return the oldest pull request whose completed outcome needs a label."""
    for pull_request in pull_requests:
        outcome = _label_outcome(pull_request, coder, round_limit)
        if outcome is None:
            continue
        label = _OUTCOME_LABEL[outcome]
        if label not in pull_request.listed.labels:
            return BabysitAction(pull_request, outcome, label)
    return None


def _label_outcome(
    pull_request: BabysitPullRequest,
    coder: GitHubLogin,
    round_limit: int,
) -> Literal[BabysitOutcome.MERGE_READY, BabysitOutcome.ROUND_LIMIT] | None:
    if is_merge_ready(pull_request, coder):
        return BabysitOutcome.MERGE_READY
    if pull_request.round_count >= round_limit and actionable_threads(pull_request.threads, coder):
        return BabysitOutcome.ROUND_LIMIT
    return None


def _label_report(
    pull_request: BabysitPullRequest,
    outcome: Literal[BabysitOutcome.MERGE_READY, BabysitOutcome.ROUND_LIMIT],
) -> BabysitReport:
    listed = pull_request.listed
    issue = listed.closed_issue
    if issue is None:
        raise ValueError("agent pull request body does not close an issue")
    return BabysitReport(
        pull_request_number=listed.number,
        pull_request_url=listed.url,
        issue_number=issue,
        outcome=outcome,
        round_number=pull_request.round_count,
    )
