"""A workbench runs the step the hub sends in the run's folder, which it cuts when it lacks it, and
removes that folder when the hub asks."""

import asyncio
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
    IntakeRunCommand,
    IntakeRunResult,
    ReasoningEffort,
    RunFolder,
    RunFolderRemoveCommand,
    StepEnding,
    StepResult,
    StepRunCommand,
    UsageSource,
)
from kinby.core.errors import IntakeFailed
from kinby.workbench import WorkbenchSteps, prepare_workspace
from tests.test_hub_factories import write_factory
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


@hook
async def silent(end: StepEnd) -> None:
    return None
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


def _run_folder(tmp_path: Path, run_id: UUID) -> Path:
    """The directory a run works in on a plain workspace."""
    return tmp_path / "workbench" / "runs" / str(run_id)


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


NAMING = """\
import subprocess
from pathlib import Path

from kinby.plugins.tools import tool


@tool(write=False)
def agent_branch(issue: int, workspace: str) -> dict[str, str]:
    \"\"\"Name the issue's branch, stacked on the newest feature branch.\"\"\"
    subprocess.run(["git", "fetch", "--quiet", "origin"], cwd=workspace, check=True)
    (Path(workspace) / "named").write_text(str(issue))
    return {"branch": f"agent/{issue}-dark-mode", "base": "feature"}


@tool(write=False)
def no_branch(issue: int) -> dict[str, str]:
    \"\"\"Name nothing.\"\"\"
    return {"base": "main"}


@tool(write=False)
def blocked(issue: int) -> dict[str, str]:
    \"\"\"Refuse to pick a base.\"\"\"
    raise RuntimeError("No agent branch holds every open blocker's agent branch.")
"""


def _featured(tmp_path: Path) -> Path:
    """The seed, with a branch feature one commit past main."""
    seed = _seed(tmp_path)
    _git(seed, "switch", "--quiet", "-c", "feature")
    (seed / "feature.md").write_text("Stacked.\n", encoding="utf-8")
    _git(seed, "add", "feature.md")
    _git(seed, "-c", "user.name=kinby", "-c", "user.email=kinby@example.com", "commit", "-qm", "f")
    _git(seed, "switch", "--quiet", "main")
    return seed


def test_a_factorys_worktree_call_names_the_run_worktrees_branch_and_the_base_it_is_cut_from(
    tmp_path,
):
    workbench = _workbench(tmp_path, str(_featured(tmp_path)))
    write_factory(tmp_path / "factories" / "tickets", {"tools/branch.py": NAMING})
    run_id = uuid4()
    named = RunFolder(branch=None, named_by="agent_branch")

    first = asyncio.run(workbench.run(_command(run_id, CommandStepRun(run=["touch a"]), named)))
    kept = RunFolder(branch="agent/12-dark-mode", base="feature")
    second = asyncio.run(
        workbench.run(_command(run_id, CommandStepRun(run=["test -f a"]), kept, 2))
    )

    worktree = tmp_path / "workbench" / "worktrees" / "agent" / "12-dark-mode"
    assert first == StepResult(
        ending=StepEnding.CLEAN,
        summary=first.summary,
        values={"branch": "agent/12-dark-mode", "base": "feature"},
    )
    assert second.ending is StepEnding.CLEAN
    assert second.values == {}
    assert (worktree / "feature.md").is_file()
    assert _git(worktree, "branch", "--show-current") == "agent/12-dark-mode"
    assert (tmp_path / "workbench" / "workspace" / "named").read_text() == "12"


def test_a_fresh_folder_is_named_again_and_cut_again_from_the_base(tmp_path):
    workbench = _workbench(tmp_path, str(_featured(tmp_path)))
    write_factory(tmp_path / "factories" / "tickets", {"tools/branch.py": NAMING})
    run_id = uuid4()
    named = RunFolder(branch=None, named_by="agent_branch")
    asyncio.run(workbench.run(_command(run_id, CommandStepRun(run=["touch draft.md"]), named)))

    again = asyncio.run(
        workbench.run(_command(run_id, CommandStepRun(run=["true"]), named, 2, fresh=True))
    )

    worktree = tmp_path / "workbench" / "worktrees" / "agent" / "12-dark-mode"
    assert again.values == {"branch": "agent/12-dark-mode", "base": "feature"}
    assert sorted(path.name for path in worktree.iterdir()) == [".git", "NOTES.md", "feature.md"]


@pytest.mark.parametrize(
    ("call", "summary"),
    [
        ("no_branch", 'Tool "no_branch" named no branch and base for the run worktree.'),
        (
            "blocked",
            'Tool "blocked" failed: RuntimeError: No agent branch holds every open blocker\'s '
            "agent branch.",
        ),
    ],
    ids=["unnamed", "raises"],
)
def test_a_step_whose_folder_is_not_named_fails_before_it_starts(tmp_path, call, summary):
    workbench = _workbench(tmp_path, str(_seed(tmp_path)))
    write_factory(tmp_path / "factories" / "tickets", {"tools/branch.py": NAMING})
    run_id = uuid4()
    step = CommandStepRun(run=["touch started"])

    result = asyncio.run(
        workbench.run(_command(run_id, step, RunFolder(branch=None, named_by=call)))
    )

    assert result == StepResult(ending=StepEnding.FAILED, summary=summary)
    assert not (tmp_path / "workbench" / "worktrees").exists()


PLACES = """\
from kinby.plugins.tools import tool


@tool(write=False)
def places(workspace: str, folder: str) -> dict[str, str]:
    \"\"\"Say where the call runs.\"\"\"
    return {"workspace": workspace, "folder": folder}


@tool(write=False)
def scan_places(workspace: str) -> list[dict[str, str]]:
    \"\"\"Find the workspace as a work item.\"\"\"
    return [{"workspace": workspace}]
"""


def test_a_factorys_tool_takes_the_workspace_and_the_run_folder_by_name(tmp_path):
    workbench = _workbench(tmp_path, None)
    write_factory(tmp_path / "factories" / "tickets", {"tools/places.py": PLACES})
    run_id = uuid4()

    called = asyncio.run(workbench.run(_command(run_id, CodeStepRun(call="places"), PLAIN)))
    found = asyncio.run(workbench.intake(IntakeRunCommand(factory="tickets", call="scan_places")))

    workspace = str(tmp_path / "workbench" / "workspace")
    assert called.values == {
        "workspace": workspace,
        "folder": str(tmp_path / "workbench" / "runs" / str(run_id)),
    }
    assert found == IntakeRunResult(work_items=[{"workspace": workspace}])


def test_removing_a_run_worktree_removes_a_directory_named_for_the_run_beside_it(tmp_path):
    workbench = _workbench(tmp_path, str(_seed(tmp_path)))
    run_id = uuid4()
    asyncio.run(workbench.run(_command(run_id, CommandStepRun(run=["true"]), PLAIN)))
    asyncio.run(workbench.run(_command(run_id, CommandStepRun(run=["true"]), BRANCH)))

    asyncio.run(workbench.remove_folder(RunFolderRemoveCommand(run_id=run_id, folder=BRANCH)))

    assert not (tmp_path / "workbench" / "runs" / str(run_id)).exists()
    assert not (tmp_path / "workbench" / "worktrees" / "tickets" / "issue-12").exists()


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


def test_a_command_step_runs_each_command_without_a_shell(tmp_path):
    workbench = _workbench(tmp_path, None)
    run_id = uuid4()
    step = CommandStepRun(run=["touch made", "echo $HOME > redirected"])

    result = asyncio.run(workbench.run(_command(run_id, step, PLAIN)))

    assert result.ending is StepEnding.CLEAN
    assert (_run_folder(tmp_path, run_id) / "made").is_file()
    assert not (_run_folder(tmp_path, run_id) / "redirected").exists()


def test_a_command_step_fails_at_the_first_failing_command_and_names_it_with_its_outputs_tail(
    tmp_path,
):
    workbench = _workbench(tmp_path, None)
    run_id = uuid4()
    step = CommandStepRun(run=["touch made", "sh -c 'echo broken >&2; exit 3'", "touch never"])

    result = asyncio.run(workbench.run(_command(run_id, step, PLAIN)))

    assert result == StepResult(
        ending=StepEnding.FAILED,
        summary="\"sh -c 'echo broken >&2; exit 3'\" exited with code 3.\nbroken",
    )
    assert (_run_folder(tmp_path, run_id) / "made").is_file()
    assert not (_run_folder(tmp_path, run_id) / "never").exists()


def test_a_command_still_running_at_the_steps_timeout_is_killed_and_the_step_times_out(tmp_path):
    workbench = _workbench(tmp_path, None)
    run_id = uuid4()
    step = CommandStepRun(run=["sleep 30", "touch never"], timeout_seconds=1)

    result = asyncio.run(asyncio.wait_for(workbench.run(_command(run_id, step, PLAIN)), 10))

    assert result == StepResult(
        ending=StepEnding.TIMED_OUT, summary="The commands ran past the step's timeout of 1s."
    )
    assert not (_run_folder(tmp_path, run_id) / "never").exists()


def test_a_command_that_cannot_start_fails_the_step(tmp_path):
    workbench = _workbench(tmp_path, None)
    step = CommandStepRun(run=["kinby-no-such-command --help"])

    result = asyncio.run(workbench.run(_command(uuid4(), step, PLAIN)))

    assert result.ending is StepEnding.FAILED
    assert result.summary.startswith('"kinby-no-such-command --help" could not start: ')


async def _when_made(path: Path) -> None:
    """Return once *path* exists."""
    async with asyncio.timeout(5):
        while not path.exists():
            await asyncio.sleep(0.01)


def test_a_step_call_sent_again_while_it_runs_waits_for_it_and_runs_it_once(tmp_path):
    workbench = _workbench(tmp_path, None)
    run_id = uuid4()
    step = CommandStepRun(run=["sh -c 'echo ran >> runs'", "sleep 0.3"])
    command = _command(run_id, step, PLAIN)
    runs = _run_folder(tmp_path, run_id) / "runs"

    async def scenario() -> tuple[StepResult, StepResult]:
        first = asyncio.create_task(workbench.run(command))
        await _when_made(runs)
        again = await workbench.run(command)
        return again, await first

    again, first = asyncio.run(scenario())

    assert again.ending is StepEnding.CLEAN
    assert again == first
    assert runs.read_text() == "ran\n"


def test_a_step_call_sent_again_after_it_finished_returns_its_result_also_after_a_restart(
    tmp_path,
):
    workbench = _workbench(tmp_path, None)
    run_id = uuid4()
    command = _command(run_id, CommandStepRun(run=["sh -c 'echo ran >> runs; exit 3'"]), PLAIN)

    first = asyncio.run(workbench.run(command))
    again = asyncio.run(workbench.run(command))
    after_the_restart = asyncio.run(_workbench(tmp_path, None).run(command))

    assert first.ending is StepEnding.FAILED
    assert again == first
    assert after_the_restart == first
    assert (_run_folder(tmp_path, run_id) / "runs").read_text() == "ran\n"


RESTARTED = StepResult(ending=StepEnding.FAILED, summary="The instance restarted during the step.")


def test_a_step_call_its_workbench_restarted_during_answers_failed(tmp_path):
    workbench = _workbench(tmp_path, None)
    run_id = uuid4()
    step = CommandStepRun(run=["sh -c 'echo ran >> runs'", "sleep 0.3"])
    command = _command(run_id, step, PLAIN)
    runs = _run_folder(tmp_path, run_id) / "runs"

    async def scenario() -> StepResult:
        cut_short = asyncio.create_task(workbench.run(command))
        await _when_made(runs)
        answer = await _workbench(tmp_path, None).run(command)
        await cut_short
        return answer

    assert asyncio.run(scenario()) == RESTARTED
    assert runs.read_text() == "ran\n"


def test_a_step_call_its_workbench_shut_down_during_answers_failed_after_the_restart(tmp_path):
    workbench = _workbench(tmp_path, None)
    run_id = uuid4()
    command = _command(run_id, CommandStepRun(run=["sh -c 'echo ran >> runs'", "sleep 5"]), PLAIN)
    runs = _run_folder(tmp_path, run_id) / "runs"

    async def shut_down() -> bool:
        cut_short = asyncio.create_task(workbench.run(command))
        await _when_made(runs)
        # Cancel every task, as the event loop's shutdown does.
        running = asyncio.all_tasks() - {asyncio.current_task()}
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        return cut_short.cancelled()

    cancelled = asyncio.run(shut_down())
    answer = asyncio.run(_workbench(tmp_path, None).run(command))

    assert cancelled
    assert answer == RESTARTED
    assert runs.read_text() == "ran\n"


@pytest.mark.parametrize(
    ("step", "attempt"), [("review", 1), ("command", None)], ids=["next-step", "ended-call"]
)
def test_a_runs_next_step_call_lets_go_of_the_answer_it_held(tmp_path, step, attempt):
    workbench = _workbench(tmp_path, None)
    run_id = uuid4()
    command = _command(run_id, CommandStepRun(run=["sh -c 'echo ran >> runs'"]), PLAIN)
    origin = command.origin.model_copy(update={"step": step})
    next_call = _command(run_id, CommandStepRun(run=["true"]), PLAIN).model_copy(
        update={"origin": origin, "attempt": attempt}
    )

    for call in (command, next_call, command):
        asyncio.run(workbench.run(call))

    assert (_run_folder(tmp_path, run_id) / "runs").read_text() == "ran\nran\n"


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


def test_a_code_step_runs_the_tool_of_the_factory_version_its_run_keeps(tmp_path):
    workbench = _workbench(tmp_path, None)
    version = "a" * 40
    factories = tmp_path / "factories"
    write_factory(factories / "tickets", {"tools/label.py": LABEL.format(label="latest")})
    kept = factories / ".versions" / "tickets" / version
    write_factory(kept, {"tools/label.py": LABEL.format(label="kept")})
    command = _command(uuid4(), CodeStepRun(call="label_issue"), PLAIN)

    result = asyncio.run(workbench.run(command.model_copy(update={"version": version})))

    assert result.values == {"label": "kept 12"}


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


GITHUB = '''\
from kinby.plugins.hooks import HookResult
from kinby.plugins.tools import tool


@tool(write=True)
def open_pull_request(branch: str, issue: int) -> dict[str, str]:
    """Open the pull request of the branch."""
    if branch == "broken":
        raise RuntimeError("GitHub refused the pull request")
    return {"pr": f"{branch} closes {issue}"}


@tool(write=True)
def read_review(issue: int) -> HookResult:
    """Read the review of the issue's pull request."""
    return HookResult(values={"reviewed": True}, outcome="changes")
'''


def _code_step(tmp_path: Path, call: str, branch: str) -> StepResult:
    """Run the GitHub tool *call* as a code step of a run whose earlier step named *branch*."""
    workbench = _workbench(tmp_path, None)
    write_factory(tmp_path / "factories" / "tickets", {"tools/github.py": GITHUB})
    command = _command(uuid4(), CodeStepRun(call=call), PLAIN)
    return asyncio.run(workbench.run(command.model_copy(update={"results": {"branch": branch}})))


def test_a_code_step_calls_its_tool_with_the_work_item_and_the_runs_values(tmp_path):
    result = _code_step(tmp_path, "open_pull_request", "agent/12")

    assert result == StepResult(ending=StepEnding.CLEAN, values={"pr": "agent/12 closes 12"})


def test_a_code_steps_tool_names_an_outcome_by_returning_a_hook_result(tmp_path):
    result = _code_step(tmp_path, "read_review", "agent/12")

    assert result == StepResult(
        ending=StepEnding.CLEAN, outcome="changes", values={"reviewed": True}
    )


def test_a_code_step_whose_tool_raises_fails_and_says_why(tmp_path):
    result = _code_step(tmp_path, "open_pull_request", "broken")

    assert result == StepResult(
        ending=StepEnding.FAILED,
        summary='Tool "open_pull_request" failed: RuntimeError: GitHub refused the pull request',
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


def test_a_check_that_records_nothing_leaves_the_steps_own_result(tmp_path):
    workbench = _workbench(tmp_path, None)
    write_factory(tmp_path / "factories" / "tickets", {"hooks/drafted.py": CHECK})
    step = CommandStepRun(run=["true"])

    result = asyncio.run(workbench.run(_command(uuid4(), step, PLAIN, check="silent")))

    assert result == StepResult(
        ending=StepEnding.CLEAN, summary="Every command exited with code 0."
    )


@pytest.mark.parametrize(
    ("check", "reason"),
    [
        ("refused", 'Check "refused" failed: ValueError: the draft has no title'),
        ("approved", 'Check "approved" is not one of factory "tickets"\'s hooks.'),
    ],
    ids=["raises", "missing"],
)
def test_a_timed_out_step_whose_check_fails_still_times_out(tmp_path, check, reason):
    workbench = _workbench(tmp_path, None)
    write_factory(tmp_path / "factories" / "tickets", {"hooks/drafted.py": CHECK})
    step = CommandStepRun(run=["sleep 30"], timeout_seconds=1)

    result = asyncio.run(
        asyncio.wait_for(workbench.run(_command(uuid4(), step, PLAIN, check=check)), 10)
    )

    assert result == StepResult(
        ending=StepEnding.TIMED_OUT,
        summary=f"The commands ran past the step's timeout of 1s.\n{reason}",
    )


LARGE = """\
from kinby.plugins.hooks import HookResult, StepEnd, hook


@hook
def long_winded(end: StepEnd) -> HookResult:
    raise RuntimeError("first words " + "x" * 5_000_000 + " last words")


@hook
def heavy(end: StepEnd) -> HookResult:
    return HookResult(values={"diff": "x" * 2_000_000})
"""


def test_a_long_summary_keeps_its_start_and_its_end_and_says_how_much_was_cut(tmp_path):
    workbench = _workbench(tmp_path, None)
    write_factory(tmp_path / "factories" / "tickets", {"hooks/large.py": LARGE})
    step = CommandStepRun(run=["true"])

    result = asyncio.run(workbench.run(_command(uuid4(), step, PLAIN, check="long_winded")))

    assert result.ending is StepEnding.FAILED
    assert len(result.summary) < 16_100
    assert result.summary.startswith(
        'Every command exited with code 0.\nCheck "long_winded" failed: RuntimeError: first '
    )
    assert result.summary.endswith("x last words")
    assert re.search(r"\n\[4,98\d,\d{3} characters cut\]\n", result.summary)


def test_a_result_too_large_to_send_fails_the_step_and_names_its_size(tmp_path):
    workbench = _workbench(tmp_path, None)
    write_factory(tmp_path / "factories" / "tickets", {"hooks/large.py": LARGE})
    step = CommandStepRun(run=["true"])

    result = asyncio.run(workbench.run(_command(uuid4(), step, PLAIN, check="heavy")))

    assert result.ending is StepEnding.FAILED
    assert result.values == {}
    assert re.fullmatch(
        r"Every command exited with code 0\.\n"
        r"The step's result is 2,000,\d{3} bytes, over the 1,048,576-byte limit\.",
        result.summary,
    )


def test_a_check_the_factorys_folder_does_not_hold_fails_the_step(tmp_path):
    check = "approved"
    workbench = _workbench(tmp_path, None)
    write_factory(tmp_path / "factories" / "tickets", {"hooks/drafted.py": CHECK})
    step = CommandStepRun(run=["true"])

    result = asyncio.run(workbench.run(_command(uuid4(), step, PLAIN, check=check)))

    assert result == StepResult(
        ending=StepEnding.FAILED,
        summary=f'Every command exited with code 0.\nCheck "{check}" is not one of factory '
        '"tickets"\'s hooks.',
    )


#: What Claude Code streams for a run that implemented the issue in session claude-1.
CLAUDE_STREAM = [
    {"type": "system", "subtype": "init", "session_id": "claude-1"},
    {"type": "assistant", "message": {"id": "m1", "model": "claude-opus-5-5", "usage": {}}},
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "session_id": "claude-1",
        "result": "Implemented issue 7.",
        "num_turns": 3,
        "modelUsage": {
            "claude-opus-5-5": {
                "inputTokens": 100,
                "outputTokens": 20,
                "cacheReadInputTokens": 50,
                "cacheCreationInputTokens": 10,
            }
        },
    },
]
#: What Codex streams for the same run, in thread codex-1.
CODEX_STREAM = [
    {"type": "thread.started", "thread_id": "codex-1"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": "Done."}},
    {
        "type": "item.completed",
        "item": {"id": "i2", "type": "agent_message", "text": "Implemented issue 7."},
    },
    {
        "type": "turn.completed",
        "usage": {"input_tokens": 160, "cached_input_tokens": 50, "output_tokens": 20},
    },
]
#: The API keys a client would bill over its subscription login, all set for every stub client.
API_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY")


def stub_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, then: str) -> Path:
    """Put an executable *name* first on PATH that records how it was called, then runs *then*.

    Returns the file each call is recorded in, one JSON object per line.
    """
    calls = tmp_path / f"{name}.calls"
    executable = tmp_path / "bin" / name
    executable.parent.mkdir(exist_ok=True)
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys, time\n"
        f"with open({str(calls)!r}, 'a') as calls:\n"
        "    json.dump({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'stdin': sys.stdin.read(),\n"
        f"               'api_keys': [name for name in {API_KEYS!r} if name in os.environ],\n"
        "               'config_dir': os.environ.get('CLAUDE_CONFIG_DIR'),\n"
        "               'no_background':\n"
        "                   os.environ.get('CLAUDE_CODE_DISABLE_BACKGROUND_TASKS'),\n"
        "               'command_limit': os.environ.get('BASH_MAX_TIMEOUT_MS'),\n"
        "               'command_default': os.environ.get('BASH_DEFAULT_TIMEOUT_MS'),\n"
        "               'kinby': {name: value for name, value in os.environ.items()\n"
        "                         if name.startswith('KINBY_')}},\n"
        "              calls)\n"
        "    calls.write('\\n')\n"
        f"{then}\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{executable.parent}{os.pathsep}{os.environ['PATH']}")
    for variable in API_KEYS:
        monkeypatch.setenv(variable, "sk-test")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", raising=False)
    monkeypatch.delenv("BASH_MAX_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("BASH_DEFAULT_TIMEOUT_MS", raising=False)
    return calls


def streaming(events: Sequence[Mapping[str, object]]) -> str:
    """The stub's body that streams *events* as JSON lines and exits with code zero."""
    return "".join(f"print({json.dumps(json.dumps(event))})\n" for event in events)


@dataclass(frozen=True)
class ClientCall:
    """How the stub client was called."""

    argv: list[str]
    cwd: str
    stdin: str
    #: The API keys the client saw.
    api_keys: list[str]
    #: Where Claude keeps its sessions, when the client was told.
    config_dir: str | None
    #: Set when Claude may leave no command running in the background.
    no_background: str | None
    #: The longest Claude may wait for one command, in milliseconds.
    command_limit: str | None
    #: How long Claude waits for a command that names no timeout, in milliseconds.
    command_default: str | None
    #: The kinby variables the client saw, by name.
    kinby: dict[str, str]


def recorded_calls(calls: Path) -> list[ClientCall]:
    return [ClientCall(**json.loads(line)) for line in calls.read_text().splitlines()]


IMPLEMENT = ClientStepRun(
    client=CodingClient.CLAUDE,
    prompt="Implement the issue.",
    timeout_seconds=30,
    model="claude-opus-5-5",
    effort=ReasoningEffort.HIGH,
)
CODEX_IMPLEMENT = IMPLEMENT.model_copy(
    update={"client": CodingClient.CODEX, "model": "gpt-5.5-codex"}
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
    assert called.argv[-4:] == ["--model", "claude-opus-5-5", "--effort", "high"]
    assert called.config_dir == str(tmp_path / "workbench" / ".state" / "claude")


def test_an_agent_step_tells_its_coding_client_how_to_search_the_owners_memory_for_the_run(
    tmp_path, monkeypatch
):
    workbench = _workbench(tmp_path, None)
    calls = stub_client(tmp_path, monkeypatch, "claude", streaming(CLAUDE_STREAM))
    route = {
        "KINBY_HUB_URL": "http://hub:8080/instances/cec/hub",
        "KINBY_HUB_SCOPES": "workbench:memory",
        "KINBY_CONTROL_TOKEN": "control",
    }
    for name, value in route.items():
        monkeypatch.setenv(name, value)
    run_id = uuid4()

    asyncio.run(workbench.run(_command(run_id, IMPLEMENT, PLAIN, attempt=2)))

    [called] = recorded_calls(calls)
    assert "kinby memory search <query>" in called.stdin
    assert "kinby memory open <node>" in called.stdin
    assert "the owner of your run's line" in called.stdin
    session = route | {"KINBY_RUN": str(run_id), "KINBY_STEP": IMPLEMENT.kind, "KINBY_ATTEMPT": "2"}
    assert {name: called.kinby.get(name) for name in session} == session


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


@pytest.mark.parametrize(
    ("step", "stream", "session", "arguments"),
    [
        (IMPLEMENT, CLAUDE_STREAM, "claude-1", ["--resume", "claude-1"]),
        (CODEX_IMPLEMENT, CODEX_STREAM, "codex-1", ["exec", "resume", "codex-1", "-"]),
    ],
    ids=["claude", "codex"],
)
def test_an_agent_step_that_resumes_continues_the_session_it_names(
    tmp_path, monkeypatch, step, stream, session, arguments
):
    workbench = _workbench(tmp_path, None)
    calls = stub_client(tmp_path, monkeypatch, step.client, streaming(stream))
    fix = step.model_copy(update={"resume": session})

    result = asyncio.run(workbench.run(_command(uuid4(), fix, PLAIN)))

    [called] = recorded_calls(calls)
    assert (result.ending, result.session) == (StepEnding.CLEAN, session)
    assert [argument for argument in called.argv if argument in arguments] == arguments


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


@pytest.mark.parametrize("check", [None, "approved"], ids=["unchecked", "missing-check"])
def test_an_agent_step_the_plan_refused_ends_limited_until_its_window_resets(
    tmp_path, monkeypatch, check
):
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

    result = asyncio.run(workbench.run(_command(uuid4(), IMPLEMENT, PLAIN, check=check)))

    assert (result.ending, result.resets_at) == (StepEnding.LIMITED, resets_at)


@pytest.mark.parametrize(
    ("step", "stream", "session", "arguments", "kept", "config_dir"),
    [
        (
            IMPLEMENT,
            CLAUDE_STREAM,
            "claude-1",
            ["-p", "--permission-mode", "acceptEdits"],
            ["OPENAI_API_KEY", "CODEX_API_KEY"],
            "claude",
        ),
        (
            CODEX_IMPLEMENT,
            CODEX_STREAM,
            "codex-1",
            ["exec", "--json", "--dangerously-bypass-approvals-and-sandbox"],
            ["ANTHROPIC_API_KEY"],
            None,
        ),
    ],
    ids=["claude", "codex"],
)
def test_an_agent_step_runs_either_coding_client_on_its_login_and_keeps_its_session(
    tmp_path, monkeypatch, step, stream, session, arguments, kept, config_dir
):
    workbench = _workbench(tmp_path, None)
    calls = stub_client(tmp_path, monkeypatch, step.client, streaming(stream))
    run_id = uuid4()

    result = asyncio.run(workbench.run(_command(run_id, step, PLAIN)))

    [called] = recorded_calls(calls)
    assert result.model_copy(update={"usage": None}) == StepResult(
        ending=StepEnding.CLEAN, summary="Implemented issue 7.", session=session
    )
    assert called.argv[: len(arguments)] == arguments
    assert called.cwd == str(_run_folder(tmp_path, run_id))
    assert called.api_keys == kept
    assert called.config_dir == (config_dir and str(tmp_path / "workbench" / ".state" / config_dir))


@pytest.mark.parametrize(
    ("step", "flags"),
    [
        (IMPLEMENT, [["--model", "claude-opus-5-5"], ["--effort", "high"]]),
        (
            CODEX_IMPLEMENT,
            [["--model", "gpt-5.5-codex"], ["--config", "model_reasoning_effort=high"]],
        ),
    ],
    ids=["claude", "codex"],
)
def test_an_agent_step_runs_its_coding_client_with_the_steps_model_and_effort(
    tmp_path, monkeypatch, step, flags
):
    workbench = _workbench(tmp_path, None)
    stream = CLAUDE_STREAM if step.client is CodingClient.CLAUDE else CODEX_STREAM
    calls = stub_client(tmp_path, monkeypatch, step.client, streaming(stream))

    asyncio.run(workbench.run(_command(uuid4(), step, PLAIN)))

    [called] = recorded_calls(calls)
    for flag in flags:
        at = called.argv.index(flag[0])
        assert called.argv[at : at + 2] == flag


def test_a_claude_step_runs_every_command_in_the_foreground_for_as_long_as_the_step_may_take(
    tmp_path, monkeypatch
):
    workbench = _workbench(tmp_path, None)
    calls = stub_client(tmp_path, monkeypatch, "claude", streaming(CLAUDE_STREAM))

    asyncio.run(workbench.run(_command(uuid4(), IMPLEMENT, PLAIN)))

    [called] = recorded_calls(calls)
    assert (called.no_background, called.command_limit, called.command_default) == (
        "1",
        "30000",
        "30000",
    )


def test_an_agent_steps_prompt_has_no_skills_catalogue_when_there_are_no_skills(
    tmp_path, monkeypatch
):
    workbench = _workbench(tmp_path, None)
    calls = stub_client(tmp_path, monkeypatch, "claude", streaming(CLAUDE_STREAM))

    asyncio.run(workbench.run(_command(uuid4(), IMPLEMENT, PLAIN)))

    [called] = recorded_calls(calls)
    assert "# Skills" not in called.stdin


def test_an_agent_steps_prompt_says_to_keep_a_lingering_process_off_a_pipe(tmp_path, monkeypatch):
    workbench = _workbench(tmp_path, None)
    calls = stub_client(tmp_path, monkeypatch, "claude", streaming(CLAUDE_STREAM))

    asyncio.run(workbench.run(_command(uuid4(), IMPLEMENT, PLAIN)))

    [called] = recorded_calls(calls)
    assert "# Commands" in called.stdin
    assert "to a file" in called.stdin


QUICK = IMPLEMENT.model_copy(update={"timeout_seconds": 1})


@pytest.mark.parametrize(
    ("step", "then", "ending", "session"),
    [
        (IMPLEMENT, streaming(CLAUDE_STREAM), StepEnding.CLEAN, "claude-1"),
        (IMPLEMENT, 'print(\'{"type": "result", "subtype": "succ\')', StepEnding.CLEAN, None),
        (QUICK, streaming(CLAUDE_STREAM[:2]) + "time.sleep(10)", StepEnding.TIMED_OUT, None),
        (
            IMPLEMENT,
            streaming(CLAUDE_STREAM[:2]) + "sys.stdout.flush()\nos.kill(os.getpid(), 9)",
            StepEnding.FAILED,
            None,
        ),
    ],
    ids=["clean", "malformed", "timeout", "kill"],
)
def test_an_agent_steps_check_records_its_result_however_the_client_ended(
    tmp_path, monkeypatch, step, then, ending, session
):
    workbench = _workbench(tmp_path, None)
    write_factory(tmp_path / "factories" / "tickets", {"hooks/drafted.py": CHECK})
    stub_client(tmp_path, monkeypatch, "claude", then)

    result = asyncio.run(workbench.run(_command(uuid4(), step, PLAIN, check="drafted")))

    assert result.ending is ending
    assert result.outcome == "redraft"
    assert result.values == {"ending": ending.value, "drafted": False, "issue": 12}
    assert result.session == session


def test_a_client_that_runs_past_its_timeout_is_killed_and_ends_the_attempt_as_a_timeout(
    tmp_path, monkeypatch
):
    workbench = _workbench(tmp_path, None)
    stub_client(tmp_path, monkeypatch, "codex", "time.sleep(1.5)\nopen('late', 'w').close()")
    run_id = uuid4()
    step = CODEX_IMPLEMENT.model_copy(update={"timeout_seconds": 1})

    async def scenario() -> StepResult:
        result = await workbench.run(_command(run_id, step, PLAIN))
        await asyncio.sleep(1)
        return result

    result = asyncio.run(scenario())

    assert (result.ending, result.summary) == (
        StepEnding.TIMED_OUT,
        "codex ran past its 1-second timeout and was killed.",
    )
    assert not (_run_folder(tmp_path, run_id) / "late").exists()


def test_an_agent_step_kills_what_its_client_left_running_outside_its_process_group(
    tmp_path, monkeypatch
):
    workbench = _workbench(tmp_path, None)
    left = (
        "import subprocess\n"
        "subprocess.Popen([sys.executable, '-c',"
        ' \'import time; time.sleep(1); open("late", "w").close()\'],'
        " start_new_session=True)\n"
    )
    stub_client(tmp_path, monkeypatch, "claude", left + streaming(CLAUDE_STREAM))
    run_id = uuid4()

    async def scenario() -> StepResult:
        result = await workbench.run(_command(run_id, IMPLEMENT, PLAIN))
        await asyncio.sleep(1.5)
        return result

    assert asyncio.run(scenario()).ending is StepEnding.CLEAN
    assert not (_run_folder(tmp_path, run_id) / "late").exists()


def _resume_failing(error: str) -> str:
    """The stub's body that exits 1 with *error* when resuming, and otherwise implements."""
    return (
        "if '--resume' in sys.argv:\n"
        f"    sys.stderr.write({error!r})\n"
        "    sys.exit(1)\n"
        f"{streaming(CLAUDE_STREAM)}"
    )


LOST = "No conversation found with session ID: claude-0\n"


def test_a_claude_step_whose_session_was_lost_runs_once_more_in_a_new_session(
    tmp_path, monkeypatch
):
    workbench = _workbench(tmp_path, None)
    calls = stub_client(tmp_path, monkeypatch, "claude", _resume_failing(LOST))
    fix = IMPLEMENT.model_copy(update={"resume": "claude-0"})

    result = asyncio.run(workbench.run(_command(uuid4(), fix, PLAIN)))

    assert result.model_copy(update={"usage": None}) == StepResult(
        ending=StepEnding.CLEAN,
        summary="Implemented issue 7.\nSession claude-0 was lost, so claude ran in a new one.",
        session="claude-1",
    )
    resumed, started = recorded_calls(calls)
    assert resumed.argv[-2:] == ["--resume", "claude-0"]
    assert "--resume" not in started.argv
    assert started.stdin == resumed.stdin


def test_a_claude_step_whose_session_was_lost_runs_again_only_until_the_steps_timeout(
    tmp_path, monkeypatch
):
    workbench = _workbench(tmp_path, None)
    stub_client(tmp_path, monkeypatch, "claude", "time.sleep(0.6)\n" + _resume_failing(LOST))
    fix = QUICK.model_copy(update={"resume": "claude-0"})

    result = asyncio.run(workbench.run(_command(uuid4(), fix, PLAIN)))

    assert (result.ending, result.summary) == (
        StepEnding.TIMED_OUT,
        "claude ran past its 1-second timeout and was killed.\n"
        "Session claude-0 was lost, so claude ran in a new one.",
    )


def test_a_resumed_claude_step_that_fails_otherwise_runs_only_once(tmp_path, monkeypatch):
    workbench = _workbench(tmp_path, None)
    calls = stub_client(tmp_path, monkeypatch, "claude", _resume_failing("Credit balance low\n"))
    fix = IMPLEMENT.model_copy(update={"resume": "claude-0"})

    result = asyncio.run(workbench.run(_command(uuid4(), fix, PLAIN)))

    assert (result.ending, result.summary) == (
        StepEnding.FAILED,
        "claude exited with code 1.\nCredit balance low",
    )
    assert len(recorded_calls(calls)) == 1


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


SCAN = """\
from kinby.plugins.tools import tool


@tool(write=True)
def scan_issues(signal: dict[str, object]) -> list[dict[str, int]] | None:
    \"\"\"Find the issues the delivery labeled.\"\"\"
    body = signal.get("body")
    if not isinstance(body, dict):
        return None
    return [{"issue": number} for number in body["labeled"]]
"""


def test_an_intake_runs_the_factorys_tool_with_its_delivery_and_returns_its_work_items(tmp_path):
    workbench = _workbench(tmp_path, None)
    write_factory(tmp_path / "factories" / "tickets", {"tools/scan.py": SCAN})
    delivered = {"headers": {"x-github-event": "issues"}, "body": {"labeled": [12, 13]}}

    found = asyncio.run(
        workbench.intake(IntakeRunCommand(factory="tickets", call="scan_issues", signal=delivered))
    )
    scheduled = asyncio.run(
        workbench.intake(IntakeRunCommand(factory="tickets", call="scan_issues"))
    )

    assert found == IntakeRunResult(work_items=[{"issue": 12}, {"issue": 13}])
    assert scheduled == IntakeRunResult(work_items=[])


@pytest.mark.parametrize(
    ("call", "refusal"),
    [
        ("close_issue", 'Tool "close_issue" is not one of factory "tickets"\'s tools.'),
        ("scan_issues", "Tool \"scan_issues\" failed: KeyError: 'labeled'"),
    ],
    ids=["missing", "raises"],
)
def test_an_intake_whose_tool_is_missing_or_raises_fails_and_says_why(tmp_path, call, refusal):
    workbench = _workbench(tmp_path, None)
    write_factory(tmp_path / "factories" / "tickets", {"tools/scan.py": SCAN})
    command = IntakeRunCommand(factory="tickets", call=call, signal={"body": {}})

    with pytest.raises(IntakeFailed) as failed:
        asyncio.run(workbench.intake(command))

    assert str(failed.value) == refusal
