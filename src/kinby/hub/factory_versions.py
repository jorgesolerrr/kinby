"""The hub's factories folder as a git repository, whose commits are each factory's versions."""

from __future__ import annotations

import io
import os
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path

import yaml

from kinby.contracts import (
    FactoryName,
    FactoryVersion,
    FactoryVersionSource,
    GitCommit,
    StepId,
)
from kinby.core.errors import FactoryVersionNotFound
from kinby.factories.file import FACTORY_FILE, FactoryLoader

_SOURCE = "Source:"
_STEPS = "Steps:"


class FactoryVersions:
    """Commit each factory's folder as a version, and read its versions back.

    Every commit touches one factory's folder, and records where it came from and the steps it
    changed in its message. A removed factory's history ends at the commit that removed it, so a
    factory made again under its name starts its own.
    """

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def changed(self, name: FactoryName) -> bool:
        """Whether the factory's folder differs from its current version."""
        return bool(self._git("status", "--porcelain", "--untracked-files=all", "--", f"{name}/"))

    def record(self, name: FactoryName, source: FactoryVersionSource) -> None:
        """Commit the factory's folder as it is as a version, unless it holds no change."""
        self._git("add", "--all", "--", f"{name}/")
        paths = set(self._git("diff", "--cached", "--name-only", f"--relative={name}/").split("\n"))
        if paths == {""}:
            return
        before = self._file_at("HEAD", name)
        after = (self._directory / name / FACTORY_FILE).read_text(encoding="utf-8")
        steps = " ".join(_changed_steps(before, after, paths))
        self._git(
            "commit", "--quiet", "-m", f"{name}: {source}\n\n{_SOURCE} {source}\n{_STEPS} {steps}"
        )

    def removed(self, name: FactoryName) -> None:
        """Commit the removal of the factory's folder, which ends its history. A factory that never
        had a version has no history to end."""
        if not self._git("ls-files", "--", f"{name}/"):
            return
        self._git("add", "--all", "--", f"{name}/")
        self._git("commit", "--quiet", "-m", f"{name}: removed")

    def set_aside(self, name: FactoryName) -> None:
        """Move the change in the factory's folder into a stash, leaving its current version."""
        message = f"{name}: a change by hand that failed the factory check"
        self._git(
            "stash", "push", "--quiet", "--include-untracked", "-m", message, "--", f"{name}/"
        )

    def current(self, name: FactoryName) -> GitCommit | None:
        """The factory's latest version, or None when it has none."""
        return self._log(name, "-1", "--format=%H") or None

    def listed(self, name: FactoryName) -> list[FactoryVersion]:
        """The factory's versions, newest first."""
        logged = self._log(name, "--format=%H%x1f%cI%x1f%B%x1e")
        return [_version(entry) for entry in logged.split("\x1e") if entry.strip()]

    def diff(self, name: FactoryName, version: GitCommit) -> str:
        """What *version* changed in the factory's folder, with paths in the folder."""
        self._known(name, version)
        shown = self._run("show", "--format=", f"--relative={name}/", version, "--", f"{name}/")
        return shown.stdout.decode()

    def extract(self, name: FactoryName, version: GitCommit, into: Path) -> None:
        """Write the factory's folder as it is in *version* to the folder *into*."""
        self._known(name, version)
        archive = self._run("archive", "--format=tar", f"{version}:{name}").stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            # The hub wrote every version, links to its other folders included.
            tar.extractall(into, filter="tar")

    def _known(self, name: FactoryName, version: GitCommit) -> None:
        if version not in self._log(name, "--format=%H").split():
            raise FactoryVersionNotFound(f'Factory "{name}" has no version {version}.')

    def _log(self, name: FactoryName, *arguments: str) -> str:
        removal = self._git(
            "log", "-1", "--diff-filter=D", "--format=%H", "--", f"{name}/{FACTORY_FILE}"
        )
        since = f"{removal}..HEAD" if removal else "HEAD"
        return self._git("log", *arguments, since, "--", f"{name}/")

    def _file_at(self, revision: str, name: FactoryName) -> str | None:
        try:
            return self._git("show", f"{revision}:{name}/{FACTORY_FILE}")
        except subprocess.CalledProcessError:
            return None

    def _git(self, *arguments: str) -> str:
        return self._run(*arguments).stdout.decode().strip()

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        if not (self._directory / ".git").exists():
            self._initialize()
        return _git(self._directory, *arguments)

    def _initialize(self) -> None:
        """Make the folder a repository, whose first commit is empty so HEAD always names one."""
        self._directory.mkdir(parents=True, exist_ok=True)
        _git(self._directory, "init", "--quiet", "--initial-branch=main")
        # The factory check imports a factory's Python, which leaves bytecode beside it.
        (self._directory / ".git" / "info" / "exclude").write_text("__pycache__/\n")
        _git(self._directory, "commit", "--quiet", "--allow-empty", "-m", "The hub's factories")


def _git(directory: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        (
            "git",
            # The hub's folder is often a mount another user owns, which git refuses by default.
            "-c",
            f"safe.directory={directory}",
            "-c",
            "commit.gpgsign=false",
            *arguments,
        ),
        cwd=directory,
        capture_output=True,
        check=True,
        env=os.environ
        | {
            "GIT_AUTHOR_NAME": "kinby",
            "GIT_AUTHOR_EMAIL": "hub@kinby",
            "GIT_COMMITTER_NAME": "kinby",
            "GIT_COMMITTER_EMAIL": "hub@kinby",
        },
    )


def _version(entry: str) -> FactoryVersion:
    commit, time, message = entry.strip().split("\x1f")
    lines = message.splitlines()
    source = next(line for line in lines if line.startswith(_SOURCE)).removeprefix(_SOURCE)
    steps = next((line for line in lines if line.startswith(_STEPS)), _STEPS)
    return FactoryVersion(
        version=commit,
        time=datetime.fromisoformat(time),
        source=FactoryVersionSource(source.strip()),
        steps=steps.removeprefix(_STEPS).split(),
    )


def _changed_steps(before: str | None, after: str, paths: set[str]) -> list[StepId]:
    """The steps whose entry differs between the two factory files, or whose prompt is among
    *paths*: the factory's steps in order, then those it no longer has."""
    old, new = _steps(before), _steps(after)
    return [
        step_id
        for step_id in new | old
        if old.get(step_id) != new.get(step_id)
        or any(step.get("prompt") in paths for step in (old.get(step_id), new.get(step_id)) if step)
    ]


def _steps(text: str | None) -> dict[StepId, dict[str, object]]:
    declared = {} if text is None else yaml.load(text, Loader=FactoryLoader)
    return {step["id"]: step for step in declared.get("steps", [])}
