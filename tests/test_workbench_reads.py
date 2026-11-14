"""A workbench answers the reads its owner sends through the hub: a file and a directory of its
workspace, and a command from the shell reads, run there."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from kinby.contracts import (
    WorkbenchCommandRunCommand,
    WorkbenchDirectoryListCommand,
    WorkbenchFileReadCommand,
)
from kinby.core.errors import WorkspaceReadRefused
from kinby.workbench import WorkbenchSteps
from tests.test_workbench_steps import _workbench

WORKBENCH = uuid4()


def _seeded(tmp_path: Path) -> WorkbenchSteps:
    workbench = _workbench(tmp_path, None)
    workspace = tmp_path / "workbench" / "workspace"
    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (workspace / "README.md").write_text("# App\n", encoding="utf-8")
    (tmp_path / "workbench" / ".env").write_text("GH_TOKEN=secret\n", encoding="utf-8")
    return workbench


def test_the_owner_reads_a_file_of_the_workspace(tmp_path):
    workbench = _seeded(tmp_path)

    read = asyncio.run(
        workbench.read_file(WorkbenchFileReadCommand(workbench_id=WORKBENCH, path="src/app.py"))
    )

    assert read.text == "print('hello')\n"


def test_the_owner_lists_a_directory_of_the_workspace_each_directory_ending_in_a_slash(tmp_path):
    workbench = _seeded(tmp_path)

    listed = asyncio.run(
        workbench.list_directory(WorkbenchDirectoryListCommand(workbench_id=WORKBENCH))
    )

    assert listed.entries == ["README.md", "src/"]


@pytest.mark.parametrize(
    "read",
    [
        lambda workbench: workbench.read_file(
            WorkbenchFileReadCommand(workbench_id=WORKBENCH, path="../.env")
        ),
        lambda workbench: workbench.list_directory(
            WorkbenchDirectoryListCommand(workbench_id=WORKBENCH, path="..")
        ),
    ],
    ids=["file", "directory"],
)
def test_a_read_outside_the_workspace_is_refused(tmp_path, read):
    workbench = _seeded(tmp_path)

    with pytest.raises(WorkspaceReadRefused, match="outside the workspace"):
        asyncio.run(read(workbench))


def test_a_shell_read_runs_in_the_workspace(tmp_path):
    workbench = _seeded(tmp_path)

    ran = asyncio.run(
        workbench.run_command(
            WorkbenchCommandRunCommand(workbench_id=WORKBENCH, command="grep -rn hello src")
        )
    )

    assert ran.output == "src/app.py:1:print('hello')\n"


@pytest.mark.parametrize(
    "command", ["rm README.md", "git log", "cat ../.env", "uv sync", "echo $GH_TOKEN"]
)
def test_any_other_command_is_refused_and_never_runs(tmp_path, command):
    workbench = _seeded(tmp_path)

    with pytest.raises(WorkspaceReadRefused, match="is no shell read"):
        asyncio.run(
            workbench.run_command(
                WorkbenchCommandRunCommand(workbench_id=WORKBENCH, command=command)
            )
        )

    assert (tmp_path / "workbench" / "workspace" / "README.md").is_file()
