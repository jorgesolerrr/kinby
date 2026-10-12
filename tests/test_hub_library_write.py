"""The user writes a skill in the library on the hub, edits any skill entry, and deletes one."""

import asyncio

import pytest

from kinby.cli.client import ContractClient
from kinby.contracts import (
    LIBRARY_SKILL_DELETE,
    LIBRARY_SKILL_WRITE,
    ErrorCode,
    ErrorEnvelope,
    FileHash,
    LibraryEntry,
    LibraryKind,
    LibraryListResult,
    LibrarySkillDeleteCommand,
    LibrarySkillWriteCommand,
    LibrarySource,
    PickState,
    SkillResult,
)
from kinby.hub import Hub
from tests.test_hub import hub_at, hub_client
from tests.test_hub_library import library, reopened
from tests.test_hub_library_git import TWO_SKILLS, add, previewed, read, refusal, repository, skill
from tests.test_hub_picks import _lock, _pick, _picks, _running


def write(
    client: ContractClient, name: str, content: str, hash: FileHash | None
) -> SkillResult | ErrorEnvelope:
    result = asyncio.run(
        client.call(
            LIBRARY_SKILL_WRITE, LibrarySkillWriteCommand(name=name, content=content, hash=hash)
        )
    )
    assert isinstance(result, SkillResult | ErrorEnvelope), result
    return result


def edit(client: ContractClient, name: str, content: str) -> SkillResult:
    result = write(client, name, content, read(client, name).hash)
    assert isinstance(result, SkillResult), result
    return result


def delete(client: ContractClient, name: str, hash: FileHash) -> LibraryListResult | ErrorEnvelope:
    result = asyncio.run(
        client.call(LIBRARY_SKILL_DELETE, LibrarySkillDeleteCommand(name=name, hash=hash))
    )
    assert isinstance(result, LibraryListResult | ErrorEnvelope), result
    return result


def entry(hub: Hub, name: str) -> LibraryEntry | None:
    return next(
        (each for each in library(hub) if each.kind is LibraryKind.SKILL and each.name == name),
        None,
    )


def test_a_skill_written_on_the_hub_is_marked_written_and_can_be_picked(tmp_path):
    hub, client, directory = _running(tmp_path)

    written = write(client, "grill", skill("grill"), None)

    assert isinstance(written, SkillResult), written
    assert written.content == skill("grill")
    assert entry(hub, "grill") == LibraryEntry(
        kind=LibraryKind.SKILL,
        name="grill",
        description="The grill skill.",
        source=LibrarySource.WRITTEN,
        edited=False,
    )
    _pick(client, directory, "grill")
    assert (directory / "skills" / "grill" / "SKILL.md").read_text() == skill("grill")
    assert _lock(directory)["grill"]["sourceType"] == "written"


@pytest.mark.parametrize(
    "content",
    [
        "No frontmatter.\n",
        "---\nname: grill\n---\nNo description.\n",
        "---\ndescription: No name.\n---\nBody.\n",
        skill("other"),
    ],
    ids=["no-frontmatter", "no-description", "no-name", "another-name"],
)
def test_a_new_skill_without_its_name_and_a_description_is_refused(tmp_path, content):
    hub = hub_at(tmp_path / "hub")

    answer = write(hub_client(hub), "grill", content, None)

    assert refusal(answer)
    assert entry(hub, "grill") is None
    assert [
        path.name for path in (hub.directory / "skills").iterdir() if "grill" in path.name
    ] == []


def test_a_name_the_library_already_holds_is_refused_for_a_new_skill(tmp_path):
    hub = hub_at(tmp_path / "hub")
    client = hub_client(hub)
    before = read(client, "tdd")

    answer = write(client, "tdd", skill("tdd", "Mine."), None)

    assert refusal(answer) == 'The library already holds a skill "tdd".'
    assert read(client, "tdd") == before


def test_editing_an_entry_changes_what_a_later_pick_copies_and_no_instance(tmp_path):
    _, client, directory = _running(tmp_path)
    _pick(client, directory, "tdd")
    picked = (directory / "skills" / "tdd" / "SKILL.md").read_text()

    edit(client, "tdd", skill("tdd", "Edited on the hub."))
    edit(client, "unslop", skill("unslop", "Edited on the hub."))
    _pick(client, directory, "tdd", "unslop")

    assert (directory / "skills" / "tdd" / "SKILL.md").read_text() == picked
    assert (directory / "skills" / "unslop" / "SKILL.md").read_text() == skill(
        "unslop", "Edited on the hub."
    )
    assert {pick.name: pick.state for pick in _picks(client, directory).picks} == {
        "tdd": PickState.BEHIND,
        "unslop": PickState.UP_TO_DATE,
    }


def test_an_edited_shipped_or_git_entry_keeps_where_it_came_from_and_reports_the_edit(tmp_path):
    hub = hub_at(tmp_path / "hub")
    client = hub_client(hub)
    source = repository(tmp_path / "skills", TWO_SKILLS)
    shown = previewed(client, str(source))
    add(client, str(source), shown.commit, "grill", "caveman")
    write(client, "mine", skill("mine"), None)

    for name in ("tdd", "grill", "mine"):
        edit(client, name, skill(name, "Edited on the hub."))

    edited = {each.name: (each.source, each.edited) for each in library(hub)}
    assert {name: edited[name] for name in ("tdd", "pr", "grill", "caveman", "mine")} == {
        "tdd": (LibrarySource.SHIPPED, True),
        "pr": (LibrarySource.SHIPPED, False),
        "grill": (LibrarySource.GIT, True),
        "caveman": (LibrarySource.GIT, False),
        "mine": (LibrarySource.WRITTEN, False),
    }
    grill = entry(hub, "grill")
    assert grill is not None
    assert grill.git is not None
    assert grill.git.commit == shown.commit


def test_an_edit_over_a_skill_that_changed_since_it_was_read_is_stale(tmp_path):
    hub = hub_at(tmp_path / "hub")
    client = hub_client(hub)
    read_before = read(client, "tdd").hash
    edit(client, "tdd", skill("tdd", "First edit."))

    answer = write(client, "tdd", skill("tdd", "Second edit."), read_before)

    assert isinstance(answer, ErrorEnvelope), answer
    assert answer.code is ErrorCode.STALE
    assert read(client, "tdd").content == skill("tdd", "First edit.")


def test_deleting_a_skill_entry_leaves_it_in_every_instance_that_picked_it(tmp_path):
    hub, client, directory = _running(tmp_path)
    _pick(client, directory, "tdd")
    picked = (directory / "skills" / "tdd" / "SKILL.md").read_text()

    listed = delete(client, "tdd", read(client, "tdd").hash)

    assert isinstance(listed, LibraryListResult), listed
    assert "tdd" not in [each.name for each in listed.entries]
    assert (directory / "skills" / "tdd" / "SKILL.md").read_text() == picked
    assert "tdd" in _lock(directory)
    assert [pick.name for pick in _picks(client, directory).picks] == ["tdd"]
    assert entry(reopened(hub), "tdd") is None


def test_a_skill_written_where_a_deleted_git_entry_was_is_written(tmp_path):
    hub = hub_at(tmp_path / "hub")
    client = hub_client(hub)
    source = repository(tmp_path / "skills", TWO_SKILLS)
    add(client, str(source), previewed(client, str(source)).commit, "grill")

    delete(client, "grill", read(client, "grill").hash)
    write(client, "grill", skill("grill", "My own."), None)

    assert entry(hub, "grill") == LibraryEntry(
        kind=LibraryKind.SKILL,
        name="grill",
        description="The grill skill.",
        source=LibrarySource.WRITTEN,
        edited=False,
    )


def test_deleting_a_skill_that_changed_since_it_was_read_or_is_absent_is_refused(tmp_path):
    hub = hub_at(tmp_path / "hub")
    client = hub_client(hub)
    read_before = read(client, "tdd").hash
    edit(client, "tdd", skill("tdd", "Edited."))

    stale = delete(client, "tdd", read_before)
    absent = delete(client, "absent", read_before)

    assert isinstance(stale, ErrorEnvelope), stale
    assert stale.code is ErrorCode.STALE
    assert entry(hub, "tdd") is not None
    assert isinstance(absent, ErrorEnvelope), absent
    assert absent.code is ErrorCode.NOT_FOUND
