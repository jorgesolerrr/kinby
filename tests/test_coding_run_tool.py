"""An instance's turn delegates coding work to a coding client signed in on it: a coding run in
the instance's workspace, which the turn waits for and records beside itself."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from kinby.contracts import (
    ApprovalRequested,
    DelegatedRun,
    DelegatedRunOutcome,
    ModelCompleted,
    PermissionMode,
    RunDelegated,
    UsageSource,
)
from kinby.core.events import EventLog
from kinby.instance import Instance
from kinby.plugins import ToolContext, coding_runs
from kinby.plugins.core import core_tools
from tests.test_instance_code import turn
from tests.test_routines import instance_at
from tests.test_workbench_steps import (
    CLAUDE_STREAM,
    CODEX_STREAM,
    recorded_calls,
    streaming,
    stub_client,
)

DELEGATE = "delegate_to_coding_client"


@pytest.fixture(autouse=True)
def installed_nowhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No coding client is installed but the stubs a test puts on PATH, and Codex keeps its
    credentials in the test's own folder."""
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))


def _instance(tmp_path: Path) -> Instance:
    instance = instance_at(tmp_path)
    instance.manifest.workspace.path.mkdir(parents=True, exist_ok=True)
    return instance


def _sign_in_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "token")


def _sign_in_codex(tmp_path: Path) -> None:
    (tmp_path / "codex").mkdir(exist_ok=True)
    (tmp_path / "codex" / "auth.json").write_text("{}")


def _names(instance: Instance) -> set[str]:
    return {tool.name for tool in core_tools(instance, ())}


async def _delegate(
    instance: Instance,
    reported: list[DelegatedRun],
    thread_id: UUID | None = None,
    **arguments: str,
) -> dict:
    async def report(run: DelegatedRun) -> None:
        reported.append(run)

    [tool] = coding_runs.coding_run_tools(instance)
    context = ToolContext(instance=instance, thread_id=thread_id or uuid4(), run_reporter=report)
    return json.loads(await tool.ainvoke(arguments, context))


def test_a_turn_delegates_its_task_to_the_signed_in_client_in_the_workspace(tmp_path, monkeypatch):
    instance = _instance(tmp_path)
    calls = stub_client(tmp_path, monkeypatch, "claude", streaming(CLAUDE_STREAM))
    _sign_in_claude(monkeypatch)
    reported: list[DelegatedRun] = []

    returned = asyncio.run(_delegate(instance, reported, task="Fix the typo in README."))

    [called] = recorded_calls(calls)
    assert called.cwd == str(instance.manifest.workspace.path)
    assert called.stdin == "Fix the typo in README."
    assert called.config_dir == str(instance.manifest.state_dir / "claude")
    assert returned == {
        "client": "claude",
        "ending": "completed",
        "message": "Implemented issue 7.",
        "session": "claude-1",
    }
    [run] = reported
    assert (run.client, run.outcome, run.session) == (
        "claude-code",
        DelegatedRunOutcome.COMPLETED,
        "claude-1",
    )


def test_an_instance_with_no_coding_client_installed_has_no_delegation(tmp_path, monkeypatch):
    _sign_in_claude(monkeypatch)
    _sign_in_codex(tmp_path)

    assert DELEGATE not in _names(_instance(tmp_path))


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_an_instance_whose_client_is_installed_but_not_signed_in_has_no_delegation(
    tmp_path, monkeypatch, client
):
    stub_client(tmp_path, monkeypatch, client, streaming([]))

    assert DELEGATE not in _names(_instance(tmp_path))


def test_claude_signs_in_with_its_token_or_the_credentials_in_the_instances_state(
    tmp_path, monkeypatch
):
    instance = _instance(tmp_path)
    stub_client(tmp_path, monkeypatch, "claude", streaming([]))
    credentials = instance.manifest.state_dir / "claude" / ".credentials.json"
    credentials.parent.mkdir(parents=True)
    credentials.write_text("{}")

    assert DELEGATE in _names(instance)


def test_codex_signs_in_with_the_auth_file_in_its_home(tmp_path, monkeypatch):
    stub_client(tmp_path, monkeypatch, "codex", streaming([]))
    _sign_in_codex(tmp_path)

    assert DELEGATE in _names(_instance(tmp_path))


def test_with_two_clients_signed_in_the_call_names_the_one_it_delegates_to(tmp_path, monkeypatch):
    instance = _instance(tmp_path)
    stub_client(tmp_path, monkeypatch, "claude", streaming(CLAUDE_STREAM))
    codex = stub_client(tmp_path, monkeypatch, "codex", streaming(CODEX_STREAM))
    _sign_in_claude(monkeypatch)
    _sign_in_codex(tmp_path)

    with pytest.raises(ValueError, match="claude, codex"):
        asyncio.run(_delegate(instance, [], task="Fix it."))
    returned = asyncio.run(_delegate(instance, [], task="Fix it.", client="codex"))

    assert len(recorded_calls(codex)) == 1
    assert (returned["client"], returned["session"]) == ("codex", "codex-1")


def test_the_delegation_asks_as_a_write_and_its_approval_names_the_client_and_the_task(
    tmp_path, monkeypatch
):
    instance = _instance(tmp_path)
    calls = stub_client(tmp_path, monkeypatch, "claude", streaming(CLAUDE_STREAM))
    _sign_in_claude(monkeypatch)

    payloads = asyncio.run(
        turn(instance, [(DELEGATE, {"task": "Fix the typo."})], PermissionMode.ASK)
    )

    approval = next(payload for payload in payloads if isinstance(payload, ApprovalRequested))
    assert approval.name == DELEGATE
    assert approval.arguments == {"task": "Fix the typo."}
    assert approval.preview is not None
    assert "claude" in approval.preview and "Fix the typo." in approval.preview
    assert not calls.exists()


def test_the_coding_run_is_recorded_beside_the_turn_and_outside_its_tokens(tmp_path, monkeypatch):
    instance = _instance(tmp_path)
    stub_client(tmp_path, monkeypatch, "claude", streaming(CLAUDE_STREAM))
    _sign_in_claude(monkeypatch)

    payloads = asyncio.run(
        turn(instance, [(DELEGATE, {"task": "Fix the typo."})], PermissionMode.FULL_ACCESS)
    )

    [delegated] = [payload for payload in payloads if isinstance(payload, RunDelegated)]
    called = [payload for payload in payloads if isinstance(payload, ModelCompleted)]
    assert (delegated.run.input_tokens, delegated.step) == (160, None)
    assert called
    assert sum(call.input_tokens + call.output_tokens for call in called) == 0


@pytest.mark.parametrize(
    ("client", "stream", "session", "arguments"),
    [
        ("claude", CLAUDE_STREAM, "claude-1", ["--resume", "claude-1"]),
        ("codex", CODEX_STREAM, "codex-1", ["exec", "resume", "codex-1", "-"]),
    ],
)
def test_a_delegation_given_a_session_continues_it(
    tmp_path, monkeypatch, client, stream, session, arguments
):
    instance = _instance(tmp_path)
    calls = stub_client(tmp_path, monkeypatch, client, streaming(stream))
    if client == "claude":
        _sign_in_claude(monkeypatch)
    else:
        _sign_in_codex(tmp_path)

    returned = asyncio.run(_delegate(instance, [], task="Repair it.", session=session))

    [called] = recorded_calls(calls)
    assert [argument for argument in called.argv if argument in arguments] == arguments
    assert returned["session"] == session


def test_a_continued_session_reports_only_the_tokens_its_own_run_spent(tmp_path, monkeypatch):
    instance = _instance(tmp_path)
    stub_client(tmp_path, monkeypatch, "claude", streaming(CLAUDE_STREAM))
    _sign_in_claude(monkeypatch)
    thread_id = uuid4()
    earlier = DelegatedRun(
        usage_source=UsageSource.CLAUDE_SUBSCRIPTION,
        client="claude-code",
        models=["claude-opus-5-5"],
        input_tokens=100,
        output_tokens=5,
        cache_read_tokens=50,
        duration_ms=1_000,
        client_turns=1,
        outcome=DelegatedRunOutcome.COMPLETED,
        session="claude-1",
    )
    log = EventLog(instance.manifest.state_dir)
    asyncio.run(log.append(thread_id, uuid4(), RunDelegated(run=earlier)))
    reported: list[DelegatedRun] = []

    asyncio.run(_delegate(instance, reported, thread_id, task="Repair it.", session="claude-1"))

    [run] = reported
    assert (
        run.input_tokens,
        run.output_tokens,
        run.cache_read_tokens,
        run.cache_creation_tokens,
    ) == (60, 15, 0, 10)


def test_a_delegation_the_plan_refused_returns_limited_until_its_window_resets(
    tmp_path, monkeypatch
):
    instance = _instance(tmp_path)
    resets_at = datetime(2030, 1, 1, 12, tzinfo=UTC)
    refused = [
        {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "rejected", "resetsAt": int(resets_at.timestamp())},
        },
        {"type": "result", "subtype": "error", "is_error": True, "session_id": "claude-2"},
    ]
    stub_client(tmp_path, monkeypatch, "claude", streaming(refused) + "sys.exit(1)")
    _sign_in_claude(monkeypatch)
    reported: list[DelegatedRun] = []

    returned = asyncio.run(_delegate(instance, reported, task="Fix it."))

    assert (returned["ending"], returned["resets_at"]) == ("limited", resets_at.isoformat())
    assert reported[0].outcome is DelegatedRunOutcome.LIMITED


def test_a_delegation_past_its_timeout_is_killed_and_returns_failed(tmp_path, monkeypatch):
    instance = _instance(tmp_path)
    stub_client(tmp_path, monkeypatch, "codex", f"{streaming(CODEX_STREAM)}time.sleep(5)")
    _sign_in_codex(tmp_path)
    monkeypatch.setattr(coding_runs, "CLIENT_TIMEOUT_SECONDS", 1)
    reported: list[DelegatedRun] = []

    returned = asyncio.run(_delegate(instance, reported, task="Fix it."))

    assert returned["ending"] == "failed"
    assert "codex ran past its 1-second timeout and was killed." in returned["message"]
    assert reported[0].outcome is DelegatedRunOutcome.FAILED


def test_interrupting_the_turn_stops_the_client_and_what_it_started(tmp_path, monkeypatch):
    instance = _instance(tmp_path)
    workspace = instance.manifest.workspace.path
    started = (
        "import subprocess\n"
        "subprocess.Popen([sys.executable, '-c',"
        ' \'import time; time.sleep(1); open("late", "w").close()\'],'
        " start_new_session=True)\n"
        "open('started', 'w').close()\n"
        "time.sleep(1)\n"
        "open('client-late', 'w').close()\n"
    )
    stub_client(tmp_path, monkeypatch, "claude", started)
    _sign_in_claude(monkeypatch)

    async def scenario() -> None:
        delegation = asyncio.create_task(_delegate(instance, [], task="Fix it."))
        while not (workspace / "started").exists():
            await asyncio.sleep(0.05)
        delegation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await delegation
        await asyncio.sleep(1.5)

    asyncio.run(scenario())

    assert not (workspace / "late").exists()
    assert not (workspace / "client-late").exists()
