"""The coder routine scans reviews on agent pull requests."""

import json
import os
from pathlib import Path

import pytest

from kinby.cli import main
from kinby.factory.babysit import (
    actionable_threads,
    is_merge_ready,
    is_waiting,
    signal_pull_request_number,
    signal_warrants_scan,
)
from kinby.factory.repository import (
    AgentPullRequest,
    BabysitPullRequest,
    BranchName,
    CheckRun,
    CheckRunStatus,
    CommitSha,
    GitHubLogin,
    GitHubRepository,
    PullRequestNumber,
    PullRequestReview,
    PullRequestUrl,
    RepositoryMetadata,
    RepositoryName,
    ReviewComment,
    ReviewThread,
    ReviewThreadId,
    select_agent_pull_requests,
)
from kinby.instance import load_instance
from tests.test_factory import (
    _arguments,
    _coder_copy,
    _records,
    _RoutineModel,
    _use_routine_model,
    _write_fake_github,
)


def _agent_pull_request(branch: str = "agent/225-babysit") -> AgentPullRequest:
    return AgentPullRequest(
        number=PullRequestNumber(24),
        url=PullRequestUrl("https://example.test/pull/24"),
        branch=BranchName(branch),
        head=CommitSha("head-24"),
        body="Closes #225\n",
        stack=None,
        author=GitHubLogin("kinby-coder"),
        labels=(),
    )


def _babysit_pull_request(
    *,
    threads: tuple[ReviewThread, ...] = (),
    checks: tuple[CheckRun, ...] = (),
    reviews: tuple[PullRequestReview, ...] = (),
) -> BabysitPullRequest:
    return BabysitPullRequest(
        listed=_agent_pull_request(),
        checks=checks,
        threads=threads,
        reviews=reviews,
        round_count=0,
    )


def test_thread_classification_is_pure_and_ignores_empty_and_resolved_threads() -> None:
    coder = GitHubLogin("kinby-coder")
    empty = ReviewThread(ReviewThreadId("empty"), False, "src/example.py", None, ())
    actionable = ReviewThread(
        ReviewThreadId("actionable"),
        False,
        "src/example.py",
        12,
        (ReviewComment(GitHubLogin("reviewer"), "please fix", CommitSha("head-24")),),
    )
    answered = ReviewThread(
        ReviewThreadId("answered"),
        False,
        "src/example.py",
        12,
        (ReviewComment(coder, "fixed", CommitSha("head-24")),),
    )
    resolved = ReviewThread(
        ReviewThreadId("resolved"),
        True,
        "src/example.py",
        12,
        (ReviewComment(GitHubLogin("reviewer"), "please fix", CommitSha("head-24")),),
    )

    assert actionable_threads((empty, actionable, answered, resolved), coder) == (actionable,)


def test_waiting_and_merge_ready_are_pure_and_use_only_the_current_head() -> None:
    coder = GitHubLogin("kinby-coder")
    completed = CheckRun(CheckRunStatus.COMPLETED)
    running = CheckRun(CheckRunStatus.IN_PROGRESS)
    stale_review = PullRequestReview(CommitSha("old-head"))
    current_review = PullRequestReview(CommitSha("head-24"))

    assert not is_waiting((completed,))
    assert is_waiting((completed, running))
    assert not is_merge_ready(
        _babysit_pull_request(checks=(completed,), reviews=(stale_review,)), coder
    )
    assert is_merge_ready(
        _babysit_pull_request(checks=(completed,), reviews=(current_review,)), coder
    )
    assert not is_merge_ready(
        _babysit_pull_request(checks=(running,), reviews=(current_review,)), coder
    )


def test_agent_pr_and_signal_selection_are_pure() -> None:
    human = _agent_pull_request("feature/human")
    agent = _agent_pull_request()
    abbreviated_comment: dict[str, object] = {
        "body": {"issue": {"number": 24, "pull_request": {"url": "https://example.test/pulls/24"}}}
    }

    assert select_agent_pull_requests((human, agent)) == (agent,)
    assert signal_warrants_scan({})
    assert signal_warrants_scan({"body": {"pull_request": {"head": {"ref": "agent/225-babysit"}}}})
    assert not signal_warrants_scan({"body": {"pull_request": {"head": {"ref": "feature/human"}}}})
    assert not signal_warrants_scan({"body": {"issue": {"number": 225}}})
    assert not signal_warrants_scan(abbreviated_comment)
    assert signal_warrants_scan(abbreviated_comment, BranchName("agent/225-babysit"))
    assert not signal_warrants_scan(abbreviated_comment, BranchName("feature/human"))
    assert signal_pull_request_number(abbreviated_comment) == PullRequestNumber(24)


def _fake_github(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    binaries = tmp_path / "bin"
    binaries.mkdir()
    canned = tmp_path / "canned"
    canned.mkdir()
    log = tmp_path / "commands.jsonl"
    _write_fake_github(binaries / "gh")
    monkeypatch.setenv("PATH", f"{binaries}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GITHUB_LOG", str(log))
    monkeypatch.setenv("FAKE_GITHUB_RESPONSES", str(canned))
    (canned / "repository.txt").write_text(
        '{"name":"kinby","owner":{"login":"jorgesolerrr"},"defaultBranchRef":{"name":"main"}}\n',
        encoding="utf-8",
    )
    (canned / "user.txt").write_text("kinby-coder\n", encoding="utf-8")
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
    requested_reviewers: list[dict[str, str]] | None = None,
    author: str = "kinby-coder",
) -> None:
    canned.joinpath("pull-requests.json").write_text(
        json.dumps(
            [
                _pull_request(
                    number=24,
                    issue=225,
                    author=author,
                    labels=labels,
                    requested_reviewers=requested_reviewers,
                )
            ]
        ),
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
    requested_reviewers: list[dict[str, str]] | None = None,
    body: str | None = None,
) -> dict[str, object]:
    return {
        "number": number,
        "html_url": f"https://example.test/pull/{number}",
        "head": {"ref": f"agent/{issue}-babysit", "sha": f"head-{number}"},
        "body": body if body is not None else f"Closes #{issue}\n",
        "user": {"login": author},
        "labels": labels or [],
        "requested_reviewers": requested_reviewers or [],
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
    thread_id: str = "PRRT_thread",
    head: str = "head-24",
) -> dict[str, object]:
    return {
        "id": thread_id,
        "isResolved": resolved,
        "path": "src/example.py",
        "line": 12,
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


def test_scan_reads_review_thread_location_and_comment_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canned, _ = _fake_github(tmp_path, monkeypatch)
    _write_scan(
        canned,
        threads=[_review_thread("reviewer", thread_id="PRRT_review")],
        checks=[{"status": "completed"}],
        reviews=[],
        comments=[],
    )

    repository = GitHubRepository(tmp_path)
    (pull_request,) = repository.babysit_pull_requests(
        GitHubLogin("kinby-coder"),
        RepositoryMetadata(
            GitHubLogin("jorgesolerrr"),
            RepositoryName("kinby"),
            BranchName("main"),
        ),
    )
    (thread,) = pull_request.threads
    (comment,) = thread.comments

    assert thread.id == ReviewThreadId("PRRT_review")
    assert thread.path == "src/example.py"
    assert thread.line == 12
    assert comment.body == "review comment"


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
    assert calls.index(["pr", "edit", "24", "--add-reviewer", "jorgesolerrr"]) < calls.index(
        ["pr", "edit", "24", "--add-label", "merge-ready"]
    )
    graphql = next(arguments for arguments in calls if arguments[:2] == ["api", "graphql"])
    query = next(item.removeprefix("query=") for item in graphql if item.startswith("query="))
    assert "query BabysitReviewThreads" in query


def test_existing_merge_ready_label_does_not_hide_a_missing_review_request(
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
        labels=[{"name": "merge-ready"}],
        author="implementing-agent",
    )

    output = _run_babysit(
        tmp_path,
        monkeypatch,
        capsys,
        {"action": "submitted", "pull_request": {"head": {"ref": "agent/225-babysit"}}},
    )

    assert '"outcome":"merge_ready"' in output
    calls = [_arguments(record) for record in _records(log)]
    assert ["pr", "edit", "24", "--add-reviewer", "jorgesolerrr"] in calls
    assert ["pr", "edit", "24", "--add-label", "merge-ready"] not in calls


def test_existing_review_request_and_merge_ready_label_need_no_mutation(
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
        labels=[{"name": "merge-ready"}],
        requested_reviewers=[{"login": "jorgesolerrr"}],
        author="implementing-agent",
    )

    output = _run_babysit(
        tmp_path,
        monkeypatch,
        capsys,
        {"action": "submitted", "pull_request": {"head": {"ref": "agent/225-babysit"}}},
    )

    assert "[tool.result] babysit_pull_request (ok): None" in output
    assert not any(_arguments(record)[:2] == ["pr", "edit"] for record in _records(log))


def test_completed_maintainer_review_is_not_requested_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canned, log = _fake_github(tmp_path, monkeypatch)
    _write_scan(
        canned,
        threads=[_review_thread("jorgesolerrr", "kinby-coder")],
        checks=[{"status": "completed"}],
        reviews=[{"commit_id": "head-24", "user": {"login": "jorgesolerrr"}}],
        comments=[],
        labels=[{"name": "merge-ready"}],
        author="implementing-agent",
    )

    output = _run_babysit(
        tmp_path,
        monkeypatch,
        capsys,
        {"action": "submitted", "pull_request": {"head": {"ref": "agent/225-babysit"}}},
    )

    assert "[tool.result] babysit_pull_request (ok): None" in output
    assert not any(_arguments(record)[:2] == ["pr", "edit"] for record in _records(log))


def test_maintainer_review_on_an_old_head_does_not_suppress_a_new_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canned, log = _fake_github(tmp_path, monkeypatch)
    _write_scan(
        canned,
        threads=[_review_thread("jorgesolerrr", "kinby-coder")],
        checks=[{"status": "completed"}],
        reviews=[{"commit_id": "old-head", "user": {"login": "jorgesolerrr"}}],
        comments=[],
        labels=[{"name": "merge-ready"}],
        author="implementing-agent",
    )

    _run_babysit(
        tmp_path,
        monkeypatch,
        capsys,
        {"action": "submitted", "pull_request": {"head": {"ref": "agent/225-babysit"}}},
    )

    assert ["pr", "edit", "24", "--add-reviewer", "jorgesolerrr"] in [
        _arguments(record) for record in _records(log)
    ]


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


def test_scan_removes_merge_ready_when_the_pull_request_is_no_longer_ready(
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
    assert ["pr", "edit", "24", "--remove-label", "merge-ready"] in [
        _arguments(record) for record in _records(log)
    ]


def test_stale_label_is_not_removed_before_the_closing_issue_is_validated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canned, log = _fake_github(tmp_path, monkeypatch)
    canned.joinpath("pull-requests.json").write_text(
        json.dumps(
            [
                _pull_request(
                    number=24,
                    issue=225,
                    labels=[{"name": "merge-ready"}],
                    body="No closing reference",
                )
            ]
        ),
        encoding="utf-8",
    )
    _write_review_state(
        canned,
        number=24,
        head="head-24",
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
        expected_exit_code=1,
    )

    assert "agent pull request body does not close an issue" in output
    assert ["pr", "edit", "24", "--remove-label", "merge-ready"] not in [
        _arguments(record) for record in _records(log)
    ]


def test_scan_replaces_ready_for_human_when_the_pull_request_becomes_ready(
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
    assert ["pr", "edit", "24", "--remove-label", "ready-for-human"] in calls
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


@pytest.mark.parametrize("status", ["waiting", "requested", "pending"])
def test_non_running_github_check_status_does_not_block_merge_ready(
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
