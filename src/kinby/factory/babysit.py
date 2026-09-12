"""Classify review state and label agent pull requests."""

from dataclasses import dataclass
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
    """The result of one babysitting action."""

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


@dataclass(frozen=True)
class _ScheduledWake:
    """An hourly wake that always scans agent pull requests."""


@dataclass(frozen=True)
class _PullRequestWake:
    """A pull request signal that carries its head branch."""

    branch: BranchName


@dataclass(frozen=True)
class _PullRequestCommentWake:
    """A pull request comment signal whose branch GitHub must resolve."""

    pull_request: PullRequestNumber


@dataclass(frozen=True)
class _IgnoredWake:
    """A signal unrelated to an agent pull request."""


type _BabysitWake = _ScheduledWake | _PullRequestWake | _PullRequestCommentWake | _IgnoredWake


@dataclass(frozen=True)
class _BabysitAction:
    """One pull request label change selected by a scan."""

    pull_request: BabysitPullRequest
    issue: IssueNumber
    outcome: BabysitOutcome
    label: LabelName


_OUTCOME_LABEL = {
    BabysitOutcome.MERGE_READY: MERGE_READY_LABEL,
    BabysitOutcome.ROUND_LIMIT: READY_FOR_HUMAN_LABEL,
}


def _actionable_threads(
    threads: tuple[ReviewThread, ...],
    coder: GitHubLogin,
) -> tuple[ReviewThread, ...]:
    """Return unresolved threads whose last comment is not the coder's."""
    return tuple(
        thread
        for thread in threads
        if not thread.resolved and thread.comments and thread.comments[-1].author != coder
    )


def _is_waiting(checks: tuple[CheckRun, ...]) -> bool:
    """Return whether any check run is queued or in progress."""
    return any(
        check.status in {CheckRunStatus.QUEUED, CheckRunStatus.IN_PROGRESS} for check in checks
    )


def _is_merge_ready(pull_request: BabysitPullRequest, coder: GitHubLogin) -> bool:
    """Return whether a reviewed pull request has nothing left to answer."""
    head = pull_request.listed.head
    reviewed_head = any(review.commit == head for review in pull_request.reviews) or any(
        comment.commit == head for thread in pull_request.threads for comment in thread.comments
    )
    return (
        reviewed_head
        and not _actionable_threads(pull_request.threads, coder)
        and not _is_waiting(pull_request.checks)
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
    """Scan agent pull requests and label every completed outcome."""
    repository = GitHubRepository(context.workspace)
    if not _wake_warrants_scan(_parse_babysit_signal(signal), repository):
        return None
    coder = repository.current_login()
    metadata = repository.metadata()
    actions = _select_babysit_actions(
        repository.babysit_pull_requests(coder, metadata),
        coder,
        round_limit,
    )
    if not actions:
        return None
    for action in actions:
        listed = action.pull_request.listed
        repository.label_pull_request(listed.number, action.label)
        if action.outcome is BabysitOutcome.MERGE_READY and coder != listed.author:
            repository.request_review(listed.number, metadata.maintainer)
    return report_json(tuple(_label_report(action) for action in actions))


def _parse_babysit_signal(signal: dict[str, object]) -> _BabysitWake:
    """Parse raw routine input into one babysitting wake."""
    if not signal:
        return _ScheduledWake()
    body = signal.get("body")
    if not isinstance(body, dict):
        return _IgnoredWake()
    pull_request = body.get("pull_request")
    if isinstance(pull_request, dict):
        branch = _pull_request_branch(pull_request)
        return _PullRequestWake(branch) if branch is not None else _IgnoredWake()
    issue = body.get("issue")
    if not isinstance(issue, dict) or not isinstance(
        nested_pull_request := issue.get("pull_request"), dict
    ):
        return _IgnoredWake()
    if "head" in nested_pull_request:
        branch = _pull_request_branch(nested_pull_request)
        return _PullRequestWake(branch) if branch is not None else _IgnoredWake()
    number = issue.get("number")
    return (
        _PullRequestCommentWake(PullRequestNumber(number))
        if isinstance(number, int)
        else _IgnoredWake()
    )


def _pull_request_branch(value: dict[object, object]) -> BranchName | None:
    head = value.get("head")
    branch = head.get("ref") if isinstance(head, dict) else None
    return BranchName(branch) if isinstance(branch, str) else None


def _wake_warrants_scan(wake: _BabysitWake, repository: GitHubRepository) -> bool:
    match wake:
        case _ScheduledWake():
            return True
        case _PullRequestWake(branch):
            return branch.startswith(AGENT_BRANCH_PREFIX)
        case _PullRequestCommentWake(pull_request):
            return repository.pull_request_branch(pull_request).startswith(AGENT_BRANCH_PREFIX)
        case _IgnoredWake():
            return False


def _select_babysit_actions(
    pull_requests: tuple[BabysitPullRequest, ...],
    coder: GitHubLogin,
    round_limit: int,
) -> tuple[_BabysitAction, ...]:
    """Return every pull request whose completed outcome needs a label."""
    actions: list[_BabysitAction] = []
    for pull_request in pull_requests:
        outcome = _label_outcome(pull_request, coder, round_limit)
        if outcome is None:
            continue
        label = _OUTCOME_LABEL[outcome]
        if label not in pull_request.listed.labels:
            issue = pull_request.listed.closed_issue
            if issue is None:
                raise ValueError("agent pull request body does not close an issue")
            actions.append(_BabysitAction(pull_request, issue, outcome, label))
    return tuple(actions)


def _label_outcome(
    pull_request: BabysitPullRequest,
    coder: GitHubLogin,
    round_limit: int,
) -> Literal[BabysitOutcome.MERGE_READY, BabysitOutcome.ROUND_LIMIT] | None:
    if _is_merge_ready(pull_request, coder):
        return BabysitOutcome.MERGE_READY
    if pull_request.round_count >= round_limit and _actionable_threads(pull_request.threads, coder):
        return BabysitOutcome.ROUND_LIMIT
    return None


def _label_report(action: _BabysitAction) -> BabysitReport:
    listed = action.pull_request.listed
    return BabysitReport(
        pull_request_number=listed.number,
        pull_request_url=listed.url,
        issue_number=action.issue,
        outcome=action.outcome,
        round_number=action.pull_request.round_count,
        threads_fixed=0,
        threads_answered=0,
        codex=None,
        checks=None,
        warnings=(),
        failure_reason=None,
    )
