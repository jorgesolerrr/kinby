"""A workbench's runtime clones its git source at its first start and keeps the clone after."""

import subprocess
from pathlib import Path

from kinby.workbench import prepare_workspace

GIT = ("git", "-c", "user.name=seed", "-c", "user.email=seed@example.com")


def _commit(repository: Path, name: str, text: str) -> None:
    (repository / name).write_text(text, encoding="utf-8")
    subprocess.run([*GIT, "add", name], cwd=repository, check=True)
    subprocess.run([*GIT, "commit", "--quiet", "--message", name], cwd=repository, check=True)


def _seed(tmp_path: Path) -> Path:
    seed = tmp_path / "seed"
    seed.mkdir()
    subprocess.run([*GIT, "init", "--quiet", "--initial-branch=main"], cwd=seed, check=True)
    _commit(seed, "NOTES.md", "seed\n")
    return seed


def test_a_git_source_is_cloned_into_the_empty_workspace_of_a_first_start(tmp_path):
    seed = _seed(tmp_path)
    workspace = tmp_path / "workbench" / "workspace"
    workspace.mkdir(parents=True)

    prepare_workspace(workspace, str(seed))

    assert (workspace / "NOTES.md").read_text(encoding="utf-8") == "seed\n"
    assert (workspace / ".git").is_dir()


def test_a_recreation_keeps_the_clone_and_the_files_written_beside_it(tmp_path):
    seed = _seed(tmp_path)
    workspace = tmp_path / "workspace"
    prepare_workspace(workspace, str(seed))
    (workspace / "NOTES.md").write_text("edited\n", encoding="utf-8")
    installed = workspace / "node_modules" / "left-pad" / "index.js"
    installed.parent.mkdir(parents=True)
    installed.write_text("module.exports = 1;\n", encoding="utf-8")
    _commit(seed, "LATER.md", "later\n")

    prepare_workspace(workspace, str(seed))

    assert (workspace / "NOTES.md").read_text(encoding="utf-8") == "edited\n"
    assert installed.read_text(encoding="utf-8") == "module.exports = 1;\n"
    assert not (workspace / "LATER.md").exists()


def test_a_workbench_without_a_source_works_in_a_plain_folder(tmp_path):
    workspace = tmp_path / "workspace"

    prepare_workspace(workspace, None)
    (workspace / "draft.md").write_text("draft\n", encoding="utf-8")
    prepare_workspace(workspace, None)

    assert [path.name for path in workspace.iterdir()] == ["draft.md"]
