"""A workbench runs the step the hub sends in the run's folder, which it cuts when it lacks it, and
removes that folder when the hub asks."""

import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from kinby.contracts import (
    ClientStepRun,
    CodeStepRun,
    CodingClient,
    CommandStepRun,
    FactoryRunOrigin,
    FactoryRunTranscript,
    FactoryRunTranscriptCommand,
    ReasoningEffort,
    RunFolder,
    RunFolderRemoveCommand,
    StepEnding,
    StepResult,
    StepRunCommand,
    UsageSource,
)
from kinby.workbench import WorkbenchSteps, prepare_workspace
from tests.test_hub_factories import write_factory
from tests.test_steps import CLAUDE_STREAM, recorded_calls, streaming, stub_client
from tests.test_workbench_workspace import _seed

BRANCH = RunFolder(branch="tickets/issue-12")
PLAIN = RunFolder(branch=None)
LABEL = """\
from kinby.plugins.tools import tool


@tool(write=True)
def label_issue(issue: int) -> dict[str, str]:
    \"\"\"Label the issue.\"\"\"
    return {{"label": "{label} {{}}".format(issue)}}
"""
CHECK = """\
from kinby.plugins.hooks import HookResult, StepEnd, hook


@hook
def drafted(end: StepEnd) -> HookResult:
    drafted = (end.workspace / "draft.md").is_file()
    return HookResult(
        values={"ending": end.ending.value, "drafted": drafted, "issue": end.work_item["issue"]},
        outcome=None if drafted else "redraft",
    )


@hook
def refused(end: StepEnd) -> None:
    raise ValueError("the draft has no title")
"""


def _workbench(tmp_path: Path, source: str | None) -> WorkbenchSteps:
    directory = tmp_path / "workbench"
    prepare_workspace(directory / "workspace", source)
    return WorkbenchSteps(directory, tmp_path / "factories", tmp_path / "shipped")


def _command(
    run_id: UUID,
    step: CommandStepRun | CodeStepRun | ClientStepRun,
    folder: RunFolder,
    attempt: int = 1,
    *,
    fresh: bool = False,
    check: str | None = None,
) -> StepRunCommand:
    return StepRunCommand(
        step=step,
        origin=FactoryRunOrigin(factory="tickets", run_id=run_id, step=step.kind),
        attempt=attempt,
        hook=check,
        work_item={"issue": 12},
        folder=folder,
        fresh_folder=fresh,
    )


def _git(directory: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=directory, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_commands_run_in_a_run_worktree_cut_from_the_clone_on_the_runs_branch(tmp_path):
    workbench = _workbench(tmp_path, str(_seed(tmp_path)))
    run_id = uuid4()
    step = CommandStepRun(run=["touch made", "git status --short"])

    result = asyncio.run(workbench.run(_command(run_id, step, BRANCH)))

    worktree = tmp_path / "workbench" / "worktrees" / "tickets" / "issue-12"
    assert result.ending is StepEnding.CLEAN
    assert (worktree / "made").is_file()
    assert (worktree / "NOTES.md").is_file()
    assert _git(worktree, "branch", "--show-current") == "tickets/issue-12"
    assert not (tmp_path / "workbench" / "workspace" / "made").exists()


def test_a_failed_step_leaves_its_uncommitted_files_for_the_retry_to_find(tmp_path):
    workbench = _workbench(tmp_path, str(_seed(tmp_path)))
    run_id = uuid4()
    draft = CommandStepRun(run=["touch draft.md", "false"])
    found = CommandStepRun(run=["test -f draft.md"])

    failed = asyncio.run(workbench.run(_command(run_id, draft, BRANCH)))
    retried = asyncio.run(workbench.run(_command(run_id, found, BRANCH, attempt=2)))

    assert failed.ending is StepEnding.FAILED
    assert retried.ending is StepEnding.CLEAN


def test_a_fresh_folder_is_a_run_worktree_cut_again_from_the_base(tmp_path):
    workbench = _workbench(tmp_path, str(_seed(tmp_path)))
    run_id = uuid4()
    draft = CommandStepRun(
        run=[
            "touch draft.md",
            "git add draft.md",
            "git -c user.name=kinby -c user.email=kinby@example.com commit --quiet -m draft",
            "git push --quiet origin tickets/issue-12",
            "touch notes.md",
        ]
    )
    drafted = asyncio.run(workbench.run(_command(run_id, draft, BRANCH)))

    again = asyncio.run(
        workbench.run(_command(run_id, CommandStepRun(run=["true"]), BRANCH, 2, fresh=True))
    )

    worktree = tmp_path / "workbench" / "worktrees" / "tickets" / "issue-12"
    workspace = tmp_path / "workbench" / "workspace"
    assert (drafted.ending, again.ending) == (StepEnding.CLEAN, StepEnding.CLEAN)
    assert sorted(path.name for path in worktree.iterdir()) == [".git", "NOTES.md"]
    assert _git(worktree, "rev-parse", "HEAD") == _git(workspace, "rev-parse", "origin/HEAD")
    assert _git(worktree, "branch", "--show-current") == "tickets/issue-12"


def test_a_fresh_folder_is_a_directory_named_for_the_run_emptied_again(tmp_path):
    workbench = _workbench(tmp_path, None)
    run_id = uuid4()
    asyncio.run(workbench.run(_command(run_id, CommandStepRun(run=["touch draft.md"]), PLAIN)))

    asyncio.run(workbench.run(_command(run_id, CommandStepRun(run=["true"]), PLAIN, 2, fresh=True)))

    assert list((tmp_path / "workbench" / "runs" / str(run_id)).iterdir()) == []


def test_removing_a_run_worktree_removes_it_and_its_branch(tmp_path):
    workbench = _workbench(tmp_path, str(_seed(tmp_path)))
    run_id = uuid4()
    asyncio.run(workbench.run(_command(run_id, CommandStepRun(run=["touch made"]), BRANCH)))

    asyncio.run(workbench.remove_folder(RunFolderRemoveCommand(run_id=run_id, folder=BRANCH)))

    workspace = tmp_path / "workbench" / "workspace"
    assert not (tmp_path / "workbench" / "worktrees" / "tickets" / "issue-12").exists()
    assert _git(workspace, "branch", "--list", "tickets/issue-12") == ""


def test_on_a_plain_workspace_a_run_works_in_a_directory_named_for_it_until_it_goes(tmp_path):
    workbench = _workbench(tmp_path, None)
    run_id = uuid4()
    draft = CommandStepRun(run=["touch draft.md", "false"])

    failed = asyncio.run(workbench.run(_command(run_id, draft, PLAIN)))
    folder = tmp_path / "workbench" / "runs" / str(run_id)
    kept = (folder / "draft.md").is_file()
    asyncio.run(workbench.remove_folder(RunFolderRemoveCommand(run_id=run_id, folder=PLAIN)))

    assert failed.ending is StepEnding.FAILED
    assert kept
    assert not folder.exists()


def test_a_code_step_runs_the_factorys_tool_as_its_folder_holds_it_at_each_step(tmp_path):
    workbench = _workbench(tmp_path, None)
    factory = tmp_path / "factories" / "tickets"
    write_factory(factory, {"tools/label.py": LABEL.format(label="triaged")})
    run_id = uuid4()
    step = CodeStepRun(call="label_issue")

    first = asyncio.run(workbench.run(_command(run_id, step, PLAIN)))
    (factory / "tools" / "label.py").write_text(LABEL.format(label="ready"), encoding="utf-8")
    edited = asyncio.run(workbench.run(_command(run_id, step, PLAIN, attempt=2)))

    assert first == StepResult(ending=StepEnding.CLEAN, values={"label": "triaged 12"})
    assert edited == StepResult(ending=StepEnding.CLEAN, values={"label": "ready 12"})


def test_a_code_step_runs_a_tool_of_the_factory_kinby_ships_when_the_hub_holds_none(tmp_path):
    workbench = _workbench(tmp_path, None)
    write_factory(tmp_path / "shipped" / "tickets", {"tools/label.py": LABEL.format(label="new")})

    result = asyncio.run(workbench.run(_command(uuid4(), CodeStepRun(call="label_issue"), PLAIN)))

    assert result.values == {"label": "new 12"}


def test_a_code_step_fails_when_the_factory_has_no_such_tool(tmp_path):
    workbench = _workbench(tmp_path, None)
    write_factory(tmp_path / "factories" / "tickets", {"tools/label.py": LABEL.format(label="x")})

    result = asyncio.run(workbench.run(_command(uuid4(), CodeStepRun(call="close_issue"), PLAIN)))

    assert result == StepResult(
        ending=StepEnding.FAILED,
        summary='Tool "close_issue" is not one of factory "tickets"\'s tools.',
    )


@pytest.mark.parametrize(
    ("step", "ending"),
    [
        (CommandStepRun(run=["touch draft.md"]), StepEnding.CLEAN),
        (CommandStepRun(run=["touch draft.md", "false"]), StepEnding.FAILED),
        (
            CommandStepRun(run=["touch draft.md", "sleep 5"], timeout_seconds=1),
            StepEnding.TIMED_OUT,
        ),
    ],
)
def test_a_check_records_values_from_the_runs_folder_however_the_step_ended(tmp_path, step, ending):
    workbench = _workbench(tmp_path, str(_seed(tmp_path)))
    write_factory(tmp_path / "factories" / "tickets", {"hooks/drafted.py": CHECK})

    result = asyncio.run(workbench.run(_command(uuid4(), step, BRANCH, check="drafted")))

    assert result.ending is ending
    assert result.values == {"ending": ending.value, "drafted": True, "issue": 12}
    assert result.outcome is None


def test_a_check_names_the_outcome_the_run_takes(tmp_path):
    workbench = _workbench(tmp_path, None)
    write_factory(tmp_path / "factories" / "tickets", {"hooks/drafted.py": CHECK})
    step = CommandStepRun(run=["true"])

    result = asyncio.run(workbench.run(_command(uuid4(), step, PLAIN, check="drafted")))

    assert result == StepResult(
        ending=StepEnding.CLEAN,
        summary="Every command exited with code 0.",
        outcome="redraft",
        values={"ending": "clean", "drafted": False, "issue": 12},
    )


def test_a_check_joins_its_values_to_a_code_steps(tmp_path):
    workbench = _workbench(tmp_path, None)
    write_factory(
        tmp_path / "factories" / "tickets",
        {"tools/label.py": LABEL.format(label="triaged"), "hooks/drafted.py": CHECK},
    )
    step = CodeStepRun(call="label_issue")

    result = asyncio.run(workbench.run(_command(uuid4(), step, PLAIN, check="drafted")))

    assert result.values == {
        "label": "triaged 12",
        "ending": "clean",
        "drafted": False,
        "issue": 12,
    }


def test_a_check_that_raises_fails_the_step_and_says_why(tmp_path):
    workbench = _workbench(tmp_path, None)
    write_factory(tmp_path / "factories" / "tickets", {"hooks/drafted.py": CHECK})
    step = CommandStepRun(run=["true"])

    result = asyncio.run(workbench.run(_command(uuid4(), step, PLAIN, check="refused")))

    assert result == StepResult(
        ending=StepEnding.FAILED,
        summary='Every command exited with code 0.\nCheck "refused" failed: '
        "ValueError: the draft has no title",
    )


@pytest.mark.parametrize("check", ["approved", "record_branch"])
def test_a_check_the_factorys_folder_does_not_hold_fails_the_step(tmp_path, check):
    workbench = _workbench(tmp_path, None)
    write_factory(tmp_path / "factories" / "tickets", {"hooks/drafted.py": CHECK})
    step = CommandStepRun(run=["true"])

    result = asyncio.run(workbench.run(_command(uuid4(), step, PLAIN, check=check)))

    assert result == StepResult(
        ending=StepEnding.FAILED,
        summary=f'Every command exited with code 0.\nCheck "{check}" is not one of factory '
        '"tickets"\'s hooks.',
    )


IMPLEMENT = ClientStepRun(
    client=CodingClient.CLAUDE,
    prompt="Implement the issue.",
    timeout_seconds=30,
    model="claude-opus-5-5",
    effort=ReasoningEffort.HIGH,
)


def test_an_agent_step_runs_its_coding_client_in_the_run_folder_with_its_prompt(
    tmp_path, monkeypatch
):
    workbench = _workbench(tmp_path, str(_seed(tmp_path)))
    calls = stub_client(tmp_path, monkeypatch, "claude", streaming(CLAUDE_STREAM))

    result = asyncio.run(workbench.run(_command(uuid4(), IMPLEMENT, BRANCH)))

    [called] = recorded_calls(calls)
    assert result.model_copy(update={"usage": None}) == StepResult(
        ending=StepEnding.CLEAN, summary="Implemented issue 7.", session="claude-1"
    )
    assert called.cwd == str(tmp_path / "workbench" / "worktrees" / "tickets" / "issue-12")
    assert called.stdin.startswith("Implement the issue.\n")
    assert '{"work_item": {"issue": 12}, "results": {}}' in called.stdin
    assert "kinby memory" not in called.stdin
    assert called.argv[-4:] == ["--model", "claude-opus-5-5", "--effort", "high"]
    assert called.config_dir == str(tmp_path / "workbench" / ".state" / "claude")


def test_an_agent_steps_result_carries_what_its_coding_client_spent(tmp_path, monkeypatch):
    workbench = _workbench(tmp_path, None)
    write_factory(tmp_path / "factories" / "tickets", {"hooks/drafted.py": CHECK})
    stub_client(tmp_path, monkeypatch, "claude", streaming(CLAUDE_STREAM))

    result = asyncio.run(workbench.run(_command(uuid4(), IMPLEMENT, PLAIN, check="refused")))

    assert result.ending is StepEnding.FAILED
    assert result.usage is not None and result.usage.delegated_run is not None
    ran = result.usage.delegated_run
    assert (ran.usage_source, ran.models, ran.session) == (
        UsageSource.CLAUDE_SUBSCRIPTION,
        ["claude-opus-5-5"],
        "claude-1",
    )
    assert (ran.input_tokens, ran.output_tokens) == (160, 20)
    assert result.usage.api is None


def test_an_agent_step_that_resumes_continues_the_session_it_names(tmp_path, monkeypatch):
    workbench = _workbench(tmp_path, None)
    calls = stub_client(tmp_path, monkeypatch, "claude", streaming(CLAUDE_STREAM))
    fix = IMPLEMENT.model_copy(update={"resume": "claude-1"})

    asyncio.run(workbench.run(_command(uuid4(), fix, PLAIN)))

    [called] = recorded_calls(calls)
    assert called.argv[-2:] == ["--resume", "claude-1"]


def _skill(name: str, description: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\nDo it.\n"


def test_an_agent_step_offers_the_factorys_skills_and_the_workbenchs_picks(tmp_path, monkeypatch):
    workbench = _workbench(tmp_path, None)
    owned = "skills/implement-ticket/SKILL.md"
    picked = "skills/tdd/SKILL.md"
    write_factory(tmp_path / "factories" / "tickets", {owned: _skill("implement-ticket", "Ship.")})
    write_factory(tmp_path / "workbench", {picked: _skill("tdd", "Test first.")})
    calls = stub_client(tmp_path, monkeypatch, "claude", streaming(CLAUDE_STREAM))

    asyncio.run(workbench.run(_command(uuid4(), IMPLEMENT, PLAIN)))

    [called] = recorded_calls(calls)
    assert f"- implement-ticket ({tmp_path / 'factories' / 'tickets' / owned}): Ship." in (
        called.stdin
    )
    assert f"- tdd ({tmp_path / 'workbench' / picked}): Test first." in called.stdin


def test_an_agent_step_the_plan_refused_ends_limited_until_its_window_resets(tmp_path, monkeypatch):
    workbench = _workbench(tmp_path, None)
    resets_at = datetime(2030, 1, 1, 12, tzinfo=UTC)
    refused = [
        {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "rejected", "resetsAt": int(resets_at.timestamp())},
        },
        {"type": "result", "subtype": "error", "is_error": True, "session_id": "claude-2"},
    ]
    stub_client(tmp_path, monkeypatch, "claude", streaming(refused) + "sys.exit(1)")

    result = asyncio.run(workbench.run(_command(uuid4(), IMPLEMENT, PLAIN)))

    assert (result.ending, result.resets_at) == (StepEnding.LIMITED, resets_at)


def _transcript(workbench: WorkbenchSteps, run_id: UUID, cursor: int = 0) -> FactoryRunTranscript:
    command = FactoryRunTranscriptCommand(run_id=run_id, step="client", attempt=1, cursor=cursor)
    return asyncio.run(workbench.transcript(command))


def test_an_agent_steps_attempt_keeps_the_clients_events_as_its_transcript(tmp_path, monkeypatch):
    workbench = _workbench(tmp_path, None)
    cut_off = 'sys.stdout.write(\'{"type": "assist\')\n'
    stub_client(tmp_path, monkeypatch, "claude", streaming(CLAUDE_STREAM) + cut_off)
    run_id = uuid4()

    asyncio.run(workbench.run(_command(run_id, IMPLEMENT, PLAIN)))

    assert _transcript(workbench, run_id) == FactoryRunTranscript(events=CLAUDE_STREAM, cursor=None)
    assert _transcript(workbench, uuid4()) == FactoryRunTranscript(events=[], cursor=None)


def test_a_long_transcript_is_read_a_page_at_a_time(tmp_path, monkeypatch):
    workbench = _workbench(tmp_path, None)
    padded = [{"type": "assistant", "n": n, "text": "x" * 600_000} for n in range(3)]
    stub_client(tmp_path, monkeypatch, "claude", streaming(padded))
    run_id = uuid4()
    asyncio.run(workbench.run(_command(run_id, IMPLEMENT, PLAIN)))

    pages = [_transcript(workbench, run_id)]
    while (cursor := pages[-1].cursor) is not None:
        pages.append(_transcript(workbench, run_id, cursor))

    assert len(pages) > 1
    assert [event for page in pages for event in page.events] == padded


def test_an_event_too_large_for_a_page_reads_as_cut_with_its_size(tmp_path, monkeypatch):
    workbench = _workbench(tmp_path, None)
    huge = {"type": "user", "text": "x" * 5_000_000}
    stub_client(tmp_path, monkeypatch, "claude", streaming([huge, *CLAUDE_STREAM]))
    run_id = uuid4()
    asyncio.run(workbench.run(_command(run_id, IMPLEMENT, PLAIN)))

    first = _transcript(workbench, run_id)

    assert first.events[0] == {"type": "cut", "bytes": len(json.dumps(huge))}
    assert len(first.model_dump_json()) < 1024 * 1024
    assert first.events[1:] == CLAUDE_STREAM
