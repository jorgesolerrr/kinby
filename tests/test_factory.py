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
    for name in ("adversarial-review", "implement-ticket", "open-pr"):
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
        + """if arguments[:1] == ["api"]:
    endpoint = next((argument for argument in arguments if argument.startswith("repos/")), "")
    if endpoint.endswith("/issues"):
        output = (responses / "issues.json").read_text(encoding="utf-8")
    elif endpoint.endswith("/pulls"):
        output = (responses / "pull-requests.json").read_text(encoding="utf-8")
    elif endpoint.endswith("/dependencies/blocked_by"):
        issue = endpoint.split("/")[-3]
        path = responses / f"blockers-{issue}.json"
        output = path.read_text(encoding="utf-8") if path.exists() else "[]"
    elif "/issues/" in endpoint and "--jq" not in arguments:
        issue = endpoint.rsplit("/", 1)[-1]
        output = (responses / f"issue-{issue}.json").read_text(encoding="utf-8")
    else:
        output = (responses / "issue-body.md").read_text(encoding="utf-8")
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
sleep_variable = (
    "FAKE_CODEX_RESUME_SLEEP" if "resume" in arguments else "FAKE_CODEX_SLEEP"
)
time.sleep(float(os.environ.get(sleep_variable, "0")))
if exit_code := int(os.environ.get("FAKE_CODEX_EXIT", "0")):
    print("Codex exploded", file=sys.stderr)
    raise SystemExit(exit_code)
if os.environ.get("FAKE_CODEX_WRITE_BODY", "1") == "1":
    scratch = Path.cwd() / ".scratch"
    scratch.mkdir(exist_ok=True)
    (scratch / "pr-body.md").write_text(
        (responses / "pr-body.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
print((responses / "codex-events.jsonl").read_text(encoding="utf-8"))
""",
    )
    _write_executable(
        binaries / "claude",
        common
        + """import time

prompt = sys.stdin.read()
record["stdin"] = prompt
record["api_key"] = os.environ.get("ANTHROPIC_API_KEY")
with Path(os.environ["FACTORY_COMMAND_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(record) + "\\n")
if os.environ.get("FAKE_CLAUDE_REQUIRE_PARALLEL") == "1":
    axis = "standards" if "Review axis: standards" in prompt else "spec"
    peer = "spec" if axis == "standards" else "standards"
    (responses / f"started-{axis}").touch()
    deadline = time.monotonic() + 1
    while not (responses / f"started-{peer}").exists():
        if time.monotonic() >= deadline:
            raise SystemExit(8)
        time.sleep(0.01)
time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "0")))
axis = "standards" if "Review axis: standards" in prompt else "spec"
records = [
    json.loads(line)
    for line in Path(os.environ["FACTORY_COMMAND_LOG"]).read_text(encoding="utf-8").splitlines()
]
fixes = sum(
    item["command"] == "codex" and "resume" in item["arguments"] for item in records
)
numbered = responses / f"review-{axis}-{fixes}.md"
path = numbered if numbered.exists() else responses / f"review-{axis}.md"
print(path.read_text(encoding="utf-8"))
""",
    )
    for command in ("git", "uv"):
        _write_executable(
            binaries / command,
            common
            + """with Path(os.environ["FACTORY_COMMAND_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(record) + "\\n")
joined = " ".join(arguments)
fail_once = os.environ.get("FAKE_COMMAND_FAIL_ONCE", "no one-shot failure configured")
records = [
    json.loads(line)
    for line in Path(os.environ["FACTORY_COMMAND_LOG"]).read_text(encoding="utf-8").splitlines()
]
matching_calls = sum(
    item["command"] == record["command"]
    and " ".join(item["arguments"]).startswith(fail_once)
    for item in records
)
if joined.startswith(os.environ.get("FAKE_COMMAND_FAIL", "no failure configured")) or (
    joined.startswith(fail_once) and matching_calls == 1
):
    print(f"{record['command']} exploded", file=sys.stderr)
    raise SystemExit(7)
""",
        )
    monkeypatch.setenv("PATH", f"{binaries}:{os.environ['PATH']}")
    monkeypatch.setenv("FACTORY_COMMAND_LOG", str(log))
    monkeypatch.setenv("FACTORY_CANNED_RESPONSES", str(canned))
    # The instance's own key must never reach the reviewer's subscription login.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-instance-key")
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
    (canned / "issue-body.md").write_text(
        "Build the fourth ticket and cover it with tests.\n", encoding="utf-8"
    )
    for axis in ("standards", "spec"):
        (canned / f"review-{axis}.md").write_text("No findings", encoding="utf-8")
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


def _run_labeled_delivery(instance: Path, tmp_path: Path, issue: int) -> int:
    payload = tmp_path / "delivery.json"
    payload.write_text(
        json.dumps(
            {
                "action": "labeled",
                "label": {"name": "ready-for-agent"},
                "issue": {"number": issue},
            }
        ),
        encoding="utf-8",
    )
    return main(
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


def _canned_issue(canned: Path, number: int, labels: tuple[str, ...]) -> None:
    (canned / f"issue-{number}.json").write_text(
        json.dumps(
            {
                "number": number,
                "title": f"Issue {number}",
                "html_url": f"https://example.test/issues/{number}",
                "state": "open",
                "labels": [{"name": label} for label in labels],
            }
        ),
        encoding="utf-8",
    )


def _single_issue_reads(log: Path, number: int) -> list[list[str]]:
    return [
        _arguments(record)
        for record in _records(log)
        if record["command"] == "gh"
        and any(argument.endswith(f"/issues/{number}") for argument in _arguments(record))
        and "--jq" not in _arguments(record)
    ]


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
    github_calls = [_arguments(record)[:1] for record in _records(log) if record["command"] == "gh"]
    assert github_calls == [
        ["api"],
        ["api"],
    ]


def test_labeled_issue_missing_from_list_runs_pipeline_after_single_issue_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    log = _fake_clients(tmp_path, monkeypatch)
    canned = tmp_path / "canned"
    (canned / "issues.json").write_text("[]", encoding="utf-8")
    _canned_issue(canned, 4, ("ready-for-agent",))

    assert _run_labeled_delivery(instance_path, tmp_path, 4) == 0

    assert _mapping(_report(capsys.readouterr().out)["issue"])["number"] == 4
    assert len(_single_issue_reads(log, 4)) == 1


def test_labeled_issue_with_removed_label_returns_no_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    log = _fake_clients(tmp_path, monkeypatch)
    canned = tmp_path / "canned"
    (canned / "issues.json").write_text("[]", encoding="utf-8")
    _canned_issue(canned, 4, ())

    assert _run_labeled_delivery(instance_path, tmp_path, 4) == 0

    assert "[tool.result] implement_ready_issue (ok): None" in capsys.readouterr().out
    assert len(_single_issue_reads(log, 4)) == 1


def test_labeled_issue_already_in_list_is_not_read_again_or_duplicated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    log = _fake_clients(tmp_path, monkeypatch)

    assert _run_labeled_delivery(instance_path, tmp_path, 4) == 0

    assert _mapping(_report(capsys.readouterr().out)["issue"])["number"] == 4
    assert _single_issue_reads(log, 4) == []


def test_lower_labeled_issue_missing_from_list_wins_over_listed_issue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    _fake_clients(tmp_path, monkeypatch)
    canned = tmp_path / "canned"
    _canned_issue(canned, 1, ("ready-for-agent",))

    assert _run_labeled_delivery(instance_path, tmp_path, 1) == 0

    assert _mapping(_report(capsys.readouterr().out)["issue"])["number"] == 1


def test_scan_paginates_all_results_and_skips_an_uncovered_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    log = _fake_clients(tmp_path, monkeypatch)
    (tmp_path / "canned" / "blockers-4.json").write_text(
        json.dumps(
            [
                {
                    "number": 7,
                    "state": "open",
                    "parent_issue_url": "https://api.example.test/issues/180",
                }
            ]
        ),
        encoding="utf-8",
    )
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

    assert _mapping(_report(capsys.readouterr().out)["issue"])["number"] == 9
    list_calls = [
        _arguments(record)
        for record in _records(log)
        if record["command"] == "gh"
        and "api" in _arguments(record)
        and any(argument.endswith(("/issues", "/pulls")) for argument in _arguments(record))
    ]
    assert len(list_calls) == 2
    assert all("--paginate" in arguments for arguments in list_calls)
    assert all("--slurp" in arguments for arguments in list_calls)


def test_blocker_with_an_agent_pr_counts_only_inside_the_same_stack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    _fake_clients(tmp_path, monkeypatch)
    canned = tmp_path / "canned"
    issues = json.loads((canned / "issues.json").read_text(encoding="utf-8"))
    assert isinstance(issues, list)
    for issue in issues:
        if isinstance(issue, dict) and issue.get("number") == 4:
            issue["parent_issue_url"] = "https://api.example.test/issues/180"
    (canned / "issues.json").write_text(json.dumps(issues), encoding="utf-8")
    (canned / "blockers-4.json").write_text(
        json.dumps(
            [
                {
                    "number": 2,
                    "state": "open",
                    "parent_issue_url": "https://api.example.test/issues/180",
                }
            ]
        ),
        encoding="utf-8",
    )
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

    issue = _mapping(_report(capsys.readouterr().out)["issue"])
    assert issue["number"] == 4
    assert issue["parent"] == 180


def test_agent_pull_request_wake_stacks_a_sub_issue_on_its_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    log = _fake_clients(tmp_path, monkeypatch)
    canned = tmp_path / "canned"
    issues = json.loads((canned / "issues.json").read_text(encoding="utf-8"))
    assert isinstance(issues, list)
    for issue in issues:
        if isinstance(issue, dict) and issue.get("number") in {2, 4}:
            issue["parent_issue_url"] = "https://api.example.test/issues/180"
    (canned / "issues.json").write_text(json.dumps(issues), encoding="utf-8")
    payload = tmp_path / "delivery.json"
    payload.write_text(
        json.dumps(
            {
                "action": "opened",
                "pull_request": {"head": {"ref": "agent/2-second-ticket"}},
            }
        ),
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
    assert _mapping(report["pull_request"])["base_branch"] == "agent/2-second-ticket"
    records = _records(log)
    git_calls = [_arguments(record) for record in records if record["command"] == "git"]
    assert ["fetch", "origin"] in git_calls
    assert [
        "switch",
        "--discard-changes",
        "-C",
        "agent/4-fourth-ticket",
        "origin/agent/2-second-ticket",
    ] in git_calls
    create = next(
        record
        for record in records
        if record["command"] == "gh" and _arguments(record)[:2] == ["pr", "create"]
    )
    create_arguments = _arguments(create)
    assert create_arguments[create_arguments.index("--base") + 1] == "agent/2-second-ticket"
    stack = next(
        record
        for record in records
        if record["command"] == "gh" and "repos/{owner}/{repo}/stacks" in _arguments(record)
    )
    assert _arguments(stack) == [
        "api",
        "--method",
        "POST",
        "-H",
        "X-GitHub-Api-Version: 2026-03-10",
        "repos/{owner}/{repo}/stacks",
        "-F",
        "pull_requests[]=20",
        "-F",
        "pull_requests[]=24",
    ]


def test_stack_registration_failure_warns_after_the_pull_request_opens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    log = _fake_clients(tmp_path, monkeypatch)
    canned = tmp_path / "canned"
    issues = json.loads((canned / "issues.json").read_text(encoding="utf-8"))
    assert isinstance(issues, list)
    for issue in issues:
        if isinstance(issue, dict) and issue.get("number") in {2, 4}:
            issue["parent_issue_url"] = "https://api.example.test/issues/180"
    (canned / "issues.json").write_text(json.dumps(issues), encoding="utf-8")
    monkeypatch.setenv("FAKE_GH_FAIL", "api --method POST")
    payload = tmp_path / "delivery.json"
    payload.write_text(
        json.dumps(
            {
                "action": "opened",
                "pull_request": {"head": {"ref": "agent/2-second-ticket"}},
            }
        ),
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
    assert report["outcome"] == "opened"
    warnings = report["warnings"]
    assert isinstance(warnings, list)
    assert len(warnings) == 1
    assert "GitHub exploded" in str(warnings[0])
    assert not any(
        record["command"] == "gh" and _arguments(record)[:2] == ["issue", "edit"]
        for record in _records(log)
    )


def test_sub_issue_extends_the_stack_from_its_most_recent_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    log = _fake_clients(tmp_path, monkeypatch)
    canned = tmp_path / "canned"
    issues = json.loads((canned / "issues.json").read_text(encoding="utf-8"))
    assert isinstance(issues, list)
    issues.append(
        {
            "number": 3,
            "title": "Third ticket",
            "url": "https://example.test/issues/3",
            "parent_issue_url": "https://api.example.test/issues/180",
        }
    )
    for issue in issues:
        if isinstance(issue, dict) and issue.get("number") in {2, 4}:
            issue["parent_issue_url"] = "https://api.example.test/issues/180"
    (canned / "issues.json").write_text(json.dumps(issues), encoding="utf-8")
    (canned / "pull-requests.json").write_text(
        json.dumps(
            [
                {
                    "number": 22,
                    "url": "https://example.test/pull/22",
                    "headRefName": "agent/3-third-ticket",
                    "body": "Closes #3\n",
                    "stack": {"number": 42},
                },
                {
                    "number": 20,
                    "url": "https://example.test/pull/20",
                    "headRefName": "agent/2-second-ticket",
                    "body": "Closes #2\n",
                    "stack": {"number": 42},
                },
            ]
        ),
        encoding="utf-8",
    )
    payload = tmp_path / "delivery.json"
    payload.write_text(
        json.dumps(
            {
                "action": "opened",
                "pull_request": {"head": {"ref": "agent/3-third-ticket"}},
            }
        ),
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
    assert _mapping(report["pull_request"])["base_branch"] == "agent/3-third-ticket"
    pull_list = next(
        record
        for record in _records(log)
        if record["command"] == "gh"
        and any(argument.endswith("/pulls") for argument in _arguments(record))
    )
    assert "sort=created" in _arguments(pull_list)
    assert "direction=desc" in _arguments(pull_list)
    stack = next(
        record
        for record in _records(log)
        if record["command"] == "gh" and "/stacks/42/add" in " ".join(_arguments(record))
    )
    assert _arguments(stack) == [
        "api",
        "--method",
        "POST",
        "-H",
        "X-GitHub-Api-Version: 2026-03-10",
        "repos/{owner}/{repo}/stacks/42/add",
        "-F",
        "pull_requests[]=24",
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
    monkeypatch.setenv("FAKE_CLAUDE_REQUIRE_PARALLEL", "1")
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
        "parent": None,
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
    review = _mapping(report["review"])
    rounds = review["rounds"]
    assert isinstance(rounds, list)
    assert len(rounds) == 1
    assert _mapping(review["open_findings"])["hard"] == []

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

    reviews = [record for record in records if record["command"] == "claude"]
    assert len(reviews) == 2
    assert all("--permission-mode" in _arguments(record) for record in reviews)
    assert all("--allowedTools" in _arguments(record) for record in reviews)
    assert all("claude-fable-5-1" in _arguments(record) for record in reviews)
    assert all("plan" in _arguments(record) for record in reviews)
    assert all("none" in _arguments(record) for record in reviews)
    assert all("--no-session-persistence" in _arguments(record) for record in reviews)
    assert all(record["api_key"] is None for record in reviews)
    assert {"standards", "spec"} == {
        "standards" if "Review axis: standards" in _text(record, "stdin") else "spec"
        for record in reviews
    }
    standards = next(
        record for record in reviews if "Review axis: standards" in _text(record, "stdin")
    )
    assert "AGENTS.md" in _text(standards, "stdin")
    assert "CODING-STANDARD.md" in _text(standards, "stdin")
    assert "references/smells.md" in _text(standards, "stdin")
    spec = next(record for record in reviews if "Review axis: spec" in _text(record, "stdin"))
    assert ".scratch/factory-ticket.md" in _text(spec, "stdin")
    assert (instance_path / "workspace" / ".scratch" / "factory-ticket.md").read_text(
        encoding="utf-8"
    ) == "Build the fourth ticket and cover it with tests."

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
    assert not any(
        record["command"] == "gh" and "repos/{owner}/{repo}/stacks" in _arguments(record)
        for record in records
    )
    assert len(model.messages) == 1


def test_hard_review_finding_resumes_codex_and_reviews_the_fix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    log = _fake_clients(tmp_path, monkeypatch)
    canned = tmp_path / "canned"
    (canned / "review-standards-0.md").write_text(
        "[hard] src/example.py:12 violates CODING-STANDARD.md\n",
        encoding="utf-8",
    )
    (canned / "review-standards-1.md").write_text("No findings", encoding="utf-8")
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
    rounds = _mapping(report["review"])["rounds"]
    assert isinstance(rounds, list)
    assert len(rounds) == 2
    first_round = _mapping(rounds[0])
    assert first_round["hard_count"] == 1
    assert first_round["suggestion_count"] == 0
    assert first_round["fix_usage"] == {
        "input_tokens": 120,
        "cached_input_tokens": 80,
        "output_tokens": 35,
    }
    codex_runs = [record for record in _records(log) if record["command"] == "codex"]
    assert len(codex_runs) == 2
    assert "resume" in _arguments(codex_runs[1])
    assert "src/example.py:12" in _text(codex_runs[1], "stdin")


def test_open_review_findings_are_reported_after_the_round_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    log = _fake_clients(tmp_path, monkeypatch)
    (tmp_path / "canned" / "review-standards.md").write_text(
        "[hard] src/example.py:12 remains broken\n",
        encoding="utf-8",
    )
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
    assert report["outcome"] == "opened_with_findings"
    review = _mapping(report["review"])
    rounds = review["rounds"]
    assert isinstance(rounds, list)
    assert len(rounds) == 3
    assert _mapping(review["open_findings"])["hard"] == ["src/example.py:12 remains broken"]
    create = next(
        record
        for record in _records(log)
        if record["command"] == "gh" and _arguments(record)[:2] == ["pr", "create"]
    )
    assert "## Open review findings" in _text(create, "body")


def test_suggestions_get_one_fix_then_remain_for_the_pull_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    log = _fake_clients(tmp_path, monkeypatch)
    canned = tmp_path / "canned"
    for fix in (0, 1):
        (canned / f"review-standards-{fix}.md").write_text(
            "[suggestion] src/example.py:12 possible Mysterious Name\n",
            encoding="utf-8",
        )
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
    assert report["outcome"] == "opened_with_findings"
    review = _mapping(report["review"])
    rounds = review["rounds"]
    assert isinstance(rounds, list)
    assert len(rounds) == 2
    assert _mapping(rounds[0])["suggestion_count"] == 1
    assert _mapping(rounds[0])["fix_usage"] is not None
    assert _mapping(rounds[1])["suggestion_count"] == 1
    assert _mapping(rounds[1])["fix_usage"] is None
    assert _mapping(review["open_findings"])["suggestions"] == [
        "src/example.py:12 possible Mysterious Name"
    ]
    records = _records(log)
    assert len([record for record in records if record["command"] == "codex"]) == 2
    assert len([record for record in records if record["command"] == "claude"]) == 4


def test_codex_must_replace_a_stale_pull_request_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    stale_body = instance_path / "workspace" / ".scratch" / "pr-body.md"
    stale_body.parent.mkdir()
    stale_body.write_text("Stale body from another issue\n", encoding="utf-8")
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    log = _fake_clients(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_CODEX_WRITE_BODY", "0")
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
    assert "could not read pull request body" in str(report["failure_reason"])
    records = _records(log)
    assert not any(
        record["command"] == "gh" and _arguments(record)[:2] == ["pr", "create"]
        for record in records
    )
    assert any(
        record["command"] == "git" and _arguments(record)[:2] == ["branch", "-D"]
        for record in records
    )


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
    codex_runs = [record for record in records if record["command"] == "codex"]
    assert len(codex_runs) == 2
    assert "resume" in _arguments(codex_runs[1])
    assert "uv exploded" in _text(codex_runs[1], "stdin")
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


def test_check_fix_is_reviewed_before_the_pull_request_opens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    log = _fake_clients(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_COMMAND_FAIL_ONCE", "run ruff check .")
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
    assert report["outcome"] == "opened"
    assert report["check_fix"] is not None
    rounds = _mapping(report["review"])["rounds"]
    assert isinstance(rounds, list)
    assert len(rounds) == 2
    records = _records(log)
    assert len([record for record in records if record["command"] == "claude"]) == 4
    last_check = max(index for index, record in enumerate(records) if record["command"] == "uv")
    final_reviews = [
        index
        for index, record in enumerate(records)
        if record["command"] == "claude" and index > last_check
    ]
    assert len(final_reviews) == 2
    create_index = next(
        index
        for index, record in enumerate(records)
        if record["command"] == "gh" and _arguments(record)[:2] == ["pr", "create"]
    )
    assert max(final_reviews) < create_index


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


def test_review_run_uses_the_review_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    routine = instance_path / "routines" / "implement-ready-issue" / "ROUTINE.md"
    routine.write_text(
        routine.read_text(encoding="utf-8").replace(
            '"review_timeout_seconds":900',
            '"review_timeout_seconds":0.05',
        ),
        encoding="utf-8",
    )
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    _fake_clients(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "10")
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
    assert "claude exceeded its 0.05-second limit" in str(report["failure_reason"])


def test_fix_run_uses_the_fix_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    routine = instance_path / "routines" / "implement-ready-issue" / "ROUTINE.md"
    routine.write_text(
        routine.read_text(encoding="utf-8").replace(
            '"fix_timeout_seconds":900',
            '"fix_timeout_seconds":0.05',
        ),
        encoding="utf-8",
    )
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    log = _fake_clients(tmp_path, monkeypatch)
    (tmp_path / "canned" / "review-standards.md").write_text(
        "[hard] src/example.py:12 remains broken\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_CODEX_RESUME_SLEEP", "10")
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
    assert "codex exceeded its 0.05-second limit" in str(report["failure_reason"])
    codex_runs = [record for record in _records(log) if record["command"] == "codex"]
    assert len(codex_runs) == 2
    assert "resume" in _arguments(codex_runs[1])


@pytest.mark.parametrize(
    "answer",
    [
        "**No findings**\n\n**Reviewer**: Claude, origin/main, 0 hard / 0 suggestion. Clean\n",
        "No findings.\n",
    ],
)
def test_decorated_no_findings_answer_opens_a_clean_pull_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answer: str,
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    log = _fake_clients(tmp_path, monkeypatch)
    (tmp_path / "canned" / "review-standards.md").write_text(answer, encoding="utf-8")
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
    assert report["outcome"] == "opened"
    assert _mapping(_mapping(report["review"])["open_findings"])["hard"] == []
    codex_runs = [record for record in _records(log) if record["command"] == "codex"]
    assert len(codex_runs) == 1
    prompts = [_text(record, "stdin") for record in _records(log) if record["command"] == "claude"]
    assert len(prompts) == 2
    assert all(
        "Do not add headings, a summary, or a Reviewer line." in prompt for prompt in prompts
    )


def test_bold_review_tag_resumes_codex_like_a_plain_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    log = _fake_clients(tmp_path, monkeypatch)
    canned = tmp_path / "canned"
    (canned / "review-standards-0.md").write_text(
        "## Standards\n\n- **[hard]** src/example.py:12 violates CODING-STANDARD.md\n\n"
        "**Reviewer**: Claude, 1 hard / 0 suggestion. Not clean\n",
        encoding="utf-8",
    )
    (canned / "review-standards-1.md").write_text("No findings", encoding="utf-8")
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
    assert report["outcome"] == "opened"
    rounds = _mapping(report["review"])["rounds"]
    assert isinstance(rounds, list)
    assert _mapping(rounds[0])["hard_count"] == 1
    codex_runs = [record for record in _records(log) if record["command"] == "codex"]
    assert len(codex_runs) == 2
    assert "src/example.py:12 violates CODING-STANDARD.md" in _text(codex_runs[1], "stdin")


def test_untagged_review_answer_fails_with_an_excerpt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    _fake_clients(tmp_path, monkeypatch)
    (tmp_path / "canned" / "review-standards.md").write_text(
        "I could not resolve the fixed point,\nso the review did not run.\n",
        encoding="utf-8",
    )
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
    assert str(report["failure_reason"]).startswith(
        "Claude review returned no tagged findings: I could not resolve the fixed point, so"
    )
