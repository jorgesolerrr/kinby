"""The coder routine delegates one ready issue through command-line clients."""

import json
import os
import shutil
import sys
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Self

import pytest
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.tools import StructuredTool

from kinby.cli import main
from kinby.core.dispatcher import TurnConfig
from kinby.core.events import EventLog
from kinby.core.turn_runner import LangGraphRunner
from kinby.instance import Instance, load_instance
from tests.helpers import turn_config_stub

INSTANCES = Path(__file__).parents[1] / "instances"
CODER = INSTANCES / "coder"


def _coder_copy(tmp_path: Path) -> Path:
    instance = tmp_path / "coder"
    shutil.copytree(CODER, instance, ignore=shutil.ignore_patterns(".state", ".env", "workspace"))
    workspace = instance / "workspace"
    workspace.mkdir()
    source_skills = INSTANCES.parent / ".claude" / "skills"
    for name in ("implement-ticket", "open-pr"):
        shutil.copytree(source_skills / name, workspace / ".claude" / "skills" / name)
    return instance


class _RoutineModel:
    def __init__(self) -> None:
        self.messages: list[list[BaseMessage]] = []

    def bind_tools(self, tools: Sequence[StructuredTool]) -> Self:
        return self

    async def astream(self, messages: Sequence[BaseMessage]) -> AsyncIterator[AIMessageChunk]:
        self.messages.append(list(messages))
        yield AIMessageChunk(
            content="Commented on the issue.",
            usage_metadata={"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        )


def _use_routine_model(
    monkeypatch: pytest.MonkeyPatch,
    instance: Instance,
    model: _RoutineModel,
) -> None:
    log = EventLog(instance.manifest.state_dir)
    runner = LangGraphRunner(instance, event_log=log, model_factory=lambda _: model)
    monkeypatch.setattr(
        "kinby.core.runtime.turn_config",
        turn_config_stub(
            lambda: TurnConfig(
                runner.prepare_for_turn,
                runner.permission_ceiling,
                runner,
            )
        ),
    )


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(0o755)


def _fake_clients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    binaries = tmp_path / "bin"
    binaries.mkdir()
    canned = tmp_path / "canned"
    canned.mkdir()
    log = tmp_path / "commands.jsonl"
    common = """import json
import os
import sys
from pathlib import Path

arguments = sys.argv[1:]
record = {"command": Path(sys.argv[0]).name, "arguments": arguments, "cwd": os.getcwd()}
responses = Path(os.environ["FACTORY_CANNED_RESPONSES"])
"""
    _write_executable(
        binaries / "gh",
        common
        + """if arguments[:2] == ["issue", "list"]:
    output = (responses / "issues.json").read_text(encoding="utf-8")
elif arguments[:2] == ["pr", "list"]:
    output = (responses / "pull-requests.json").read_text(encoding="utf-8")
elif arguments[:2] == ["repo", "view"]:
    output = (responses / "repository.json").read_text(encoding="utf-8")
elif arguments[:2] == ["pr", "create"]:
    body_path = Path(arguments[arguments.index("--body-file") + 1])
    record["body"] = body_path.read_text(encoding="utf-8")
    output = (responses / "pull-request-url.txt").read_text(encoding="utf-8")
else:
    output = ""
with Path(os.environ["FACTORY_COMMAND_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(record) + "\\n")
if " ".join(arguments).startswith(os.environ.get("FAKE_GH_FAIL", "no failure configured")):
    print("GitHub exploded", file=sys.stderr)
    raise SystemExit(7)
print(output)
""",
    )
    _write_executable(
        binaries / "codex",
        common
        + """import time

record["stdin"] = sys.stdin.read()
record["pid"] = os.getpid()
with Path(os.environ["FACTORY_COMMAND_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(record) + "\\n")
time.sleep(float(os.environ.get("FAKE_CODEX_SLEEP", "0")))
if exit_code := int(os.environ.get("FAKE_CODEX_EXIT", "0")):
    print("Codex exploded", file=sys.stderr)
    raise SystemExit(exit_code)
scratch = Path.cwd() / ".scratch"
scratch.mkdir(exist_ok=True)
(scratch / "pr-body.md").write_text(
    (responses / "pr-body.md").read_text(encoding="utf-8"), encoding="utf-8"
)
print((responses / "codex-events.jsonl").read_text(encoding="utf-8"))
""",
    )
    for command in ("claude", "git", "uv"):
        _write_executable(
            binaries / command,
            common
            + """with Path(os.environ["FACTORY_COMMAND_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(record) + "\\n")
if " ".join(arguments).startswith(os.environ.get("FAKE_COMMAND_FAIL", "no failure configured")):
    print(f"{record['command']} exploded", file=sys.stderr)
    raise SystemExit(7)
""",
        )
    monkeypatch.setenv("PATH", f"{binaries}:{os.environ['PATH']}")
    monkeypatch.setenv("FACTORY_COMMAND_LOG", str(log))
    monkeypatch.setenv("FACTORY_CANNED_RESPONSES", str(canned))
    (canned / "issues.json").write_text(
        json.dumps(
            [
                {"number": 9, "title": "Ninth ticket", "url": "https://example.test/issues/9"},
                {"number": 2, "title": "Second ticket", "url": "https://example.test/issues/2"},
                {"number": 4, "title": "Fourth ticket", "url": "https://example.test/issues/4"},
            ]
        ),
        encoding="utf-8",
    )
    (canned / "pull-requests.json").write_text(
        json.dumps(
            [
                {
                    "number": 20,
                    "url": "https://example.test/pull/20",
                    "headRefName": "agent/2-second-ticket",
                    "body": "Closes #2\n",
                },
                {
                    "number": 21,
                    "url": "https://example.test/pull/21",
                    "headRefName": "feature/human-work",
                    "body": "Closes #4\n",
                },
            ]
        ),
        encoding="utf-8",
    )
    (canned / "repository.json").write_text(
        json.dumps({"owner": {"login": "jorgesolerrr"}, "defaultBranchRef": {"name": "main"}}),
        encoding="utf-8",
    )
    (canned / "pull-request-url.txt").write_text("https://example.test/pull/24", encoding="utf-8")
    (canned / "pr-body.md").write_text(
        "## Summary\n\nImplement the delegated pipeline.\n\n"
        "## Checks\n\nAll repository checks pass.\n",
        encoding="utf-8",
    )
    (canned / "codex-events.jsonl").write_text(
        '{"type":"thread.started","thread_id":"thread-184"}\n'
        '{"type":"turn.completed","usage":{"input_tokens":120,"cached_input_tokens":80,'
        '"output_tokens":35}}',
        encoding="utf-8",
    )
    return log


def _records(log: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _arguments(record: dict[str, object]) -> list[str]:
    arguments = record["arguments"]
    assert isinstance(arguments, list)
    assert all(isinstance(argument, str) for argument in arguments)
    return arguments


def _text(record: dict[str, object], key: str) -> str:
    value = record[key]
    assert isinstance(value, str)
    return value


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _number(value: object) -> int | float:
    assert isinstance(value, int | float)
    return value


def _report(output: str) -> dict[str, object]:
    result_line = next(
        line
        for line in output.splitlines()
        if line.startswith("[tool.result] implement_ready_issue (ok): ")
    )
    value = json.loads(result_line.partition(": ")[2])
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize(
    "delivery",
    [
        {"action": "labeled", "label": {"name": "bug"}},
        {"action": "unlabeled", "label": {"name": "bug"}},
        {"action": "created", "comment": {"body": "hello"}},
        {
            "action": "opened",
            "pull_request": {"head": {"ref": "feature/human-work"}},
        },
    ],
)
def test_irrelevant_payload_returns_no_work_without_github(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    delivery: dict[str, object],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance = _coder_copy(tmp_path)
    payload = tmp_path / "delivery.json"
    payload.write_text(json.dumps(delivery), encoding="utf-8")
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))

    exit_code = main(
        [
            "routine",
            "run",
            "implement-ready-issue",
            "--payload",
            str(payload),
            "--instance",
            str(instance),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[tool.result] implement_ready_issue (ok): None" in captured.out


@pytest.mark.parametrize(
    "delivery",
    [
        {"action": "opened", "issue": {"state": "open", "labels": [{"name": "bug"}]}},
        {"action": "unlabeled", "label": {"name": "ready-for-agent"}},
        {
            "action": "closed",
            "issue": {"state": "closed", "labels": [{"name": "ready-for-agent"}]},
        },
    ],
)
def test_issue_event_scans_github_and_returns_no_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    delivery: dict[str, object],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance = _coder_copy(tmp_path)
    log = _fake_clients(tmp_path, monkeypatch)
    (tmp_path / "canned" / "issues.json").write_text("[]", encoding="utf-8")
    payload = tmp_path / "delivery.json"
    payload.write_text(json.dumps(delivery), encoding="utf-8")

    exit_code = main(
        [
            "routine",
            "run",
            "implement-ready-issue",
            "--payload",
            str(payload),
            "--instance",
            str(instance),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[tool.result] implement_ready_issue (ok): None" in captured.out
    github_calls = [_arguments(record)[:2] for record in _records(log) if record["command"] == "gh"]
    assert github_calls == [
        ["issue", "list"],
        ["pr", "list"],
    ]


def test_ready_issue_runs_codex_checks_and_opens_pull_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    instance = load_instance(instance_path)
    model = _RoutineModel()
    _use_routine_model(monkeypatch, instance, model)
    log = _fake_clients(tmp_path, monkeypatch)
    payload = tmp_path / "delivery.json"
    payload.write_text(
        json.dumps({"action": "labeled", "label": {"name": "ready-for-agent"}}),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "routine",
            "run",
            "implement-ready-issue",
            "--payload",
            str(payload),
            "--instance",
            str(instance_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    report = _report(captured.out)
    assert report["issue"] == {
        "number": 4,
        "title": "Fourth ticket",
        "url": "https://example.test/issues/4",
    }
    assert report["outcome"] == "opened"
    assert report["pull_request"] == {
        "url": "https://example.test/pull/24",
        "branch": "agent/4-fourth-ticket",
        "base_branch": "main",
    }
    assert report["checks"] == {"passed": True, "failed": None}
    codex_report = _mapping(report["codex"])
    assert codex_report["thread_id"] == "thread-184"
    assert codex_report["usage"] == {
        "input_tokens": 120,
        "cached_input_tokens": 80,
        "output_tokens": 35,
    }
    codex_duration = _number(codex_report["duration_seconds"])
    assert codex_duration >= 0
    assert _number(report["duration_seconds"]) >= codex_duration

    records = _records(log)
    codex = next(record for record in records if record["command"] == "codex")
    codex_arguments = _arguments(codex)
    assert "--model" in codex_arguments
    assert "gpt-5.6-sol" in codex_arguments
    assert "--json" in codex_arguments
    assert "--dangerously-bypass-approvals-and-sandbox" in codex_arguments
    assert 'model_reasoning_effort="high"' in codex_arguments
    assert "#4" in _text(codex, "stdin")
    assert "Build test-first" in _text(codex, "stdin")
    assert ".scratch/pr-body.md" in _text(codex, "stdin")

    checks = [_arguments(record) for record in records if record["command"] == "uv"]
    assert checks == [
        ["run", "ruff", "check", "."],
        ["run", "ruff", "format", "--check", "."],
        ["run", "ty", "check"],
        ["run", "pytest"],
    ]
    push_index = next(
        index
        for index, record in enumerate(records)
        if record["command"] == "git" and _arguments(record)[:1] == ["push"]
    )
    last_check = max(index for index, record in enumerate(records) if record["command"] == "uv")
    assert last_check < push_index
    push = records[push_index]
    assert _arguments(push) == ["push", "-u", "origin", "agent/4-fourth-ticket"]

    create = next(
        record
        for record in records
        if record["command"] == "gh" and _arguments(record)[:2] == ["pr", "create"]
    )
    assert _text(create, "body").startswith("Closes #4\n")
    create_arguments = _arguments(create)
    assert create_arguments[create_arguments.index("--reviewer") + 1] == "jorgesolerrr"
    assert len(model.messages) == 1


def test_failing_check_reports_output_and_relabels_issue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    log = _fake_clients(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_COMMAND_FAIL", "run ruff check .")
    payload = tmp_path / "delivery.json"
    payload.write_text(
        json.dumps({"action": "labeled", "label": {"name": "ready-for-agent"}}),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "routine",
                "run",
                "implement-ready-issue",
                "--payload",
                str(payload),
                "--instance",
                str(instance_path),
            ]
        )
        == 0
    )

    report = _report(capsys.readouterr().out)
    assert report["outcome"] == "failed"
    assert report["checks"] == {"passed": False, "failed": "uv run ruff check ."}
    failure_reason = report["failure_reason"]
    assert isinstance(failure_reason, str)
    assert "uv exploded" in failure_reason
    records = _records(log)
    assert not any(
        record["command"] == "git" and _arguments(record)[:1] == ["push"] for record in records
    )
    assert not any(
        record["command"] == "gh" and _arguments(record)[:2] == ["pr", "create"]
        for record in records
    )
    relabel = next(
        record
        for record in records
        if record["command"] == "gh" and _arguments(record)[:2] == ["issue", "edit"]
    )
    assert _arguments(relabel) == [
        "issue",
        "edit",
        "4",
        "--remove-label",
        "ready-for-agent",
        "--add-label",
        "ready-for-human",
    ]


@pytest.mark.parametrize(
    ("environment", "failure"),
    [
        ({"FAKE_CODEX_EXIT": "9"}, "Codex exploded"),
        ({"FAKE_GH_FAIL": "pr create"}, "GitHub exploded"),
    ],
)
def test_client_or_github_failure_relabels_issue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    environment: dict[str, str],
    failure: str,
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    log = _fake_clients(tmp_path, monkeypatch)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    payload = tmp_path / "delivery.json"
    payload.write_text(
        json.dumps({"action": "labeled", "label": {"name": "ready-for-agent"}}),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "routine",
                "run",
                "implement-ready-issue",
                "--payload",
                str(payload),
                "--instance",
                str(instance_path),
            ]
        )
        == 0
    )

    report = _report(capsys.readouterr().out)
    assert report["outcome"] == "failed"
    reason = report["failure_reason"]
    assert isinstance(reason, str)
    assert failure in reason
    records = _records(log)
    assert any(
        record["command"] == "gh" and _arguments(record)[:2] == ["issue", "edit"]
        for record in records
    )


def test_client_overrun_is_killed_and_relabels_issue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    routine = instance_path / "routines" / "implement-ready-issue" / "ROUTINE.md"
    routine.write_text(
        routine.read_text(encoding="utf-8").replace(
            '"implement_timeout_seconds":1800',
            '"implement_timeout_seconds":0.05',
        ),
        encoding="utf-8",
    )
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    log = _fake_clients(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_CODEX_SLEEP", "10")
    payload = tmp_path / "delivery.json"
    payload.write_text(
        json.dumps({"action": "labeled", "label": {"name": "ready-for-agent"}}),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "routine",
                "run",
                "implement-ready-issue",
                "--payload",
                str(payload),
                "--instance",
                str(instance_path),
            ]
        )
        == 0
    )

    report = _report(capsys.readouterr().out)
    assert report["outcome"] == "failed"
    reason = report["failure_reason"]
    assert isinstance(reason, str)
    assert "0.05-second limit and was killed" in reason
    records = _records(log)
    codex = next(record for record in records if record["command"] == "codex")
    pid = codex["pid"]
    assert isinstance(pid, int)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert any(
        record["command"] == "gh" and _arguments(record)[:2] == ["issue", "edit"]
        for record in records
    )
