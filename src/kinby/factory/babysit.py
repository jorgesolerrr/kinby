"""Classify review state and label agent pull requests."""

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Literal, NewType

from kinby.factory.clients import CodexModel, CodexRun, ReasoningEffort
from kinby.factory.pull_request import ChecksFailed, ChecksPassed
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

DEFAULT_FIX_MODEL = CodexModel("gpt-5.6-sol")
MERGE_READY_LABEL = LabelName("merge-ready")
BabysitWarning = NewType("BabysitWarning", str)


class BabysitOutcome(StrEnum):
    """The outcome of one babysitting action."""

    FIXED = "fixed"
    MERGE_READY = "merge_ready"
    ROUND_LIMIT = "round_limit"
    FAILED = "failed"


@dataclass(frozen=True)
class BabysitReport:
    """A babysitting result that changed a pull request label."""

    pull_request_number: PullRequestNumber
    pull_request_url: PullRequestUrl
    issue_number: IssueNumber
    outcome: BabysitOutcome
    round_number: int
    threads_fixed: int
    threads_answered: int
    codex: CodexRun | None
    checks: ChecksPassed | ChecksFailed | None
    warnings: tuple[BabysitWarning, ...]
    failure_reason: str | None


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
    fix_model: CodexModel = DEFAULT_FIX_MODEL,
    fix_effort: ReasoningEffort = ReasoningEffort.HIGH,
    round_limit: int = 3,
    fix_timeout_seconds: float = 900,
    checks_fix_timeout_seconds: float = 900,
) -> str | None:
    """Scan agent pull requests and label a completed babysitting outcome."""
    repository = GitHubRepository(context.workspace)
    signaled_pull_request = signal_pull_request_number(signal)
    signaled_branch = (
        repository.pull_request_branch(signaled_pull_request)
        if signaled_pull_request is not None
        else None
    )
    if not signal_warrants_scan(signal, signaled_branch):
        return None
    coder = repository.current_login()
    coordinates = repository.coordinates()
    pull_requests = repository.babysit_pull_requests(coder, coordinates)
    reports: list[BabysitReport] = []
    for pull_request in pull_requests:
        listed = pull_request.listed
        outcome = _label_outcome(pull_request, coder, round_limit)
        if outcome is not BabysitOutcome.MERGE_READY and MERGE_READY_LABEL in listed.labels:
            repository.remove_pull_request_label(listed.number, MERGE_READY_LABEL)
        if outcome is BabysitOutcome.MERGE_READY and READY_FOR_HUMAN_LABEL in listed.labels:
            repository.remove_pull_request_label(listed.number, READY_FOR_HUMAN_LABEL)
        if outcome is None:
            continue
        label = (
            MERGE_READY_LABEL if outcome is BabysitOutcome.MERGE_READY else READY_FOR_HUMAN_LABEL
        )
        if label in listed.labels:
            continue
        repository.label_pull_request(listed.number, label)
        if outcome is BabysitOutcome.MERGE_READY and coder != listed.author:
            repository.request_review(listed.number, coordinates.owner)
        reports.append(_label_report(pull_request, outcome))
    return _reports_json(reports)


def signal_warrants_scan(
    signal: dict[str, object],
    abbreviated_pull_request_branch: BranchName | None = None,
) -> bool:
    """Return whether a schedule or self-contained signal warrants a scan."""
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
    return (
        abbreviated_pull_request_branch is not None
        and abbreviated_pull_request_branch.startswith(AGENT_BRANCH_PREFIX)
    )


def signal_pull_request_number(signal: dict[str, object]) -> PullRequestNumber | None:
    """Return the PR number from an abbreviated pull request comment signal."""
    body = signal.get("body")
    issue = body.get("issue") if isinstance(body, dict) else None
    if (
        not isinstance(issue, dict)
        or not isinstance(issue.get("pull_request"), dict)
        or not isinstance(number := issue.get("number"), int)
    ):
        return None
    return PullRequestNumber(number)


def _pull_request_is_agent(value: dict[object, object]) -> bool:
    head = value.get("head")
    return (
        isinstance(head, dict)
        and isinstance(branch := head.get("ref"), str)
        and branch.startswith(AGENT_BRANCH_PREFIX)
    )


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


def _reports_json(reports: list[BabysitReport]) -> str | None:
    if not reports:
        return None
    values = [asdict(report) for report in reports]
    return report_json(values[0] if len(values) == 1 else values)


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
        threads_fixed=0,
        threads_answered=0,
        codex=None,
        checks=None,
        warnings=(),
        failure_reason=None,
    )
