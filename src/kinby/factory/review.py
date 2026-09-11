"""Run independent reviews and fixes before the delegated pipeline opens a PR."""

from dataclasses import dataclass
from pathlib import Path

from kinby.factory.clients import (
    ClaudeModel,
    CodexModel,
    CodexRun,
    CodexThreadId,
    CodingClientError,
    Findings,
    ReasoningEffort,
    ReviewRun,
    fix_with_codex,
    review_with_claude,
)
from kinby.factory.repository import BranchName


@dataclass(frozen=True)
class ReviewRound:
    """One review and the fix that answered it, when another round remains."""

    number: int
    review: ReviewRun
    fix: CodexRun | None


@dataclass(frozen=True)
class ReviewLoop:
    """The completed review rounds and any findings left for the maintainer."""

    rounds: tuple[ReviewRound, ...]
    open_findings: Findings


def run_review_loop(
    workspace: Path,
    *,
    base_branch: BranchName,
    ticket_body: str,
    thread_id: CodexThreadId,
    implementer_model: CodexModel,
    implementer_effort: ReasoningEffort,
    reviewer_model: ClaudeModel,
    round_limit: int,
    review_timeout_seconds: float,
    fix_timeout_seconds: float,
) -> ReviewLoop:
    """Review and fix until clean or the configured review cap is reached."""
    if round_limit < 1:
        raise CodingClientError("review round limit must be at least one")
    rounds: list[ReviewRound] = []
    for number in range(1, round_limit + 1):
        review = review_with_claude(
            workspace,
            base_branch=base_branch,
            ticket_body=ticket_body,
            model=reviewer_model,
            timeout_seconds=review_timeout_seconds,
        )
        findings = review.findings
        should_fix = bool(findings.hard) or bool(findings.suggestions and not rounds)
        if not should_fix or number == round_limit:
            rounds.append(ReviewRound(number, review, None))
            return ReviewLoop(tuple(rounds), findings)
        fix_findings = Findings(
            findings.hard,
            findings.suggestions if not rounds else (),
            findings.raw,
        )
        fix = fix_with_codex(
            workspace,
            thread_id=thread_id,
            findings=fix_findings,
            model=implementer_model,
            effort=implementer_effort,
            timeout_seconds=fix_timeout_seconds,
        )
        rounds.append(ReviewRound(number, review, fix))
    raise AssertionError("review loop exhausted without returning")
