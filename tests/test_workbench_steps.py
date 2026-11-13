"""A workbench runs the step the hub sends in the run's folder, which it cuts when it lacks it, and
removes that folder when the hub asks."""

import asyncio
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

from kinby.contracts import (
    CodeStepRun,
    CommandStepRun,
    FactoryRunOrigin,
    RunFolder,
    RunFolderRemoveCommand,
    StepEnding,
    StepResult,
    StepRunCommand,
)
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


def _workbench(tmp_path: Path, source: str | None) -> WorkbenchSteps:
    directory = tmp_path / "workbench"
    prepare_workspace(directory / "workspace", source)
    return WorkbenchSteps(directory, tmp_path / "factories", tmp_path / "shipped")


def _command(
    run_id: UUID, step: CommandStepRun | CodeStepRun, folder: RunFolder, attempt: int = 1
) -> StepRunCommand:
    return StepRunCommand(
        step=step,
        origin=FactoryRunOrigin(factory="tickets", run_id=run_id, step=step.kind),
        attempt=attempt,
        work_item={"issue": 12},
        folder=folder,
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
