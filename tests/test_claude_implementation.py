"""The public routine uses Claude's subscription and resumes it for check repairs."""

import asyncio
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kinby.cli import main
from kinby.core.receiver import Receiver
from kinby.instance import Serve, load_instance
from tests.test_factory import (
    _arguments,
    _coder_copy,
    _fake_clients,
    _mapping,
    _records,
    _report,
    _RoutineModel,
    _text,
    _use_routine_model,
)
from tests.test_receiver import request
from tests.test_scheduler import FakeClock, runtime
from tests.test_scheduler import call as contract_call


def _prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: str = "claude"
) -> tuple[Path, Path]:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance = _coder_copy(tmp_path, client=client)
    _use_routine_model(monkeypatch, load_instance(instance), _RoutineModel())
    return instance, _fake_clients(tmp_path, monkeypatch)


def _run(instance: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    assert main(["routine", "run", "implement-ready-issue", "--instance", str(instance)]) == 0
    return _report(capsys.readouterr().out)


@pytest.mark.parametrize("client", ["claude", "default"])
@pytest.mark.parametrize("check_failure", [None, "run ruff check ."])
def test_opus_implements_and_repairs_checks_in_the_same_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    client: str,
    check_failure: str | None,
) -> None:
    instance, log = _prepare(tmp_path, monkeypatch, client)
    if check_failure:
        monkeypatch.setenv("FAKE_COMMAND_FAIL_ONCE", check_failure)

    report = _run(instance, capsys)

    assert report["outcome"] == "opened"
    assert report["review"] is None
    assert "codex" not in report
    implementation = _mapping(report["implementation"])
    assert implementation["client"] == "claude"
    assert implementation["thread_id"] == "claude-session-184"
    assert implementation["usage"] == {
        "input_tokens": 120,
        "cached_input_tokens": 80,
        "output_tokens": 35,
    }
    records = _records(log)
    assert not any(record["command"] == "codex" for record in records)
    calls = [record for record in records if record["command"] == "claude"]
    assert len(calls) == (2 if check_failure else 1)
    for call in calls:
        arguments = _arguments(call)
        assert arguments[arguments.index("--model") + 1] == "claude-opus-5"
        assert arguments[arguments.index("--effort") + 1] == "high"
        assert arguments[arguments.index("--output-format") + 1] == "json"
        assert arguments[arguments.index("--permission-mode") + 1] == "acceptEdits"
        assert "--no-session-persistence" not in arguments
        assert call["api_key"] is None
    if check_failure:
        arguments = _arguments(calls[-1])
        assert arguments[arguments.index("--resume") + 1] == "claude-session-184"
        assert _mapping(report["check_fix"])["client"] == "claude"
        assert "uv exploded" in _text(calls[-1], "stdin")
    else:
        assert report["check_fix"] is None
    last_check = max(i for i, record in enumerate(records) if record["command"] == "uv")
    push = next(
        i
        for i, record in enumerate(records)
        if record["command"] == "git" and _arguments(record)[:1] == ["push"]
    )
    assert last_check < push


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"type": "assistant"}, "Claude returned an invalid result"),
        ({"is_error": True, "result": "session failed"}, "Claude failed: session failed"),
        ({"subtype": "error_max_turns", "result": "stopped"}, "Claude failed: stopped"),
        ({"session_id": ""}, "Claude returned no session id"),
        ({"usage": None}, "Claude returned no usage"),
        ({"usage": {"input_tokens": True}}, "Claude returned invalid usage"),
        ({"usage": {"input_tokens": -1}}, "Claude returned invalid usage"),
    ],
)
def test_failed_or_malformed_claude_results_cannot_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    change: dict[str, object],
    reason: str,
) -> None:
    instance, log = _prepare(tmp_path, monkeypatch)
    result_path = tmp_path / "canned" / "claude-result.json"
    result = json.loads(result_path.read_text())
    result.update(change)
    result_path.write_text(json.dumps(result))

    report = _run(instance, capsys)

    assert report["outcome"] == "failed"
    assert reason in str(report["failure_reason"])
    assert not any(
        record["command"] == "git" and _arguments(record)[:1] == ["push"]
        for record in _records(log)
    )
    assert any("ready-for-human" in _arguments(record) for record in _records(log))


@pytest.mark.parametrize(
    ("environment", "reason"),
    [
        ({"FAKE_CLAUDE_IMPLEMENT_EXIT": "7"}, "Claude exploded"),
        ({"FAKE_CLAUDE_WRITE_BODY": "0"}, "could not read pull request body"),
        (
            {"FAKE_CLAUDE_WRONG_SESSION": "1", "FAKE_COMMAND_FAIL_ONCE": "run ruff check ."},
            "Claude resumed a different session",
        ),
        ({"FAKE_CLAUDE_IMPLEMENT_SLEEP": "10"}, "claude exceeded its 0.05-second limit"),
    ],
)
def test_claude_execution_failures_stop_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    environment: dict[str, str],
    reason: str,
) -> None:
    instance, log = _prepare(tmp_path, monkeypatch)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    if "FAKE_CLAUDE_IMPLEMENT_SLEEP" in environment:
        routine = instance / "routines" / "implement-ready-issue" / "ROUTINE.md"
        routine.write_text(
            routine.read_text().replace(
                '"implement_timeout_seconds":1800', '"implement_timeout_seconds":0.05'
            )
        )

    report = _run(instance, capsys)

    assert report["outcome"] == "failed"
    assert reason in str(report["failure_reason"])
    assert not any(
        record["command"] == "git" and _arguments(record)[:1] == ["push"]
        for record in _records(log)
    )


def test_disabled_babysitting_has_no_schedule_and_rejects_signed_deliveries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance_path, log = _prepare(tmp_path, monkeypatch)

    async def scenario() -> None:
        instance = load_instance(instance_path)
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 16, tzinfo=UTC)))
        listed = await contract_call(dispatcher, "routine.list")
        babysit = next(
            routine for routine in listed.routines if routine.name == "babysit-pull-request"
        )
        assert not babysit.enabled
        assert babysit.next_run is None
        receiver = Receiver(Serve("127.0.0.1", 0), dispatcher.scheduler, instance)
        address = await receiver.start()
        body = b'{"action":"submitted"}'
        signature = hmac.new(b"secret", body, "sha256").hexdigest()
        try:
            status, _ = await request(
                address,
                "POST",
                "/signals/babysit-pull-request",
                body=body,
                headers={
                    "X-Hub-Signature-256": f"sha256={signature}",
                    "Content-Type": "application/json",
                },
            )
            assert status == 410
        finally:
            await receiver.stop()

    asyncio.run(scenario())
    assert not log.exists()
