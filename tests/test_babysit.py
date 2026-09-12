"""The coder routine scans reviews on agent pull requests."""

import json
import os
from pathlib import Path

import pytest

from kinby.cli import main
from kinby.factory.babysit import (
    actionable_threads,
    answered_threads,
    is_merge_ready,
    is_waiting,
    resolved_threads,
    select_pull_request,
)
from kinby.factory.repository import (
    BabysitPullRequest,
    BranchName,
    CheckRun,
    CheckRunStatus,
    CommitSha,
    GitHubLogin,
    LabelName,
    PullRequestNumber,
    PullRequestReview,
    PullRequestUrl,
    ReviewComment,
    ReviewThread,
    ReviewThreadId,
)
from kinby.instance import load_instance
from tests.test_factory import (
    _arguments,
    _coder_copy,
    _records,
    _RoutineModel,
    _use_routine_model,
    _write_executable,
)


def _comment(
    author: str,
    *,
    body: str = "review comment",
    commit: str | None = "head-24",
) -> ReviewComment:
    return ReviewComment(
        GitHubLogin(author),
        body,
        CommitSha(commit) if commit is not None else None,
    )


def _thread(
    *comments: ReviewComment,
    resolved: bool = False,
    thread_id: str = "thread-1",
) -> ReviewThread:
    return ReviewThread(ReviewThreadId(thread_id), resolved, "src/example.py", 12, comments)


def _pull_request(
    *,
    threads: tuple[ReviewThread, ...] = (),
    checks: tuple[CheckRun, ...] = (),
    reviews: tuple[PullRequestReview, ...] = (),
    rounds: tuple[str, ...] = (),
    labels: tuple[LabelName, ...] = (),
) -> BabysitPullRequest:
    return BabysitPullRequest(
        number=PullRequestNumber(24),
        url=PullRequestUrl("https://example.test/pull/24"),
        branch=BranchName("agent/225-babysit"),
        head=CommitSha("head-24"),
        body="Closes #225\n",
        author=GitHubLogin("kinby-coder"),
        labels=labels,
        checks=checks,
        threads=threads,
        reviews=reviews,
        round_comments=rounds,
    )


def test_thread_classification_uses_resolution_and_the_last_author() -> None:
    coder = GitHubLogin("kinby-coder")
    actionable = _thread(_comment("reviewer"), thread_id="actionable")
    answered = _thread(
        _comment("reviewer"),
        _comment("kinby-coder", body="handled"),
        thread_id="answered",
    )
    resolved = _thread(_comment("reviewer"), resolved=True, thread_id="resolved")

    threads = (actionable, answered, resolved)

    assert actionable_threads(threads, coder) == (actionable,)
    assert answered_threads(threads, coder) == (answered,)
    assert resolved_threads(threads) == (resolved,)


def test_waiting_and_merge_ready_are_derived_from_the_current_head() -> None:
    coder = GitHubLogin("kinby-coder")
    answered = _thread(
        _comment("reviewer", commit="old-head"),
        _comment("kinby-coder", commit="old-head"),
    )
    completed = CheckRun(CheckRunStatus.COMPLETED)
    running = CheckRun(CheckRunStatus.IN_PROGRESS)
    reviewed = PullRequestReview(CommitSha("head-24"))

    assert not is_waiting((completed,))
    assert is_waiting((completed, running))
    assert is_merge_ready(
        _pull_request(threads=(answered,), checks=(completed,), reviews=(reviewed,)), coder
    )
    assert not is_merge_ready(
        _pull_request(threads=(answered,), checks=(running,), reviews=(reviewed,)), coder
    )
    assert not is_merge_ready(_pull_request(threads=(answered,), checks=(completed,)), coder)


def test_review_comment_on_the_head_is_enough_for_merge_ready() -> None:
    coder = GitHubLogin("kinby-coder")
    answered = _thread(_comment("reviewer"), _comment("kinby-coder"))

    assert is_merge_ready(_pull_request(threads=(answered,)), coder)


def test_selection_takes_the_first_actionable_non_waiting_pr_below_the_limit() -> None:
    coder = GitHubLogin("kinby-coder")
    waiting = _pull_request(
        threads=(_thread(_comment("reviewer")),),
        checks=(CheckRun(CheckRunStatus.QUEUED),),
    )
    at_limit = _pull_request(
        threads=(_thread(_comment("reviewer")),),
        rounds=("one", "two", "three"),
    )
    selected = _pull_request(threads=(_thread(_comment("reviewer")),))

    assert select_pull_request((waiting, at_limit, selected), coder, round_limit=3) is selected


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
    output = '{"nameWithOwner":"jorgesolerrr/kinby"}'
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
        json.dumps(
            [
                {
                    "number": 24,
                    "html_url": "https://example.test/pull/24",
                    "head": {"ref": "agent/225-babysit", "sha": "head-24"},
                    "body": "Closes #225\n",
                    "user": {"login": author},
                    "labels": labels or [],
                }
            ]
        ),
        encoding="utf-8",
    )
    canned.joinpath("check-runs-head-24.json").write_text(
        json.dumps({"check_runs": checks}), encoding="utf-8"
    )
    canned.joinpath("review-threads-24.json").write_text(
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
    canned.joinpath("reviews-24.json").write_text(json.dumps(reviews), encoding="utf-8")
    canned.joinpath("issue-comments-24.json").write_text(json.dumps(comments), encoding="utf-8")


def _review_thread(*authors: str) -> dict[str, object]:
    return {
        "id": "PRRT_thread",
        "isResolved": False,
        "path": "src/example.py",
        "line": 12,
        "comments": {
            "nodes": [
                {
                    "author": {"login": author},
                    "body": "review comment",
                    "commit": {"oid": "head-24"},
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

    assert main(arguments) == 0
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
        threads=[_review_thread("reviewer", "kinby-coder")],
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
    assert json.loads(result_line.partition(": ")[2]) == {
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
    report = json.loads(result_line.partition(": ")[2])
    assert report["outcome"] == "round_limit"
    assert report["round_number"] == 3
    assert report["issue_number"] == 225
    assert ["pr", "edit", "24", "--add-label", "ready-for-human"] in [
        _arguments(record) for record in _records(log)
    ]


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
