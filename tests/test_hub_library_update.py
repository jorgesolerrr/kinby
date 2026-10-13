"""The user moves a library skill to a newer version after reading the diff: a git skill to the
commit its ref names now, a shipped skill to the version a kinby upgrade ships."""

import asyncio
from pathlib import Path

from kinby.cli.client import ContractClient
from kinby.contracts import (
    LIBRARY_SKILL_CHECK,
    LIBRARY_SKILL_MOVE,
    ErrorCode,
    ErrorEnvelope,
    FileHash,
    GitCommit,
    LibraryListResult,
    LibrarySkillCheckCommand,
    LibrarySkillCheckResult,
    LibrarySkillMoveCommand,
    NewerVersion,
    PickState,
)
from kinby.hub import Hub
from tests.test_hub import FakeImages, FakeRuntime, hub_at, hub_client
from tests.test_hub_library_git import add, commit, head, read, refusal, repository, skill
from tests.test_hub_library_write import edit, entry, write
from tests.test_hub_picks import _lock, _pick, _picks, _running


def check(client: ContractClient, name: str) -> LibrarySkillCheckResult | ErrorEnvelope:
    result = asyncio.run(client.call(LIBRARY_SKILL_CHECK, LibrarySkillCheckCommand(name=name)))
    assert isinstance(result, LibrarySkillCheckResult | ErrorEnvelope), result
    return result


def newer(client: ContractClient, name: str) -> NewerVersion:
    result = check(client, name)
    assert isinstance(result, LibrarySkillCheckResult), result
    assert result.newer is not None, result
    return result.newer


def move(
    client: ContractClient,
    name: str,
    shown: NewerVersion,
    *,
    hash: FileHash | None = None,
    overwrite: bool = False,
) -> LibraryListResult | ErrorEnvelope:
    result = asyncio.run(
        client.call(
            LIBRARY_SKILL_MOVE,
            LibrarySkillMoveCommand(
                name=name,
                hash=hash or shown.hash,
                commit=shown.commit,
                overwrite=overwrite,
            ),
        )
    )
    assert isinstance(result, LibraryListResult | ErrorEnvelope), result
    return result


def git_hub(tmp_path: Path) -> tuple[Hub, ContractClient, Path]:
    """A hub whose library holds the skill grill, added from a repository built in the test."""
    hub = hub_at(tmp_path / "hub")
    client = hub_client(hub)
    source = repository(tmp_path / "skills", {"skills/grill/SKILL.md": skill("grill")})
    added = add(client, str(source), head(source), "grill")
    assert not isinstance(added, ErrorEnvelope), added
    return hub, client, source


def landed_commit(hub: Hub, name: str) -> GitCommit:
    found = entry(hub, name)
    assert found is not None
    assert found.git is not None
    return found.git.commit


def test_checking_a_git_skill_whose_ref_moved_shows_the_new_commit_and_a_diff(tmp_path):
    hub, client, source = git_hub(tmp_path)
    before = read(client, "grill")
    commit(source, {"skills/grill/SKILL.md": skill("grill", "Ask why five times.")})

    shown = newer(client, "grill")

    assert shown.commit == head(source)
    assert shown.hash == before.hash
    assert "-Do it well.\n+Ask why five times.\n" in shown.diff
    assert shown.diff.startswith("--- a/SKILL.md\n+++ b/SKILL.md\n")
    assert read(client, "grill") == before
    assert landed_commit(hub, "grill") != head(source)


def test_checking_a_git_skill_whose_ref_did_not_move_says_it_is_current(tmp_path):
    _, client, _ = git_hub(tmp_path)

    assert check(client, "grill") == LibrarySkillCheckResult(newer=None)


def test_moving_a_git_skill_lands_the_checked_commit_and_records_it(tmp_path):
    hub, client, source = git_hub(tmp_path)
    commit(source, {"skills/grill/SKILL.md": skill("grill", "Ask why five times.")})
    shown = newer(client, "grill")
    commit(source, {"skills/grill/SKILL.md": skill("grill", "Ask why seven times.")})

    moved = move(client, "grill", shown)

    assert isinstance(moved, LibraryListResult), moved
    assert read(client, "grill").content == skill("grill", "Ask why five times.")
    assert landed_commit(hub, "grill") == shown.commit
    found = entry(hub, "grill")
    assert found is not None
    assert not found.edited
    assert newer(client, "grill").commit == head(source)


def test_moving_a_skill_changes_no_instance(tmp_path):
    _, client, directory = _running(tmp_path)
    source = repository(tmp_path / "skills", {"skills/grill/SKILL.md": skill("grill")})
    assert not isinstance(add(client, str(source), head(source), "grill"), ErrorEnvelope)
    _pick(client, directory, "grill")
    picked = (directory / "skills" / "grill" / "SKILL.md").read_text()
    lock = _lock(directory)
    commit(source, {"skills/grill/SKILL.md": skill("grill", "Ask why five times.")})

    move(client, "grill", newer(client, "grill"))

    assert (directory / "skills" / "grill" / "SKILL.md").read_text() == picked
    assert _lock(directory) == lock
    assert [(pick.name, pick.state) for pick in _picks(client, directory).picks] == [
        ("grill", PickState.BEHIND)
    ]


def test_moving_a_skill_the_user_edited_asks_for_an_overwrite(tmp_path):
    hub, client, source = git_hub(tmp_path)
    edit(client, "grill", skill("grill", "My way."))
    commit(source, {"skills/grill/SKILL.md": skill("grill", "Ask why five times.")})
    shown = newer(client, "grill")
    assert "-My way.\n+Ask why five times.\n" in shown.diff

    refused = move(client, "grill", shown)

    assert "edited" in refusal(refused)
    assert read(client, "grill").content == skill("grill", "My way.")

    assert isinstance(move(client, "grill", shown, overwrite=True), LibraryListResult)
    assert read(client, "grill").content == skill("grill", "Ask why five times.")
    found = entry(hub, "grill")
    assert found is not None
    assert not found.edited


def test_moving_a_skill_changed_since_the_check_is_stale(tmp_path):
    _, client, source = git_hub(tmp_path)
    commit(source, {"skills/grill/SKILL.md": skill("grill", "Ask why five times.")})
    shown = newer(client, "grill")
    write(client, "grill", skill("grill", "My way."), shown.hash)

    stale = move(client, "grill", shown, overwrite=True)

    assert isinstance(stale, ErrorEnvelope), stale
    assert stale.code is ErrorCode.STALE
    assert read(client, "grill").content == skill("grill", "My way.")


def test_a_written_skill_has_no_newer_version(tmp_path):
    hub = hub_at(tmp_path / "hub")
    client = hub_client(hub)
    written = write(client, "grill", skill("grill"), None)
    assert not isinstance(written, ErrorEnvelope), written

    assert check(client, "grill") == LibrarySkillCheckResult(newer=None)
    shown = NewerVersion(commit=None, hash=written.hash, diff="")
    assert "written" in refusal(move(client, "grill", shown))


def shipped_hub(directory: Path, shipped: Path) -> Hub:
    return Hub(directory, runtime=FakeRuntime(), images=FakeImages(), shipped_skills=shipped)


def ship(shipped: Path, body: str) -> None:
    """Ship the skill grill with *body*, as a kinby version does."""
    folder = shipped / "grill"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(skill("grill", body), encoding="utf-8")


def upgraded(hub: Hub, shipped: Path, body: str) -> Hub:
    """Reopen *hub* on a kinby that ships grill with *body*."""
    hub.close()
    ship(shipped, body)
    return shipped_hub(hub.directory, shipped)


def test_after_an_upgrade_an_untouched_shipped_skill_offers_its_newer_version(tmp_path):
    shipped = tmp_path / "shipped"
    ship(shipped, "Do it well.")
    hub = shipped_hub(tmp_path / "hub", shipped)
    found = entry(hub, "grill")
    assert found is not None
    assert not found.newer
    assert check(hub_client(hub), "grill") == LibrarySkillCheckResult(newer=None)

    hub = upgraded(hub, shipped, "Ask why five times.")
    client = hub_client(hub)

    found = entry(hub, "grill")
    assert found is not None
    assert (found.newer, found.edited) == (True, False)
    assert read(client, "grill").content == skill("grill")
    shown = newer(client, "grill")
    assert shown.commit is None
    assert "-Do it well.\n+Ask why five times.\n" in shown.diff

    assert isinstance(move(client, "grill", shown), LibraryListResult)
    assert read(client, "grill").content == skill("grill", "Ask why five times.")
    found = entry(hub, "grill")
    assert found is not None
    assert (found.newer, found.edited) == (False, False)
    assert check(client, "grill") == LibrarySkillCheckResult(newer=None)


def test_a_shipped_skill_the_user_edited_is_kept_through_an_upgrade_and_marked(tmp_path):
    shipped = tmp_path / "shipped"
    ship(shipped, "Do it well.")
    hub = shipped_hub(tmp_path / "hub", shipped)
    edit(hub_client(hub), "grill", skill("grill", "My way."))

    hub = upgraded(hub, shipped, "Ask why five times.")
    client = hub_client(hub)

    assert read(client, "grill").content == skill("grill", "My way.")
    found = entry(hub, "grill")
    assert found is not None
    assert (found.newer, found.edited) == (True, True)
    shown = newer(client, "grill")
    assert "-My way.\n+Ask why five times.\n" in shown.diff
    assert "edited" in refusal(move(client, "grill", shown))
    assert read(client, "grill").content == skill("grill", "My way.")
