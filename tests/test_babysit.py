"""The coder routine scans reviews on agent pull requests."""

import json
import os
from pathlib import Path

import pytest

from kinby.cli import main
from kinby.instance import load_instance
from tests.test_factory import (
    _arguments,
    _coder_copy,
    _records,
    _RoutineModel,
    _use_routine_model,
    _write_executable,
)


def _fake_github(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    binaries = tmp_path / "bin"
    binaries.mkdir()
    canned = tmp_path / "canned"
    canned.mkdir()
    log = tmp_path / "commands.jsonl"
    _write_executable(
        binaries / "gh",
        """import json
import os
import sys
from pathlib import Path

arguments = sys.argv[1:]
record = {"command": "gh", "arguments": arguments, "cwd": os.getcwd()}
responses = Path(os.environ["FACTORY_CANNED_RESPONSES"])
if arguments[:2] == ["repo", "view"]:
    output = '{"name":"kinby","owner":{"login":"jorgesolerrr"},"defaultBranchRef":{"name":"main"}}'
elif arguments[:2] == ["pr", "view"]:
    output = (responses / "signal-head.txt").read_text(encoding="utf-8")
elif arguments[:1] == ["api"] and "user" in arguments:
    output = "kinby-coder"
elif arguments[:2] == ["api", "graphql"]:
    number = next(
        argument.split("=", 1)[1]
        for argument in arguments
        if argument.startswith("number=")
    )
    output = (responses / f"review-threads-{number}.json").read_text(encoding="utf-8")
elif arguments[:1] == ["api"]:
    endpoint = next((argument for argument in arguments if argument.startswith("repos/")), "")
    if endpoint.endswith("/pulls"):
        output = (responses / "pull-requests.json").read_text(encoding="utf-8")
    elif endpoint.endswith("/check-runs"):
        number = endpoint.split("/")[-2]
        output = (responses / f"check-runs-{number}.json").read_text(encoding="utf-8")
    elif endpoint.endswith("/reviews"):
        number = endpoint.split("/")[-2]
        output = (responses / f"reviews-{number}.json").read_text(encoding="utf-8")
    elif endpoint.endswith("/comments"):
        number = endpoint.split("/")[-2]
        output = (responses / f"issue-comments-{number}.json").read_text(encoding="utf-8")
    else:
        output = ""
else:
    output = ""
with Path(os.environ["FACTORY_COMMAND_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(record) + "\\n")
print(output)
""",
    )
    monkeypatch.setenv("PATH", f"{binaries}:{os.environ['PATH']}")
    monkeypatch.setenv("FACTORY_COMMAND_LOG", str(log))
    monkeypatch.setenv("FACTORY_CANNED_RESPONSES", str(canned))
    (canned / "signal-head.txt").write_text("agent/225-babysit\n", encoding="utf-8")
    (canned / "pull-requests.json").write_text("[]", encoding="utf-8")
    return canned, log


def _write_scan(
    canned: Path,
    *,
    threads: list[dict[str, object]],
    checks: list[dict[str, object]],
    reviews: list[dict[str, object]],
    comments: list[dict[str, object]],
    labels: list[dict[str, str]] | None = None,
    author: str = "kinby-coder",
) -> None:
    canned.joinpath("pull-requests.json").write_text(
        json.dumps([_pull_request(number=24, issue=225, author=author, labels=labels)]),
        encoding="utf-8",
    )
    _write_review_state(
        canned,
        number=24,
        head="head-24",
        threads=threads,
        checks=checks,
        reviews=reviews,
        comments=comments,
    )


def _pull_request(
    *,
    number: int,
    issue: int,
    author: str = "kinby-coder",
    labels: list[dict[str, str]] | None = None,
    body: str | None = None,
) -> dict[str, object]:
    return {
        "number": number,
        "html_url": f"https://example.test/pull/{number}",
        "head": {"ref": f"agent/{issue}-babysit", "sha": f"head-{number}"},
        "body": body if body is not None else f"Closes #{issue}\n",
        "user": {"login": author},
        "labels": labels or [],
    }


def _write_review_state(
    canned: Path,
    *,
    number: int,
    head: str,
    threads: list[dict[str, object]],
    checks: list[dict[str, object]],
    reviews: list[dict[str, object]],
    comments: list[dict[str, object]],
) -> None:
    canned.joinpath(f"check-runs-{head}.json").write_text(
        json.dumps({"check_runs": checks}), encoding="utf-8"
    )
    canned.joinpath(f"review-threads-{number}.json").write_text(
        json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {"nodes": threads, "pageInfo": {"hasNextPage": False}}
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    canned.joinpath(f"reviews-{number}.json").write_text(json.dumps(reviews), encoding="utf-8")
    canned.joinpath(f"issue-comments-{number}.json").write_text(
        json.dumps(comments), encoding="utf-8"
    )


def _review_thread(
    *authors: str,
    resolved: bool = False,
    head: str = "head-24",
) -> dict[str, object]:
    return {
        "isResolved": resolved,
        "comments": {
            "nodes": [
                {
                    "author": {"login": author},
                    "body": "review comment",
                    "commit": {"oid": head},
                }
                for author in authors
            ]
        },
    }


def _run_babysit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    delivery: dict[str, object] | None,
    expected_exit_code: int = 0,
) -> str:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    instance = load_instance(instance_path)
    _use_routine_model(monkeypatch, instance, _RoutineModel())
    arguments = [
        "routine",
        "run",
        "babysit-pull-request",
        "--instance",
        str(instance_path),
    ]
    if delivery is not None:
        payload = tmp_path / "delivery.json"
        payload.write_text(json.dumps(delivery), encoding="utf-8")
        arguments[3:3] = ["--payload", str(payload)]

    assert main(arguments) == expected_exit_code
    return capsys.readouterr().out


@pytest.mark.parametrize(
    "delivery",
    [
        {"action": "submitted", "pull_request": {"head": {"ref": "feature/human"}}},
        {"action": "created", "issue": {"number": 225}, "comment": {"body": "hello"}},
    ],
)
def test_irrelevant_delivery_returns_no_work_without_github(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    delivery: dict[str, object],
) -> None:
    _, log = _fake_github(tmp_path, monkeypatch)

    output = _run_babysit(tmp_path, monkeypatch, capsys, delivery)

    assert "[tool.result] babysit_pull_request (ok): None" in output
    assert not log.exists()


def test_issue_comment_on_an_agent_pull_request_triggers_a_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, log = _fake_github(tmp_path, monkeypatch)

    output = _run_babysit(
        tmp_path,
        monkeypatch,
        capsys,
        {
            "action": "created",
            "issue": {"number": 24, "pull_request": {"url": "https://api.example.test/pulls/24"}},
            "comment": {"body": "please revisit this"},
        },
    )

    assert "[tool.result] babysit_pull_request (ok): None" in output
    calls = [_arguments(record) for record in _records(log)]
    assert calls[0][:3] == ["pr", "view", "24"]
    assert any(
        arguments[0] == "api" and any(item.endswith("/pulls") for item in arguments)
        for arguments in calls
    )


def test_schedule_wake_always_scans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, log = _fake_github(tmp_path, monkeypatch)

    output = _run_babysit(tmp_path, monkeypatch, capsys, None)

    assert "[tool.result] babysit_pull_request (ok): None" in output
    assert any(
        any(item.endswith("/pulls") for item in _arguments(record)) for record in _records(log)
    )


def test_answered_review_labels_the_pull_request_merge_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canned, log = _fake_github(tmp_path, monkeypatch)
    _write_scan(
        canned,
        threads=[
            _review_thread("reviewer", "kinby-coder"),
            _review_thread("reviewer", resolved=True),
        ],
        checks=[{"status": "completed"}],
        reviews=[{"commit_id": "head-24"}],
        comments=[],
        author="implementing-agent",
    )

    output = _run_babysit(
        tmp_path,
        monkeypatch,
        capsys,
        {"action": "submitted", "pull_request": {"head": {"ref": "agent/225-babysit"}}},
    )

    result_line = next(
        line
        for line in output.splitlines()
        if line.startswith("[tool.result] babysit_pull_request (ok): ")
    )
    assert json.loads(result_line.partition(": ")[2]) == [
        {
            "pull_request_number": 24,
            "pull_request_url": "https://example.test/pull/24",
            "issue_number": 225,
            "outcome": "merge_ready",
            "round_number": 0,
            "threads_fixed": 0,
            "threads_answered": 0,
            "codex": None,
            "checks": None,
            "warnings": [],
            "failure_reason": None,
        }
    ]
    calls = [_arguments(record) for record in _records(log)]
    assert ["pr", "edit", "24", "--add-label", "merge-ready"] in calls
    assert ["pr", "edit", "24", "--add-reviewer", "jorgesolerrr"] in calls
    graphql = next(arguments for arguments in calls if arguments[:2] == ["api", "graphql"])
    query = next(item.removeprefix("query=") for item in graphql if item.startswith("query="))
    assert "query BabysitReviewThreads" in query


def test_round_limit_labels_the_pull_request_ready_for_human(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canned, log = _fake_github(tmp_path, monkeypatch)
    _write_scan(
        canned,
        threads=[_review_thread("reviewer")],
        checks=[{"status": "completed"}],
        reviews=[{"commit_id": "head-24"}],
        comments=[
            {
                "user": {"login": "kinby-coder"},
                "body": f"Babysit round {number} of 3: fixed 1, answered 0.",
            }
            for number in range(1, 4)
        ],
    )

    output = _run_babysit(
        tmp_path,
        monkeypatch,
        capsys,
        {"action": "created", "pull_request": {"head": {"ref": "agent/225-babysit"}}},
    )

    result_line = next(
        line
        for line in output.splitlines()
        if line.startswith("[tool.result] babysit_pull_request (ok): ")
    )
    (report,) = json.loads(result_line.partition(": ")[2])
    assert report["outcome"] == "round_limit"
    assert report["round_number"] == 3
    assert report["issue_number"] == 225
    assert ["pr", "edit", "24", "--add-label", "ready-for-human"] in [
        _arguments(record) for record in _records(log)
    ]


def test_one_scan_labels_every_completed_pull_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canned, log = _fake_github(tmp_path, monkeypatch)
    canned.joinpath("pull-requests.json").write_text(
        json.dumps(
            [
                _pull_request(number=24, issue=225, author="implementing-agent"),
                _pull_request(number=25, issue=226),
            ]
        ),
        encoding="utf-8",
    )
    _write_review_state(
        canned,
        number=24,
        head="head-24",
        threads=[_review_thread("reviewer", "kinby-coder")],
        checks=[{"status": "completed"}],
        reviews=[{"commit_id": "head-24"}],
        comments=[],
    )
    _write_review_state(
        canned,
        number=25,
        head="head-25",
        threads=[_review_thread("reviewer", head="head-25")],
        checks=[{"status": "completed"}],
        reviews=[{"commit_id": "head-25"}],
        comments=[
            {
                "user": {"login": "kinby-coder"},
                "body": f"Babysit round {number} of 3: fixed 1, answered 0.",
            }
            for number in range(1, 4)
        ],
    )

    output = _run_babysit(
        tmp_path,
        monkeypatch,
        capsys,
        {"action": "submitted", "pull_request": {"head": {"ref": "agent/225-babysit"}}},
    )

    calls = [_arguments(record) for record in _records(log)]
    assert ["pr", "edit", "24", "--add-label", "merge-ready"] in calls
    assert ["pr", "edit", "24", "--add-reviewer", "jorgesolerrr"] in calls
    assert ["pr", "edit", "25", "--add-label", "ready-for-human"] in calls
    assert sum(arguments[:2] == ["repo", "view"] for arguments in calls) == 1
    result_line = next(
        line
        for line in output.splitlines()
        if line.startswith("[tool.result] babysit_pull_request (ok): ")
    )
    reports = json.loads(result_line.partition(": ")[2])
    assert [report["pull_request_number"] for report in reports] == [24, 25]
    assert [report["outcome"] for report in reports] == ["merge_ready", "round_limit"]


def test_missing_closed_issue_fails_before_label_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canned, log = _fake_github(tmp_path, monkeypatch)
    canned.joinpath("pull-requests.json").write_text(
        json.dumps([_pull_request(number=24, issue=225, body="No closing reference")]),
        encoding="utf-8",
    )
    _write_review_state(
        canned,
        number=24,
        head="head-24",
        threads=[_review_thread("reviewer", "kinby-coder")],
        checks=[{"status": "completed"}],
        reviews=[{"commit_id": "head-24"}],
        comments=[],
    )

    output = _run_babysit(
        tmp_path,
        monkeypatch,
        capsys,
        {"action": "submitted", "pull_request": {"head": {"ref": "agent/225-babysit"}}},
        expected_exit_code=1,
    )

    assert "agent pull request body does not close an issue" in output
    assert not any(_arguments(record)[:2] == ["pr", "edit"] for record in _records(log))


@pytest.mark.parametrize("status", ["waiting", "requested", "pending"])
def test_other_github_check_statuses_do_not_abort_the_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: str,
) -> None:
    canned, log = _fake_github(tmp_path, monkeypatch)
    _write_scan(
        canned,
        threads=[_review_thread("reviewer", "kinby-coder")],
        checks=[{"status": status}],
        reviews=[{"commit_id": "head-24"}],
        comments=[],
    )

    output = _run_babysit(
        tmp_path,
        monkeypatch,
        capsys,
        {"action": "submitted", "pull_request": {"head": {"ref": "agent/225-babysit"}}},
    )

    assert '"outcome":"merge_ready"' in output
    assert ["pr", "edit", "24", "--add-label", "merge-ready"] in [
        _arguments(record) for record in _records(log)
    ]


def test_scan_does_not_remove_merge_ready_when_the_pull_request_is_no_longer_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canned, log = _fake_github(tmp_path, monkeypatch)
    _write_scan(
        canned,
        threads=[_review_thread("reviewer")],
        checks=[{"status": "in_progress"}],
        reviews=[{"commit_id": "head-24"}],
        comments=[],
        labels=[{"name": "merge-ready"}],
    )

    output = _run_babysit(
        tmp_path,
        monkeypatch,
        capsys,
        {"action": "submitted", "pull_request": {"head": {"ref": "agent/225-babysit"}}},
    )

    assert "[tool.result] babysit_pull_request (ok): None" in output
    assert ["pr", "edit", "24", "--remove-label", "merge-ready"] not in [
        _arguments(record) for record in _records(log)
    ]


def test_scan_does_not_remove_ready_for_human_when_the_pull_request_becomes_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canned, log = _fake_github(tmp_path, monkeypatch)
    _write_scan(
        canned,
        threads=[_review_thread("reviewer", "kinby-coder")],
        checks=[{"status": "completed"}],
        reviews=[{"commit_id": "head-24"}],
        comments=[],
        labels=[{"name": "ready-for-human"}],
    )

    _run_babysit(
        tmp_path,
        monkeypatch,
        capsys,
        {"action": "submitted", "pull_request": {"head": {"ref": "agent/225-babysit"}}},
    )

    calls = [_arguments(record) for record in _records(log)]
    assert ["pr", "edit", "24", "--remove-label", "ready-for-human"] not in calls
    assert ["pr", "edit", "24", "--add-label", "merge-ready"] in calls


def test_actionable_review_waits_for_a_running_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canned, log = _fake_github(tmp_path, monkeypatch)
    _write_scan(
        canned,
        threads=[_review_thread("reviewer")],
        checks=[{"status": "in_progress"}],
        reviews=[{"commit_id": "head-24"}],
        comments=[],
    )

    output = _run_babysit(
        tmp_path,
        monkeypatch,
        capsys,
        {"action": "submitted", "pull_request": {"head": {"ref": "agent/225-babysit"}}},
    )

    assert "[tool.result] babysit_pull_request (ok): None" in output
    assert not any(_arguments(record)[:2] == ["pr", "edit"] for record in _records(log))


def test_actionable_review_below_the_round_limit_is_left_for_the_fix_slice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canned, log = _fake_github(tmp_path, monkeypatch)
    _write_scan(
        canned,
        threads=[_review_thread("reviewer")],
        checks=[{"status": "completed"}],
        reviews=[{"commit_id": "head-24"}],
        comments=[],
    )

    output = _run_babysit(
        tmp_path,
        monkeypatch,
        capsys,
        {"action": "submitted", "pull_request": {"head": {"ref": "agent/225-babysit"}}},
    )

    assert "[tool.result] babysit_pull_request (ok): None" in output
    assert not any(_arguments(record)[:2] == ["pr", "edit"] for record in _records(log))


def test_deployment_registers_the_babysit_signal() -> None:
    root = Path(__file__).parents[1]
    wizard = (root / "scripts" / "deploy-wizard.sh").read_text(encoding="utf-8")
    container = (root / "docs" / "container.md").read_text(encoding="utf-8")

    for value in (
        "babysit-pull-request",
        "pull_request_review",
        "pull_request_review_comment",
        "issue_comment",
    ):
        assert value in wizard
        assert value in container
