"""Run the delegated issue-to-pull-request pipeline."""

import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from time import monotonic
from typing import Literal

from kinby.factory.clients import (
    ClaudeModel,
    CodexModel,
    CodexRun,
    CodingClientError,
    ReasoningEffort,
    run_codex,
)
from kinby.factory.process import CommandError
from kinby.factory.pull_request import (
    ChecksFailed,
    ChecksPassed,
    PullRequestBodyError,
    RepositoryCheckFailed,
    branch_name,
    open_pull_request,
    prepare_branch,
    run_checks,
)
from kinby.factory.repository import (
    BranchName,
    GitHubRepository,
    Issue,
    PullRequestUrl,
    RepositoryResponseError,
)
from kinby.factory.scan import oldest_issue_without_agent_pr, payload_can_change_eligibility
from kinby.plugins import ToolContext, tool

DEFAULT_IMPLEMENTER_MODEL = CodexModel("gpt-5.6-sol")
DEFAULT_REVIEWER_MODEL = ClaudeModel("claude-fable-5-1")


class PipelineOutcome(StrEnum):
    """The outcome of a delegated pipeline run."""

    OPENED = "opened"
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
    duration_seconds: float
    outcome: Literal[PipelineOutcome.OPENED] = PipelineOutcome.OPENED
    failure_reason: None = None


@dataclass(frozen=True)
class FailedPipelineReport:
    """A delegated pipeline run that failed before opening a pull request."""

    issue: Issue | None
    checks: ChecksFailed | None
    codex: CodexRun | None
    duration_seconds: float
    failure_reason: str
    outcome: Literal[PipelineOutcome.FAILED] = PipelineOutcome.FAILED
    pull_request: None = None


type PipelineReport = OpenedPipelineReport | FailedPipelineReport


def _report_json(report: PipelineReport) -> str:
    return json.dumps(asdict(report), separators=(",", ":"))


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
    started_at = monotonic()
    repository = GitHubRepository(context.workspace)
    issue: Issue | None = None
    codex: CodexRun | None = None
    checks: ChecksFailed | None = None
    report: PipelineReport
    try:
        issues = repository.ready_issues()
        pull_requests = repository.agent_pull_requests()
        selected = oldest_issue_without_agent_pr(issues, pull_requests)
        if selected is None:
            return None
        issue = selected
        metadata = repository.metadata()
        branch = branch_name(issue)
        prepare_branch(context.workspace, branch, metadata.default_branch)
        codex = run_codex(
            context.workspace,
            issue_number=issue.number,
            issue_title=issue.title,
            issue_url=issue.url,
            model=implementer_model,
            effort=implementer_effort,
            timeout_seconds=implement_timeout_seconds,
        )
        try:
            passed_checks = run_checks(context.workspace)
        except RepositoryCheckFailed as exc:
            checks = ChecksFailed(failed=" ".join(exc.command))
            raise
        pull_request_url = open_pull_request(
            repository,
            context.workspace,
            issue,
            metadata,
            branch,
        )
        report = OpenedPipelineReport(
            issue=issue,
            pull_request=PullRequestReport(pull_request_url, branch, metadata.default_branch),
            checks=passed_checks,
            codex=codex,
            duration_seconds=monotonic() - started_at,
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
        if issue is not None:
            try:
                repository.mark_ready_for_human(issue.number)
            except CommandError as label_error:
                failure = f"{failure}; label update failed: {label_error}"
        report = FailedPipelineReport(
            issue=issue,
            checks=checks,
            codex=codex,
            duration_seconds=monotonic() - started_at,
            failure_reason=failure,
        )
    return _report_json(report)
