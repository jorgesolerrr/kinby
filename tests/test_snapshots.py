import asyncio
import logging
import shutil
import subprocess
from pathlib import Path

import pytest

from kinby.contracts import TreeId
from kinby.core.snapshots import (
    SNAPSHOTS_DIR,
    SnapshotError,
    SnapshotRef,
    WorkspaceSnapshots,
)

_BEFORE = SnapshotRef("refs/kinby/snapshots/thread/turn/before")
_AFTER = SnapshotRef("refs/kinby/snapshots/thread/turn/after")


async def _opened(tmp_path: Path) -> WorkspaceSnapshots:
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.md").write_text("first\n", encoding="utf-8")
    store = await WorkspaceSnapshots.open(state_dir, workspace, enabled=True)
    assert store is not None
    return store


def _captured_paths(tmp_path: Path, tree: TreeId) -> list[str]:
    listing = subprocess.run(
        [
            "git",
            f"--git-dir={tmp_path / '.state' / SNAPSHOTS_DIR}",
            "ls-tree",
            "-r",
            "--name-only",
            tree,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return listing.stdout.split()


def test_two_captures_of_an_unchanged_workspace_return_the_same_tree(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = await _opened(tmp_path)

        before = await store.capture(_BEFORE)
        after = await store.capture(_AFTER)

        assert before == after
        assert len(before) == 40

    asyncio.run(scenario())


def test_a_capture_after_an_edit_returns_a_different_tree(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = await _opened(tmp_path)

        before = await store.capture(_BEFORE)
        (tmp_path / "workspace" / "notes.md").write_text("second\n", encoding="utf-8")
        after = await store.capture(_AFTER)

        assert before != after

    asyncio.run(scenario())


def test_a_capture_takes_untracked_files_and_leaves_ignored_ones_out(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = await _opened(tmp_path)
        workspace = tmp_path / "workspace"
        (workspace / ".gitignore").write_text("secrets.env\n", encoding="utf-8")
        (workspace / "untracked.md").write_text("new\n", encoding="utf-8")
        (workspace / "secrets.env").write_text("KEY=1\n", encoding="utf-8")

        tree = await store.capture(_BEFORE)

        assert _captured_paths(tmp_path, tree) == [".gitignore", "notes.md", "untracked.md"]

    asyncio.run(scenario())


def test_a_workspace_that_is_a_git_repository_never_has_its_git_captured(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = await _opened(tmp_path)
        subprocess.run(["git", "init", "--quiet", str(tmp_path / "workspace")], check=True)

        tree = await store.capture(_BEFORE)

        assert _captured_paths(tmp_path, tree) == ["notes.md"]

    asyncio.run(scenario())


def test_snapshots_turned_off_open_no_store_and_no_repository(tmp_path: Path) -> None:
    async def scenario() -> None:
        state_dir = tmp_path / ".state"
        state_dir.mkdir()

        store = await WorkspaceSnapshots.open(state_dir, tmp_path / "workspace", enabled=False)

        assert store is None
        assert not (state_dir / SNAPSHOTS_DIR).exists()

    asyncio.run(scenario())


def test_a_missing_git_binary_opens_no_store_and_warns_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        state_dir = tmp_path / ".state"
        state_dir.mkdir()
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))

        with caplog.at_level(logging.WARNING, logger="kinby.core.snapshots"):
            store = await WorkspaceSnapshots.open(state_dir, tmp_path / "workspace", enabled=True)

        assert store is None
        assert not (state_dir / SNAPSHOTS_DIR).exists()
        assert [record.message for record in caplog.records] == [
            "Git is not installed, so kinby records no workspace snapshots."
        ]

    asyncio.run(scenario())


def test_a_capture_of_a_missing_workspace_raises_a_snapshot_error(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = await _opened(tmp_path)
        shutil.rmtree(tmp_path / "workspace")

        with pytest.raises(SnapshotError):
            await store.capture(_BEFORE)

    asyncio.run(scenario())


def test_a_file_ignored_after_it_was_captured_drops_out_of_later_captures(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = await _opened(tmp_path)
        workspace = tmp_path / "workspace"
        (workspace / "secrets.env").write_text("KEY=1\n", encoding="utf-8")
        await store.capture(_BEFORE)

        (workspace / ".gitignore").write_text("secrets.env\n", encoding="utf-8")
        tree = await store.capture(_AFTER)

        assert _captured_paths(tmp_path, tree) == [".gitignore", "notes.md"]

    asyncio.run(scenario())


def test_a_capture_keeps_forty_hex_characters_when_git_defaults_to_sha256(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("GIT_DEFAULT_HASH", "sha256")
        store = await _opened(tmp_path)

        tree = await store.capture(_BEFORE)

        assert len(tree) == 40

    asyncio.run(scenario())


def test_a_capture_cancelled_while_git_runs_leaves_no_git_behind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interrupt cancels the capture, so git must be done before the next one starts."""

    async def scenario() -> None:
        store = await _opened(tmp_path)
        spawn = asyncio.create_subprocess_exec
        started: list[asyncio.subprocess.Process] = []
        running = asyncio.Event()

        async def watched(
            program: str,
            *arguments: str,
            cwd: Path | None = None,
            stdout: int | None = None,
            stderr: int | None = None,
        ) -> asyncio.subprocess.Process:
            process = await spawn(program, *arguments, cwd=cwd, stdout=stdout, stderr=stderr)
            started.append(process)
            running.set()
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", watched)
        capturing = asyncio.create_task(store.capture(_BEFORE))
        await running.wait()
        capturing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await capturing

        assert started
        assert all(process.returncode is not None for process in started)

    asyncio.run(scenario())


def _ref_tree(tmp_path: Path, ref: SnapshotRef) -> str:
    shown = subprocess.run(
        [
            "git",
            f"--git-dir={tmp_path / '.state' / SNAPSHOTS_DIR}",
            "rev-parse",
            f"{ref}^{{tree}}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return shown.stdout.strip()


def test_a_capture_leaves_the_ref_where_plain_git_reads_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = await _opened(tmp_path)

        before = await store.capture(_BEFORE)
        (tmp_path / "workspace" / "notes.md").write_text("second\n", encoding="utf-8")
        after = await store.capture(_AFTER)

        assert _ref_tree(tmp_path, _BEFORE) == before
        assert _ref_tree(tmp_path, _AFTER) == after

    asyncio.run(scenario())


def test_captures_asked_for_at_once_do_not_fight_over_the_index(tmp_path: Path) -> None:
    """Turns on different threads share one workspace, one index and one lock file."""

    async def scenario() -> None:
        store = await _opened(tmp_path)
        workspace = tmp_path / "workspace"
        # Enough files that the git calls overlap, or they never reach for the lock at once.
        for number in range(200):
            (workspace / f"note{number}.md").write_text(f"{number}\n", encoding="utf-8")

        trees = await asyncio.gather(
            *(
                store.capture(SnapshotRef(f"refs/kinby/snapshots/thread/turn{number}/after"))
                for number in range(5)
            )
        )

        assert len(set(trees)) == 1

    asyncio.run(scenario())
