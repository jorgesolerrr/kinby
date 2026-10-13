"""Skills in a git repository, which the user names by an address in the ``skills`` CLI's syntax."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from kinby.contracts import GitCommit, SkillName
from kinby.core.errors import InvalidConfig
from kinby.instance.layout import SKILL_FILE, SKILLS_DIR
from kinby.plugins.instance_tools import validate_name
from kinby.plugins.skills import load_skill_file

_GITHUB_TREE = re.compile(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/tree/([^/]+)(?:/(.+))?")
_SHORTHAND = re.compile(r"[\w.-]+/[\w.-]+(?:/[^/]+)*")


@dataclass(frozen=True)
class Address:
    url: str
    #: The folder the skills are looked for in, None for the repository's root.
    path: PurePosixPath | None
    #: A branch or tag. None is the repository's default branch.
    ref: str | None


def parse_address(text: str) -> Address:
    """Read ``owner/repo``, ``owner/repo/path`` or a URL, each with an optional ``#ref``.

    A URL is a local path, a GitHub tree URL, or anything else git clones.
    """
    location, _, fragment = text.strip().partition("#")
    ref = fragment or None
    if tree := _GITHUB_TREE.fullmatch(location.rstrip("/")):
        owner, repo, branch, path = tree.groups()
        return Address(f"https://github.com/{owner}/{repo}.git", _path(path), ref or branch)
    if "://" in location:
        return Address(_without_credentials(location), None, ref)
    if location.startswith(("/", ".", "git@")):
        return Address(location, None, ref)
    if _SHORTHAND.fullmatch(location):
        owner, repo, *path = location.split("/")
        return Address(f"https://github.com/{owner}/{repo}.git", _path("/".join(path)), ref)
    raise InvalidConfig(
        {
            "address": f'"{text}" is not an address. Give owner/repo, owner/repo/path or a URL, '
            "each with an optional #ref."
        }
    )


def _without_credentials(url: str) -> str:
    """*url*, once it carries no password, nor a user over HTTP, where a user is a token.

    The hub records the URL and lists it to every reader, so a credential belongs to git's own
    configuration on the hub.
    """
    parts = urlsplit(url)
    if parts.password is not None or (parts.scheme in {"http", "https"} and parts.username):
        raise InvalidConfig(
            {
                "address": "The URL carries a credential. Give it without one, and let git's "
                "credential helper on the hub supply it."
            }
        )
    return url


def _path(text: str | None) -> PurePosixPath | None:
    if not text:
        return None
    path = PurePosixPath(text.strip("/"))
    if ".." in path.parts:
        raise InvalidConfig({"address": f'The path "{text}" leads out of the repository.'})
    return path


@contextmanager
def checked_out(url: str, revision: str) -> Iterator[tuple[GitCommit, Path]]:
    """The commit *revision* resolves to in the repository at *url*, and its files in a temporary
    folder. *revision* is a branch, a tag, ``HEAD`` or a commit."""
    with TemporaryDirectory(prefix="kinby-skills-") as temporary:
        repository = Path(temporary) / "repository"
        tree = Path(temporary) / "tree"
        tree.mkdir()
        _git(Path(temporary), "init", "--quiet", "--bare", str(repository))
        try:
            _git(repository, "fetch", "--quiet", "--depth", "1", "--", url, revision)
        except subprocess.CalledProcessError as exc:
            raise InvalidConfig(
                {"address": f"git could not fetch {revision} from {url}: {exc.stderr.strip()}"}
            ) from exc
        commit = _git(repository, "rev-parse", "FETCH_HEAD^{commit}")
        _git(repository, f"--work-tree={tree}", "checkout", "--quiet", "FETCH_HEAD", "--", ".")
        yield commit, tree


def _git(directory: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=directory,
        capture_output=True,
        text=True,
        check=True,
        # A repository that asks for credentials fails, rather than waiting for a terminal.
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    return completed.stdout.strip()


def found_skills(tree: Path, path: PurePosixPath | None) -> dict[SkillName, PurePosixPath]:
    """Each skill in the checked-out *tree*, by name, with its folder's path in it: the one at
    *path*, or else one per folder under its ``skills`` folder.

    A skill holding a symlink or a binary file is refused, as is one that leads out of *tree*.
    """
    root = path if path is not None else PurePosixPath()
    if (tree / root / SKILL_FILE).is_file():
        folders = [root]
    else:
        under = tree / root / SKILLS_DIR
        folders = sorted(
            root / SKILLS_DIR / folder.name
            for folder in (under.iterdir() if under.is_dir() else ())
            if (folder / SKILL_FILE).is_file()
        )
    if not folders:
        where = "the repository's root" if path is None else str(path)
        inside = f"{SKILLS_DIR}/" if path is None else f"{path}/{SKILLS_DIR}/"
        raise InvalidConfig(
            {"address": f"No {SKILL_FILE} at {where}, and none in a folder under {inside}."}
        )
    found: dict[SkillName, PurePosixPath] = {}
    for folder in folders:
        name = _checked(tree, folder)
        if name in found:
            raise InvalidConfig(
                {"address": f'{found[name]} and {folder} both hold a skill named "{name}".'}
            )
        found[name] = folder
    return found


def _checked(tree: Path, folder: PurePosixPath) -> SkillName:
    """The name of the skill in *folder* of *tree*, once nothing in it is a symlink or binary."""
    directory = tree / folder
    if not directory.resolve().is_relative_to(tree.resolve()):
        raise InvalidConfig({"address": f"{folder} leads out of the repository."})
    for file in sorted(directory.rglob("*")):
        shown = (folder / file.relative_to(directory)).as_posix()
        if file.is_symlink():
            raise InvalidConfig({"address": f"{shown} is a symlink. A skill holds only files."})
        if file.is_file() and not _text(file.read_bytes()):
            raise InvalidConfig({"address": f"{shown} is a binary file. A skill holds only text."})
    try:
        name = load_skill_file(directory / SKILL_FILE).name
        validate_name(name)
    except ValueError as exc:
        raise InvalidConfig({"address": f"{folder / SKILL_FILE}: {exc}"}) from exc
    return name


def _text(content: bytes) -> bool:
    if b"\0" in content:
        return False
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True
