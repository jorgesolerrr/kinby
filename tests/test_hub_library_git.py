"""The user adds a skill to the library from a git repository, after a preview, at a commit."""

import asyncio
import subprocess
from pathlib import Path

import pytest

from kinby.cli.client import ContractClient
from kinby.contracts import (
    LIBRARY_GIT_ADD,
    LIBRARY_GIT_PREVIEW,
    LIBRARY_SKILL_READ,
    ErrorCode,
    ErrorEnvelope,
    GitSource,
    LibraryEntry,
    LibraryGitAddCommand,
    LibraryGitAddResult,
    LibraryGitPreviewCommand,
    LibraryGitPreviewResult,
    LibraryKind,
    LibrarySkillReadCommand,
    LibrarySource,
    PreviewedSkill,
    SkillResult,
)
from kinby.hub import Hub
from tests.test_hub import hub_at, hub_client
from tests.test_hub_library import library
from tests.test_hub_picks import _lock, _pick, _running


def skill(name: str, body: str = "Do it well.") -> str:
    return f"---\nname: {name}\ndescription: The {name} skill.\n---\n{body}\n"


def git(directory: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments), cwd=directory, check=True, capture_output=True, text=True
    ).stdout.strip()


def repository(directory: Path, files: dict[str, str | bytes]) -> Path:
    """A git repository in *directory* whose main holds *files*, in one commit."""
    directory.mkdir(parents=True)
    git(directory, "init", "--quiet", "-b", "main")
    git(directory, "config", "user.name", "Skill author")
    git(directory, "config", "user.email", "author@example.com")
    return commit(directory, files)


def commit(directory: Path, files: dict[str, str | bytes]) -> Path:
    for name, content in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    git(directory, "add", "--all")
    git(directory, "commit", "--quiet", "-m", "Skills")
    return directory


def head(directory: Path) -> str:
    return git(directory, "rev-parse", "HEAD")


@pytest.fixture
def github(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A folder git reads in place of github.com, so an ``owner/repo`` address needs no network."""
    folder = tmp_path / "github"
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{folder.as_posix()}/.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "https://github.com/")
    return folder


def preview(client: ContractClient, address: str) -> LibraryGitPreviewResult | ErrorEnvelope:
    result = asyncio.run(
        client.call(LIBRARY_GIT_PREVIEW, LibraryGitPreviewCommand(address=address))
    )
    assert isinstance(result, LibraryGitPreviewResult | ErrorEnvelope), result
    return result


def previewed(client: ContractClient, address: str) -> LibraryGitPreviewResult:
    result = preview(client, address)
    assert isinstance(result, LibraryGitPreviewResult), result
    return result


def add(
    client: ContractClient, address: str, commit: str, *skills: str
) -> LibraryGitAddResult | ErrorEnvelope:
    result = asyncio.run(
        client.call(
            LIBRARY_GIT_ADD,
            LibraryGitAddCommand(address=address, commit=commit, skills=list(skills)),
        )
    )
    assert isinstance(result, LibraryGitAddResult | ErrorEnvelope), result
    return result


def refusal(answer: object) -> str:
    assert isinstance(answer, ErrorEnvelope), answer
    assert answer.code is ErrorCode.INVALID_ARGUMENT
    assert answer.fields is not None
    return " ".join(answer.fields.values())


def read(client: ContractClient, name: str) -> SkillResult:
    result = asyncio.run(client.call(LIBRARY_SKILL_READ, LibrarySkillReadCommand(name=name)))
    assert isinstance(result, SkillResult), result
    return result


def skill_names(hub: Hub) -> list[str]:
    return [entry.name for entry in library(hub) if entry.kind is LibraryKind.SKILL]


TWO_SKILLS: dict[str, str | bytes] = {
    "README.md": "Two skills.\n",
    "skills/grill/SKILL.md": skill("grill"),
    "skills/grill/references/questions.md": "Why?\n",
    "skills/caveman/SKILL.md": skill("caveman"),
}


def test_a_preview_lists_every_skill_under_skills_at_the_resolved_commit_and_adds_none(tmp_path):
    hub = hub_at(tmp_path / "hub")
    source = repository(tmp_path / "skills", TWO_SKILLS)
    before = library(hub)

    shown = previewed(hub_client(hub), str(source))

    assert shown == LibraryGitPreviewResult(
        url=str(source),
        ref=None,
        commit=head(source),
        skills=[
            PreviewedSkill(
                name="caveman", path="skills/caveman", content=skill("caveman"), files=[]
            ),
            PreviewedSkill(
                name="grill",
                path="skills/grill",
                content=skill("grill"),
                files=["references/questions.md"],
            ),
        ],
    )
    assert library(hub) == before


def test_a_repository_whose_root_holds_a_skill_previews_that_one_skill(tmp_path):
    hub = hub_at(tmp_path / "hub")
    source = repository(tmp_path / "grill", {"SKILL.md": skill("grill"), "notes.md": "More.\n"})

    shown = previewed(hub_client(hub), str(source))

    assert [(each.name, each.path, each.files) for each in shown.skills] == [
        ("grill", ".", ["notes.md"])
    ]


def test_an_address_with_a_path_previews_the_one_skill_at_that_path(tmp_path, github):
    hub = hub_at(tmp_path / "hub")
    source = repository(github / "owner" / "repo.git", TWO_SKILLS)
    git(source, "tag", "v1")

    shown = previewed(hub_client(hub), "owner/repo/skills/grill#v1")

    assert (shown.url, shown.ref, shown.commit) == (
        "https://github.com/owner/repo.git",
        "v1",
        head(source),
    )
    assert [each.name for each in shown.skills] == ["grill"]


def test_a_github_tree_url_names_the_ref_and_the_path(tmp_path, github):
    hub = hub_at(tmp_path / "hub")
    repository(github / "owner" / "repo.git", TWO_SKILLS)

    shown = previewed(hub_client(hub), "https://github.com/owner/repo/tree/main/skills/caveman")

    assert (shown.url, shown.ref) == ("https://github.com/owner/repo.git", "main")
    assert [each.name for each in shown.skills] == ["caveman"]


def test_adding_a_previewed_skill_keeps_its_files_and_records_where_it_came_from(tmp_path, github):
    hub = hub_at(tmp_path / "hub")
    client = hub_client(hub)
    source = repository(github / "owner" / "repo.git", TWO_SKILLS)
    shown = previewed(client, "owner/repo#main")

    added = add(client, "owner/repo#main", shown.commit, "grill")

    entry = LibraryEntry(
        kind=LibraryKind.SKILL,
        name="grill",
        description="The grill skill.",
        source=LibrarySource.GIT,
        git=GitSource(
            url="https://github.com/owner/repo.git",
            path="skills/grill",
            ref="main",
            commit=head(source),
        ),
    )
    assert added == LibraryGitAddResult(entries=[entry])
    assert entry in library(hub)
    assert "caveman" not in skill_names(hub)
    grill = read(client, "grill")
    assert (grill.content, grill.files) == (skill("grill"), ["references/questions.md"])


def test_adding_after_the_ref_moved_lands_the_commit_the_preview_showed(tmp_path):
    hub = hub_at(tmp_path / "hub")
    client = hub_client(hub)
    source = repository(tmp_path / "skills", TWO_SKILLS)
    shown = previewed(client, str(source))
    commit(source, {"skills/grill/SKILL.md": skill("grill", "Changed after the preview.")})

    added = add(client, str(source), shown.commit, "grill")

    assert isinstance(added, LibraryGitAddResult), added
    assert added.entries[0].git is not None
    assert added.entries[0].git.commit == shown.commit != head(source)
    assert read(client, "grill").content == skill("grill")


@pytest.mark.parametrize(
    ("files", "named"),
    [
        ({"skills/grill/logo.png": b"\x89PNG\r\n\x1a\n\x00\x00"}, "skills/grill/logo.png"),
        ({"skills/grill/notes.md": "Fine.\n"}, "skills/grill/link.md"),
    ],
    ids=["binary", "symlink"],
)
def test_a_skill_folder_holding_a_binary_file_or_a_symlink_is_refused_by_name(
    tmp_path, files, named
):
    hub = hub_at(tmp_path / "hub")
    source = repository(tmp_path / "skills", {"skills/grill/SKILL.md": skill("grill"), **files})
    if named.endswith("link.md"):
        (source / named).symlink_to("notes.md")
        commit(source, {})

    answer = preview(hub_client(hub), str(source))

    assert named in refusal(answer)
    assert "grill" not in skill_names(hub)


def test_a_skill_whose_name_the_library_holds_is_refused(tmp_path):
    hub = hub_at(tmp_path / "hub")
    client = hub_client(hub)
    source = repository(tmp_path / "skills", {"skills/tdd/SKILL.md": skill("tdd", "Mine.")})
    shown = previewed(client, str(source))

    answer = add(client, str(source), shown.commit, "tdd")

    assert 'The library already holds a skill "tdd".' in refusal(answer)
    assert read(client, "tdd").content != skill("tdd", "Mine.")


def test_an_address_with_no_skill_says_where_the_hub_looked(tmp_path):
    hub = hub_at(tmp_path / "hub")
    source = repository(tmp_path / "empty", {"README.md": "Nothing here.\n"})

    answer = preview(hub_client(hub), str(source))

    assert refusal(answer) == (
        "No SKILL.md at the repository's root, and none in a folder under skills/."
    )


def test_a_pick_of_a_skill_from_git_locks_where_it_came_from(tmp_path):
    _, client, directory = _running(tmp_path)
    source = repository(tmp_path / "skills", TWO_SKILLS)
    shown = previewed(client, str(source))
    add(client, f"{source}#main", shown.commit, "grill")

    _pick(client, directory, "grill")

    locked = _lock(directory)["grill"]
    assert {key: locked[key] for key in ("source", "sourceType", "ref", "skillPath", "commit")} == {
        "source": str(source),
        "sourceType": "git",
        "ref": "main",
        "skillPath": "skills/grill",
        "commit": shown.commit,
    }
