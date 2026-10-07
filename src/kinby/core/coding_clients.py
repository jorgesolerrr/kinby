"""Run a coding client in the workspace, and read what its event stream says about the run.

The stream only ever describes the run. Whether the step did its work is its hook's to record.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryFile
from time import monotonic

from kinby.contracts import (
    CodingClient,
    CodingSessionId,
    DelegatedRun,
    DelegatedRunOutcome,
    StepEnding,
    TokenTotals,
    UsageSource,
)

#: Claude Code bills an API key over the subscription login when both are set.
_API_KEY = "ANTHROPIC_API_KEY"
#: How much of a failed client's error output the summary keeps, from the end.
_ERROR_TAIL = 2_000
#: Claude's names for its token counts, in the order _claude_tokens reads them.
_STREAMED_USAGE = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)
_RESULT_USAGE = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
#: Codex names a limit's reset in local time: "try again at 3:05 PM." or
#: "at Sep 26th, 2026 3:05 PM."
_CODEX_RETRY = re.compile(r"try again at ([^.]+)\.")
_ORDINAL_DAY = re.compile(r"(\d+)(?:st|nd|rd|th),")


@dataclass(frozen=True)
class CodingRun:
    """One run of a coding client, however it ended."""

    ending: StepEnding
    #: What the client said last, then why the run did not end clean.
    summary: str
    #: The session the run went on in. None when the stream does not name it.
    session: CodingSessionId | None
    #: The run as the client reports it, None when it never started. A run in a session counts
    #: the session's running total.
    reported: DelegatedRun | None


async def run_coding_client(
    client: CodingClient,
    prompt: str,
    workspace: Path,
    *,
    resume: CodingSessionId | None,
    timeout_seconds: int,
) -> CodingRun:
    """Run *client* in *workspace* with *prompt* on its input, continuing *resume* if given."""
    # Files rather than pipes: a task the client leaves running can hold a pipe open, and what
    # a killed client wrote survives it.
    with TemporaryFile() as stdin, TemporaryFile() as stdout, TemporaryFile() as stderr:
        stdin.write(prompt.encode())
        stdin.seek(0)
        try:
            process = await asyncio.create_subprocess_exec(
                *_command(client, workspace, resume),
                cwd=workspace,
                env=_environment(client),
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                # Its own process group, so whatever the client starts is killed with it.
                start_new_session=True,
            )
        except OSError as exc:
            return CodingRun(StepEnding.FAILED, f"{client} could not start: {exc}", None, None)
        started = monotonic()
        timed_out = False
        try:
            async with asyncio.timeout(timeout_seconds):
                await process.wait()
        except TimeoutError:
            timed_out = True
        finally:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
        duration_ms = round((monotonic() - started) * 1000)
        stdout.seek(0)
        stderr.seek(0)
        output, errors = (
            stdout.read().decode(errors="replace"),
            stderr.read().decode(errors="replace"),
        )
    events = _json_events(output)
    said, reported = (_claude if client is CodingClient.CLAUDE else _codex)(events, duration_ms)
    ran = CodingRun(StepEnding.CLEAN, said, reported.session, reported)
    if timed_out:
        reason = f"{client} ran past its {timeout_seconds}-second timeout and was killed."
        return _ended(ran, StepEnding.TIMED_OUT, reason)
    if process.returncode == 0:
        return ran
    if process.returncode is not None and process.returncode < 0:
        return _ended(
            ran, StepEnding.FAILED, f"{client} was killed by signal {-process.returncode}."
        )
    tail = errors.strip()[-_ERROR_TAIL:]
    reason = f"{client} exited with code {process.returncode}.\n{tail}".strip()
    return _ended(ran, StepEnding.FAILED, reason)


def own_tokens(run: DelegatedRun, earlier: Iterable[DelegatedRun]) -> DelegatedRun:
    """*run* with only its own tokens: what the earlier runs of its session counted comes off.

    A client counts a session's running total, so a run that continues a session reports less
    than the client read.
    """
    if run.session is None:
        return run
    before = [other for other in earlier if other.session == run.session]
    return run.model_copy(
        update={
            name: max(getattr(run, name) - sum(getattr(other, name) for other in before), 0)
            for name in TokenTotals.model_fields
        }
    )


def _command(
    client: CodingClient, workspace: Path, resume: CodingSessionId | None
) -> tuple[str, ...]:
    """The client's non-interactive command line. The prompt goes on its input."""
    match client:
        case CodingClient.CLAUDE:
            command = (
                "claude",
                "-p",
                "--permission-mode",
                "acceptEdits",
                "--permission-prompts",
                "none",
                "--allowedTools",
                "Read,Write,Edit,Bash,Glob,Grep,Skill",
                "--output-format",
                "stream-json",
                "--verbose",
            )
            return command if resume is None else (*command, "--resume", resume)
        case CodingClient.CODEX:
            options = ("--json", "--dangerously-bypass-approvals-and-sandbox")
            if resume is None:
                return ("codex", "exec", *options, "--cd", str(workspace), "-")
            return ("codex", "exec", "resume", *options, resume, "-")


def _environment(client: CodingClient) -> Mapping[str, str] | None:
    """Claude runs on its subscription login, never on an API key the instance holds."""
    if client is CodingClient.CLAUDE:
        return {name: value for name, value in os.environ.items() if name != _API_KEY}
    return None


def _ended(ran: CodingRun, ending: StepEnding, reason: str) -> CodingRun:
    """*ran* ending as *ending*, with *reason* after what the client said."""
    summary = f"{ran.summary}\n{reason}" if ran.summary else reason
    return CodingRun(ending, summary, ran.session, ran.reported)


def _claude(events: list[dict[str, object]], duration_ms: int) -> tuple[str, DelegatedRun]:
    """What Claude said last, and its run as its stream reports it, whatever state it is in.

    Tokens come from the result's ``modelUsage``, which covers subagents. A stream cut off before
    its result counts the messages it carried, which are no session's running total, so that run
    names no session.
    """
    result = next((event for event in reversed(events) if event.get("type") == "result"), None)
    if result is None:
        messages = {
            message_id: message
            for event in events
            if event.get("type") == "assistant"
            and isinstance(message := event.get("message"), dict)
            and isinstance(message_id := message.get("id"), str)
        }
        counted = [
            (model, _claude_tokens(usage, _STREAMED_USAGE))
            for message in messages.values()
            if isinstance(model := message.get("model"), str)
            and isinstance(usage := message.get("usage"), dict)
        ]
        said, session, turns, succeeded = "", None, len(messages), False
    else:
        by_model = result.get("modelUsage")
        counted = [
            (model, _claude_tokens(usage, _RESULT_USAGE))
            for model, usage in (by_model.items() if isinstance(by_model, dict) else ())
            if isinstance(usage, dict)
        ]
        said = result.get("result")
        said = said.strip() if isinstance(said, str) else ""
        session = result.get("session_id")
        session = CodingSessionId(session) if isinstance(session, str) and session else None
        turns = _count(result.get("num_turns"))
        succeeded = result.get("is_error") is False and result.get("subtype") == "success"
    resets_at = next(
        (
            datetime.fromtimestamp(reset, UTC)
            for event in events
            if event.get("type") == "rate_limit_event"
            and isinstance(info := event.get("rate_limit_info"), dict)
            and info.get("status") == "rejected"
            and type(reset := info.get("resetsAt")) is int
        ),
        None,
    )
    return said, _reported(
        UsageSource.CLAUDE_SUBSCRIPTION,
        "claude-code",
        [model for model, tokens in counted if tokens.total],
        [tokens for _, tokens in counted],
        duration_ms=duration_ms,
        client_turns=turns,
        succeeded=succeeded,
        resets_at=resets_at,
        session=session,
    )


def _claude_tokens(usage: dict[object, object], names: tuple[str, str, str, str]) -> TokenTotals:
    """One model's counts as Claude names them: uncached input, output, cache read and created.

    kinby's input counts cached input too.
    """
    uncached, output, read, created = (_count(usage.get(name)) for name in names)
    return TokenTotals(
        input_tokens=uncached + read + created,
        output_tokens=output,
        cache_read_tokens=read,
        cache_creation_tokens=created,
    )


def _codex(events: list[dict[str, object]], duration_ms: int) -> tuple[str, DelegatedRun]:
    """What Codex said last, and its run as its stream reports it, whatever state it is in.

    ``turn.completed`` counts the thread's running total, its input counting cached input too.
    The stream never names the model.
    """
    session = next(
        (
            CodingSessionId(thread)
            for event in events
            if event.get("type") == "thread.started"
            and isinstance(thread := event.get("thread_id"), str)
            and thread
        ),
        None,
    )
    messages = [
        text.strip()
        for event in events
        if event.get("type") == "item.completed"
        and isinstance(item := event.get("item"), dict)
        and item.get("type") == "agent_message"
        and isinstance(text := item.get("text"), str)
    ]
    completed = [event for event in events if event.get("type") == "turn.completed"]
    failures = [
        message
        for event in events
        if event.get("type") in {"turn.failed", "error"}
        and isinstance(
            message := (
                error.get("message")
                if isinstance(error := event.get("error"), dict)
                else event.get("message")
            ),
            str,
        )
    ]
    usage = completed[-1].get("usage") if completed else None
    counted = (
        [
            TokenTotals(
                input_tokens=_count(usage.get("input_tokens")),
                output_tokens=_count(usage.get("output_tokens")),
                cache_read_tokens=_count(usage.get("cached_input_tokens")),
                cache_creation_tokens=_count(usage.get("cache_write_input_tokens")),
            )
        ]
        if isinstance(usage, dict)
        else []
    )
    limited = (_codex_reset(message) for message in failures if "usage limit" in message.lower())
    return messages[-1] if messages else "", _reported(
        UsageSource.CHATGPT_SUBSCRIPTION,
        "codex",
        [],
        counted,
        duration_ms=duration_ms,
        client_turns=sum(
            event.get("type") in {"turn.completed", "turn.failed"} for event in events
        ),
        succeeded=bool(completed) and not failures,
        resets_at=next((reset for reset in limited if reset is not None), None),
        session=session,
    )


def _codex_reset(message: str) -> datetime | None:
    """The reset time a Codex usage-limit message names, read in this machine's time zone."""
    match = _CODEX_RETRY.search(message)
    if match is None:
        return None
    stamp = _ORDINAL_DAY.sub(r"\1,", match.group(1).strip())
    with suppress(ValueError):
        return datetime.strptime(stamp, "%b %d, %Y %I:%M %p").astimezone()
    try:
        at = datetime.strptime(stamp, "%I:%M %p").time()
    except ValueError:
        return None
    return datetime.combine(date.today(), at).astimezone()


def _reported(
    source: UsageSource,
    client: str,
    models: list[str],
    counted: list[TokenTotals],
    *,
    duration_ms: int,
    client_turns: int,
    succeeded: bool,
    resets_at: datetime | None,
    session: CodingSessionId | None,
) -> DelegatedRun:
    """The delegated run that *counted* the tokens. One the plan refused is limited.

    A limit without a reset time counts as a failure: kinby has to say when the window resets.
    """
    if succeeded:
        outcome = DelegatedRunOutcome.COMPLETED
    elif resets_at is not None:
        outcome = DelegatedRunOutcome.LIMITED
    else:
        outcome = DelegatedRunOutcome.FAILED
    return DelegatedRun(
        usage_source=source,
        client=client,
        models=sorted(set(models)),
        input_tokens=sum(tokens.input_tokens for tokens in counted),
        output_tokens=sum(tokens.output_tokens for tokens in counted),
        cache_read_tokens=sum(tokens.cache_read_tokens for tokens in counted),
        cache_creation_tokens=sum(tokens.cache_creation_tokens for tokens in counted),
        duration_ms=duration_ms,
        client_turns=client_turns,
        outcome=outcome,
        resets_at=resets_at if outcome is DelegatedRunOutcome.LIMITED else None,
        session=session,
    )


def _count(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


def _json_events(source: str) -> list[dict[str, object]]:
    """Every JSON object line in a client's event stream, skipping anything else."""
    events: list[dict[str, object]] = []
    for line in source.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events
