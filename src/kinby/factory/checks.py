"""Run repository checks and one Codex repair attempt."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kinby.factory.clients import (
    CodexModel,
    CodexRun,
    CodexThreadId,
    CodingClientError,
    Findings,
    ReasoningEffort,
    fix_with_codex,
)
from kinby.factory.process import CommandError, run_command

CHECKS = (
    ("uv", "run", "ruff", "check", "."),
    ("uv", "run", "ruff", "format", "--check", "."),
    ("uv", "run", "ty", "check"),
    ("uv", "run", "pytest"),
)
CHECK_TIMEOUT_SECONDS = 900.0


@dataclass(frozen=True)
class ChecksPassed:
    passed: Literal[True] = True
    failed: None = None


@dataclass(frozen=True)
class ChecksFailed:
    failed: str
    passed: Literal[False] = False


class RepositoryCheckFailed(RuntimeError):
    """One required repository check failed."""

    def __init__(self, command: tuple[str, ...], reason: str) -> None:
        self.command = command
        super().__init__(reason)


class ChecksFixFailed(RuntimeError):
    """Repository checks still failed after one Codex fix attempt."""

    def __init__(
        self,
        checks: ChecksFailed,
        codex: CodexRun | None,
        reason: str,
    ) -> None:
        self.checks = checks
        self.codex = codex
        super().__init__(reason)


def run_checks(workspace: Path) -> ChecksPassed:
    """Run all repository checks in their required order."""
    for command in CHECKS:
        try:
            run_command(command, cwd=workspace, timeout_seconds=CHECK_TIMEOUT_SECONDS)
        except CommandError as exc:
            raise RepositoryCheckFailed(command, str(exc)) from exc
    return ChecksPassed()


def run_checks_with_fix(
    workspace: Path,
    *,
    thread_id: CodexThreadId,
    model: CodexModel,
    effort: ReasoningEffort,
    timeout_seconds: float,
) -> tuple[ChecksPassed, CodexRun | None]:
    """Run repository checks and make one Codex fix attempt after a failure."""
    try:
        return run_checks(workspace), None
    except RepositoryCheckFailed as exc:
        checks = ChecksFailed(failed=" ".join(exc.command))
        failure = exc
    try:
        codex = fix_with_codex(
            workspace,
            thread_id=thread_id,
            findings=Findings((str(failure),), (), str(failure)),
            model=model,
            effort=effort,
            timeout_seconds=timeout_seconds,
        )
    except (CommandError, CodingClientError) as fix_error:
        raise ChecksFixFailed(checks, None, str(fix_error)) from fix_error
    try:
        return run_checks(workspace), codex
    except RepositoryCheckFailed as retry_error:
        failed = ChecksFailed(failed=" ".join(retry_error.command))
        raise ChecksFixFailed(failed, codex, str(retry_error)) from retry_error
