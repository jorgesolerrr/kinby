"""Run the delegated issue-to-pull-request pipeline."""

import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from time import monotonic
from typing import Literal, NewType

from kinby.factory.clients import (
    ClaudeModel,
    CodexModel,
    CodexRun,
    CodingClientError,
    Findings,
    ReasoningEffort,
    fix_with_codex,
    review_with_claude,
    run_codex,
)
from kinby.factory.process import CommandError
from kinby.factory.pull_request import (
    ChecksFailed,
    ChecksPassed,
    PullRequestBodyError,
    RepositoryCheckFailed,
    branch_name,
    clean_failed_branch,
    open_pull_request,
    prepare_branch,
    run_checks,
)
from kinby.factory.report import report_json
from kinby.factory.repository import (
    BranchName,
    GitHubRepository,
    Issue,
    PullRequestUrl,
    RepositoryResponseError,
)
from kinby.factory.review import ReviewLoop, ReviewRound, run_review_loop
from kinby.factory.scan import (
    labeled_issue_number,
    oldest_eligible_issue,
    payload_can_change_eligibility,
    sibling_pull_requests,
)
from kinby.plugins import ToolContext, tool

DEFAULT_IMPLEMENTER_MODEL = CodexModel("gpt-5.6-sol")
DEFAULT_REVIEWER_MODEL = ClaudeModel("claude-fable-5-1")
PipelineWarning = NewType("PipelineWarning", str)


class PipelineOutcome(StrEnum):
    """The outcome of a delegated pipeline run."""

    OPENED = "opened"
    OPENED_WITH_FINDINGS = "opened_with_findings"
    FAILED = "failed"


@dataclass(frozen=True)
class PullRequestReport:
    """The pull request opened by a delegated pipeline run."""

    url: PullRequestUrl
    branch: BranchName
    base_branch: BranchName


@dataclass(frozen=True)
class OpenedPipelineReport:
    """A delegated pipeline run that opened a pull request."""

    issue: Issue
    pull_request: PullRequestReport
    checks: ChecksPassed
    codex: CodexRun
    review: ReviewLoop
    check_fix: CodexRun | None
    warnings: tuple[PipelineWarning, ...]
    duration_seconds: float
    outcome: Literal[
        PipelineOutcome.OPENED,
        PipelineOutcome.OPENED_WITH_FINDINGS,
    ] = PipelineOutcome.OPENED
    failure_reason: None = None


@dataclass(frozen=True)
class FailedPipelineReport:
    """A delegated pipeline run that failed before opening a pull request."""

    issue: Issue | None
    checks: ChecksFailed | None
    codex: CodexRun | None
    review: ReviewLoop | None
    check_fix: CodexRun | None
    duration_seconds: float
    failure_reason: str
    outcome: Literal[PipelineOutcome.FAILED] = PipelineOutcome.FAILED
    pull_request: None = None


type PipelineReport = OpenedPipelineReport | FailedPipelineReport


@tool(write=True)
def implement_ready_issue(
    signal: dict[str, object],
    context: ToolContext,
    implementer_model: CodexModel = DEFAULT_IMPLEMENTER_MODEL,
    implementer_effort: ReasoningEffort = ReasoningEffort.HIGH,
    reviewer_model: ClaudeModel = DEFAULT_REVIEWER_MODEL,
    review_round_limit: int = 3,
    implement_timeout_seconds: float = 1800,
    review_timeout_seconds: float = 600,
    fix_timeout_seconds: float = 900,
) -> str | None:
    """Implement the oldest ready issue and open its pull request."""
    if not payload_can_change_eligibility(signal):
        return None
    labeled_issue = labeled_issue_number(signal)
    started_at = monotonic()
    repository = GitHubRepository(context.workspace)
    issue: Issue | None = None
    codex: CodexRun | None = None
    review: ReviewLoop | None = None
    check_fix: CodexRun | None = None
    checks: ChecksFailed | None = None
    branch: BranchName | None = None
    base_branch: BranchName | None = None
    warnings: tuple[PipelineWarning, ...] = ()
    report: PipelineReport
    try:
        issues = repository.ready_issues()
        if labeled_issue is not None and all(issue.number != labeled_issue for issue in issues):
            fetched_issue = repository.ready_issue(labeled_issue)
        else:
            fetched_issue = None
        if fetched_issue is not None:
            issues = tuple(sorted((*issues, fetched_issue), key=lambda issue: issue.number))
        pull_requests = repository.agent_pull_requests()
        selected = oldest_eligible_issue(repository, issues, pull_requests)
        if selected is None:
            return None
        issue = selected
        siblings = sibling_pull_requests(issue, issues, pull_requests)
        metadata = repository.metadata()
        base_branch = siblings[-1].branch if siblings else metadata.default_branch
        branch = branch_name(issue)
        prepare_branch(context.workspace, branch, base_branch)
        codex = run_codex(
            context.workspace,
            issue_number=issue.number,
            issue_title=issue.title,
            issue_url=issue.url,
            model=implementer_model,
            effort=implementer_effort,
            timeout_seconds=implement_timeout_seconds,
        )
        review = run_review_loop(
            context.workspace,
            base_branch=base_branch,
            ticket_body=repository.issue_body(issue.number),
            thread_id=codex.thread_id,
            implementer_model=implementer_model,
            implementer_effort=implementer_effort,
            reviewer_model=reviewer_model,
            round_limit=review_round_limit,
            review_timeout_seconds=review_timeout_seconds,
            fix_timeout_seconds=fix_timeout_seconds,
        )
        try:
            passed_checks = run_checks(context.workspace)
        except RepositoryCheckFailed as exc:
            checks = ChecksFailed(failed=" ".join(exc.command))
            check_fix = fix_with_codex(
                context.workspace,
                thread_id=codex.thread_id,
                findings=Findings((str(exc),), (), str(exc)),
                model=implementer_model,
                effort=implementer_effort,
                timeout_seconds=fix_timeout_seconds,
            )
            try:
                passed_checks = run_checks(context.workspace)
            except RepositoryCheckFailed as retry_error:
                checks = ChecksFailed(failed=" ".join(retry_error.command))
                raise
            final_review = review_with_claude(
                context.workspace,
                base_branch=base_branch,
                ticket_body=repository.issue_body(issue.number),
                model=reviewer_model,
                timeout_seconds=review_timeout_seconds,
            )
            review = ReviewLoop(
                (
                    *review.rounds,
                    ReviewRound(
                        number=len(review.rounds) + 1,
                        review=final_review,
                        fix=None,
                        hard_count=len(final_review.findings.hard),
                        suggestion_count=len(final_review.findings.suggestions),
                        fix_usage=None,
                    ),
                ),
                final_review.findings,
            )
        pull_request = open_pull_request(
            repository,
            context.workspace,
            issue,
            metadata,
            branch,
            base_branch,
            review.open_findings,
        )
        if siblings:
            try:
                if (stack := siblings[-1].stack) is None:
                    previous_pull_requests = tuple(sibling.number for sibling in siblings)
                    repository.create_stack((*previous_pull_requests, pull_request.number))
                else:
                    repository.extend_stack(stack, pull_request.number)
            except CommandError as exc:
                warnings = (PipelineWarning(f"stack registration failed: {exc}"),)
        has_findings = bool(review.open_findings.hard or review.open_findings.suggestions)
        report = OpenedPipelineReport(
            issue=issue,
            pull_request=PullRequestReport(pull_request.url, branch, base_branch),
            checks=passed_checks,
            codex=codex,
            review=review,
            check_fix=check_fix,
            warnings=warnings,
            duration_seconds=monotonic() - started_at,
            outcome=(
                PipelineOutcome.OPENED_WITH_FINDINGS if has_findings else PipelineOutcome.OPENED
            ),
        )
    except (
        CommandError,
        CodingClientError,
        json.JSONDecodeError,
        PullRequestBodyError,
        RepositoryCheckFailed,
        RepositoryResponseError,
    ) as exc:
        failure = str(exc)
        if branch is not None and base_branch is not None:
            try:
                clean_failed_branch(context.workspace, branch, base_branch)
            except (CommandError, PullRequestBodyError) as cleanup_error:
                failure = f"{failure}; workspace cleanup failed: {cleanup_error}"
        if issue is not None:
            try:
                repository.mark_ready_for_human(issue.number)
            except CommandError as label_error:
                failure = f"{failure}; label update failed: {label_error}"
        report = FailedPipelineReport(
            issue=issue,
            checks=checks,
            codex=codex,
            review=review,
            check_fix=check_fix,
            duration_seconds=monotonic() - started_at,
            failure_reason=failure,
        )
    return report_json(asdict(report))
