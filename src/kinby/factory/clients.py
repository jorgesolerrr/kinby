"""Run coding clients and parse their machine-readable results."""

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import NewType

from kinby.factory.process import run_command
from kinby.factory.repository import IssueNumber, IssueTitle, IssueUrl

PR_BODY = Path(".scratch/pr-body.md")
CodexModel = NewType("CodexModel", str)
ClaudeModel = NewType("ClaudeModel", str)
CodexThreadId = NewType("CodexThreadId", str)


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
    return CodexRun(thread_id, usage, result.duration_seconds)


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
