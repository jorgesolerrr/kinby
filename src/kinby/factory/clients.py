"""Run coding clients and parse their machine-readable results."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import NewType

from kinby.factory.process import run_command
from kinby.factory.repository import IssueNumber, IssueTitle, IssueUrl

PR_BODY = Path(".scratch/pr-body.md")
TICKET_BODY = Path(".scratch/factory-ticket.md")
CodexModel = NewType("CodexModel", str)
ClaudeModel = NewType("ClaudeModel", str)
CodexThreadId = NewType("CodexThreadId", str)
_FINDING = re.compile(r"^\s*(?:[-*]\s*)?\[(hard|suggestion)\]\s*(.+)$", re.IGNORECASE)


class ReasoningEffort(StrEnum):
    """A reasoning effort accepted by the Codex client."""

    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class CodingClientError(RuntimeError):
    """A coding client's workspace input or response is invalid."""


@dataclass(frozen=True)
class TokenUsage:
    """Tokens reported by a coding client."""

    input_tokens: int
    cached_input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class CodexRun:
    """The observable result of one Codex implementation run."""

    thread_id: CodexThreadId
    usage: TokenUsage
    duration_seconds: float


@dataclass(frozen=True)
class Findings:
    """Tagged findings returned by both review axes."""

    hard: tuple[str, ...]
    suggestions: tuple[str, ...]
    raw: str


@dataclass(frozen=True)
class ReviewRun:
    """The merged result of one parallel two-axis review."""

    findings: Findings
    duration_seconds: float


def run_codex(
    workspace: Path,
    *,
    issue_number: IssueNumber,
    issue_title: IssueTitle,
    issue_url: IssueUrl,
    model: CodexModel,
    effort: ReasoningEffort,
    timeout_seconds: float,
) -> CodexRun:
    """Run Codex once for one issue."""
    _clear_pr_body(workspace)
    result = run_command(
        (
            "codex",
            "exec",
            "--model",
            model,
            "--config",
            f'model_reasoning_effort="{effort}"',
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "--cd",
            str(workspace),
            "-",
        ),
        cwd=workspace,
        timeout_seconds=timeout_seconds,
        stdin=_prompt(workspace, issue_number, issue_title, issue_url),
    )
    thread_id, usage = _codex_events(result.stdout)
    _require_pr_body(workspace)
    return CodexRun(thread_id, usage, result.duration_seconds)


def fix_with_codex(
    workspace: Path,
    *,
    thread_id: CodexThreadId,
    findings: Findings,
    model: CodexModel,
    effort: ReasoningEffort,
    timeout_seconds: float,
) -> CodexRun:
    """Resume the implementing Codex thread to address review findings."""
    _clear_pr_body(workspace)
    result = run_command(
        (
            "codex",
            "exec",
            "resume",
            "--model",
            model,
            "--config",
            f'model_reasoning_effort="{effort}"',
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            thread_id,
            "-",
        ),
        cwd=workspace,
        timeout_seconds=timeout_seconds,
        stdin=_fix_prompt(findings),
    )
    resumed_thread_id, usage = _codex_events(result.stdout)
    if resumed_thread_id != thread_id:
        raise CodingClientError("Codex resumed a different thread")
    _require_pr_body(workspace)
    return CodexRun(resumed_thread_id, usage, result.duration_seconds)


def review_with_claude(
    workspace: Path,
    *,
    base_branch: str,
    ticket_body: str,
    model: ClaudeModel,
    timeout_seconds: float,
) -> ReviewRun:
    """Run fresh standards and spec reviews in parallel."""
    ticket_path = workspace / TICKET_BODY
    ticket_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        ticket_path.write_text(ticket_body, encoding="utf-8")
    except OSError as exc:
        raise CodingClientError(f"could not write review ticket: {exc}") from exc
    prompts = (
        _standards_prompt(workspace, base_branch),
        _spec_prompt(workspace, base_branch, ticket_path),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                run_command,
                (
                    "claude",
                    "-p",
                    "--model",
                    model,
                    "--permission-mode",
                    "plan",
                    "--permission-prompts",
                    "none",
                    "--allowedTools",
                    "Read,Grep,Glob,Bash(git diff:*),Bash(git log:*),Bash(git show:*)",
                    "--no-session-persistence",
                ),
                cwd=workspace,
                timeout_seconds=timeout_seconds,
                stdin=prompt,
            )
            for prompt in prompts
        ]
        results = [future.result() for future in futures]
    axes = tuple(_findings(result.stdout) for result in results)
    return ReviewRun(
        Findings(
            tuple(finding for axis in axes for finding in axis.hard),
            tuple(finding for axis in axes for finding in axis.suggestions),
            "\n\n".join(axis.raw for axis in axes),
        ),
        max(result.duration_seconds for result in results),
    )


def _prompt(
    workspace: Path,
    issue_number: IssueNumber,
    issue_title: IssueTitle,
    issue_url: IssueUrl,
) -> str:
    implement_ticket = _skill(workspace, "implement-ticket")
    open_pr = _skill(workspace, "open-pr")
    return (
        f"Implement GitHub issue #{issue_number}: {issue_title}\n"
        f"Ticket: {issue_url}\n\n"
        "Follow this implement-ticket skill exactly:\n\n"
        f"{implement_ticket}\n\n"
        f"Before finishing, write the pull request body to {PR_BODY.as_posix()}. "
        "Follow these open-pr body rules, but do not push or open the pull request. "
        "The pipeline owns those operations.\n\n"
        f"{open_pr}\n"
    )


def _fix_prompt(findings: Findings) -> str:
    hard = "\n".join(f"- {finding}" for finding in findings.hard) or "- None"
    suggestions = "\n".join(f"- {finding}" for finding in findings.suggestions) or "- None"
    return (
        "Address every hard finding below. Apply the suggestions listed here once. "
        "Run the relevant tests, commit the fixes, and rewrite .scratch/pr-body.md "
        "for the current change before finishing.\n\n"
        f"Hard findings:\n{hard}\n\nSuggestions:\n{suggestions}\n"
    )


def _standards_prompt(workspace: Path, base_branch: str) -> str:
    review_skill = workspace / ".claude" / "skills" / "adversarial-review" / "SKILL.md"
    smells = workspace / ".claude" / "skills" / "adversarial-review" / "references" / "smells.md"
    return (
        "Review axis: standards.\n"
        f"Repository: {workspace}\n"
        f"Review: git diff origin/{base_branch}...HEAD\n"
        f"Commits: git log origin/{base_branch}..HEAD --oneline\n"
        f"Read {review_skill} and execute only its Standards reviewer brief in step 5. "
        "Do not dispatch another reviewer. "
        "Read AGENTS.md and CODING-STANDARD.md, plus the smell baseline at "
        f"{smells}. Report each documented-standard breach as [hard] and each "
        "baseline smell as [suggestion]. Start every finding with its tag and path:line. "
        "Skip anything tooling enforces. Keep the answer under 400 words. If there are "
        "no findings, answer exactly: No findings\n"
    )


def _spec_prompt(workspace: Path, base_branch: str, ticket_path: Path) -> str:
    review_skill = workspace / ".claude" / "skills" / "adversarial-review" / "SKILL.md"
    return (
        "Review axis: spec.\n"
        f"Repository: {workspace}\n"
        f"Review: git diff origin/{base_branch}...HEAD\n"
        f"Commits: git log origin/{base_branch}..HEAD --oneline\n"
        f"Ticket: {ticket_path}\n"
        f"Read {review_skill} and execute only its Spec reviewer brief in step 5. "
        "Do not dispatch another reviewer. "
        "Report missing, extra, or wrongly implemented requirements. Tag every finding "
        "[hard] and start it with path:line. Quote the ticket requirement. Keep the answer "
        "under 400 words. If there are no findings, answer exactly: No findings\n"
    )


def _findings(source: str) -> Findings:
    body = source.strip()
    if body == "No findings":
        return Findings((), (), body)
    hard: list[str] = []
    suggestions: list[str] = []
    for line in body.splitlines():
        match = _FINDING.match(line)
        if match is None:
            continue
        target = hard if match.group(1).lower() == "hard" else suggestions
        target.append(match.group(2).strip())
    if not hard and not suggestions:
        raise CodingClientError("Claude review returned no tagged findings")
    return Findings(tuple(hard), tuple(suggestions), body)


def _clear_pr_body(workspace: Path) -> None:
    try:
        (workspace / PR_BODY).unlink(missing_ok=True)
    except OSError as exc:
        raise CodingClientError(f"could not clear pull request body: {exc}") from exc


def _require_pr_body(workspace: Path) -> None:
    path = workspace / PR_BODY
    try:
        if not path.read_text(encoding="utf-8").strip():
            raise CodingClientError("Codex did not write a pull request body")
    except OSError as exc:
        raise CodingClientError(f"could not read pull request body: {exc}") from exc


def _skill(workspace: Path, name: str) -> str:
    path = workspace / ".claude" / "skills" / name / "SKILL.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CodingClientError(f"could not read {name} skill: {exc}") from exc


def _codex_events(source: str) -> tuple[CodexThreadId, TokenUsage]:
    lines = [line for line in source.splitlines() if line.strip()]
    if not lines:
        raise CodingClientError("Codex returned no JSON events")
    first = json.loads(lines[0])
    last = json.loads(lines[-1])
    if not isinstance(first, dict) or not isinstance(thread_id := first.get("thread_id"), str):
        raise CodingClientError("Codex's first JSON event has no thread id")
    if not isinstance(last, dict) or not isinstance(usage := last.get("usage"), dict):
        raise CodingClientError("Codex's last JSON event has no usage")
    input_tokens = usage.get("input_tokens")
    cached_input_tokens = usage.get("cached_input_tokens")
    output_tokens = usage.get("output_tokens")
    if (
        not isinstance(input_tokens, int)
        or not isinstance(cached_input_tokens, int)
        or not isinstance(output_tokens, int)
    ):
        raise CodingClientError("Codex's last JSON event has invalid usage")
    return CodexThreadId(thread_id), TokenUsage(input_tokens, cached_input_tokens, output_tokens)
