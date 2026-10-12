"""An instance picks library skills: a copy in its skills folder, and an entry in its lock."""

import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from uuid import UUID

from kinby.cli.client import ContractClient
from kinby.contracts import (
    INSTANCE_PICKS_GET,
    INSTANCE_PICKS_SET,
    ConfigActor,
    ErrorCode,
    ErrorEnvelope,
    FileHash,
    InstancePicksGetCommand,
    InstancePicksResult,
    InstancePicksSetCommand,
    LibrarySource,
    SkillPick,
)
from kinby.hub import Hub
from kinby.instance import inspect_instance
from kinby.instance.config_changes import ConfigChangeLog
from kinby.instance.skills_lock import skill_hash
from kinby.library import SHIPPED_SKILLS
from kinby.plugins.skills import load_skills
from tests.test_hub import hub_at, hub_client, started_instance

OWN_SKILL = "---\nname: mine\ndescription: The agent's own.\n---\nMine.\n"
TDD = SkillPick(name="tdd", source=LibrarySource.SHIPPED)
UNSLOP = SkillPick(name="unslop", source=LibrarySource.SHIPPED)


def _running(tmp_path: Path) -> tuple[Hub, ContractClient, Path]:
    """A hub with one running instance, and the instance's directory."""
    hub = hub_at(tmp_path / "hub")
    client = hub_client(hub)
    instance_id = asyncio.run(started_instance(client, hub)).instance_id
    return hub, client, hub.instances_directory / str(instance_id)


def _instance_id(directory: Path) -> UUID:
    return UUID(directory.name)


def _read(client: ContractClient, directory: Path) -> object:
    command = InstancePicksGetCommand(instance_id=_instance_id(directory))
    return asyncio.run(client.call(INSTANCE_PICKS_GET, command))


def _picks(client: ContractClient, directory: Path) -> InstancePicksResult:
    result = _read(client, directory)
    assert isinstance(result, InstancePicksResult), result
    return result


def _set(
    client: ContractClient, directory: Path, skills: list[str], read: FileHash | None
) -> object:
    command = InstancePicksSetCommand(instance_id=_instance_id(directory), skills=skills, hash=read)
    return asyncio.run(client.call(INSTANCE_PICKS_SET, command))


def _pick(client: ContractClient, directory: Path, *skills: str) -> object:
    """Set the picks as a client does: with the hash of the lock it just read."""
    return _set(client, directory, list(skills), _picks(client, directory).hash)


def _picked(answer: object) -> list[SkillPick]:
    assert isinstance(answer, InstancePicksResult), answer
    return answer.picks


def _refused(answer: object, code: ErrorCode = ErrorCode.INVALID_ARGUMENT) -> str:
    assert isinstance(answer, ErrorEnvelope), answer
    assert answer.code is code
    return answer.fields.get("skills", answer.message)


def _lock(directory: Path) -> dict[str, dict[str, str]]:
    return json.loads((directory / "skills-lock.json").read_text(encoding="utf-8"))["skills"]


def _catalogue(directory: Path) -> list[str]:
    skills, _ = load_skills(inspect_instance(directory))
    return [skill.name for skill in skills]


def _files(folder: Path) -> dict[str, bytes]:
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in folder.rglob("*")
        if path.is_file()
    }


def _own_skill(directory: Path) -> Path:
    own = directory / "skills" / "mine" / "SKILL.md"
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text(OWN_SKILL, encoding="utf-8")
    return own


def _written_skill(hub: Hub, name: str) -> Path:
    written = hub.directory / "skills" / name
    written.mkdir()
    (written / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Ours.\n---\nOurs.\n", encoding="utf-8"
    )
    return written


def test_a_picked_library_skill_lands_in_the_instance_tier_of_a_running_instance(tmp_path):
    _, client, directory = _running(tmp_path)

    picked = _pick(client, directory, "tdd", "unslop")

    assert _files(SHIPPED_SKILLS / "tdd") == _files(directory / "skills" / "tdd")
    assert {"tdd", "unslop"} <= set(_catalogue(directory))
    assert _picked(picked) == [TDD, UNSLOP]
    assert _picks(client, directory) == picked


def test_the_lock_records_each_pick_with_its_source_and_content_hash(tmp_path):
    hub, client, directory = _running(tmp_path)
    _written_skill(hub, "house-style")

    _pick(client, directory, "tdd", "house-style")

    assert _lock(directory) == {
        "house-style": {
            "source": "hub",
            "sourceType": "written",
            "computedHash": skill_hash(directory / "skills" / "house-style"),
        },
        "tdd": {
            "source": "kinby",
            "sourceType": "shipped",
            "computedHash": skill_hash(directory / "skills" / "tdd"),
        },
    }


def test_dropping_a_pick_removes_its_folder_and_lock_entry_and_leaves_the_agents_own(tmp_path):
    _, client, directory = _running(tmp_path)
    own = _own_skill(directory)
    _pick(client, directory, "tdd", "unslop")

    dropped = _pick(client, directory, "unslop")

    assert _picked(dropped) == [UNSLOP]
    assert not (directory / "skills" / "tdd").exists()
    assert set(_lock(directory)) == {"unslop"}
    assert own.read_text(encoding="utf-8") == OWN_SKILL
    assert "mine" in _catalogue(directory)


def test_picking_a_name_the_instance_holds_as_its_own_skill_fails_and_says_so(tmp_path):
    _, client, directory = _running(tmp_path)
    own = directory / "skills" / "tdd" / "SKILL.md"
    own.parent.mkdir(parents=True)
    own.write_text("---\nname: tdd\ndescription: Mine.\n---\nMine.\n", encoding="utf-8")

    refusal = _refused(_pick(client, directory, "unslop", "tdd"))

    assert 'The instance has its own skill "tdd".' in refusal
    assert own.read_text(encoding="utf-8") == "---\nname: tdd\ndescription: Mine.\n---\nMine.\n"
    assert not (directory / "skills" / "unslop").exists()
    assert not (directory / "skills-lock.json").exists()


def test_picking_a_skill_the_library_does_not_hold_fails_before_anything_changes(tmp_path):
    _, client, directory = _running(tmp_path)
    _pick(client, directory, "tdd")

    refusal = _refused(_pick(client, directory, "unslop", "absent", "../factories"))

    assert 'The library has no skill "absent".' in refusal
    assert not (directory / "skills" / "unslop").exists()
    assert set(_lock(directory)) == {"tdd"}
    assert _picks(client, directory).picks == [TDD]


def test_each_change_to_the_picks_is_recorded_as_a_config_change(tmp_path):
    _, client, directory = _running(tmp_path)
    state = inspect_instance(directory).manifest.state_dir

    _pick(client, directory, "tdd")
    _pick(client, directory)

    dropped_lock, dropped, picked_lock, picked = ConfigChangeLog(state).history(None, 10)
    assert [
        (change.file, change.actor) for change in (dropped_lock, dropped, picked_lock, picked)
    ] == [
        ("skills-lock.json", ConfigActor.APP),
        ("skills/tdd", ConfigActor.APP),
        ("skills-lock.json", ConfigActor.APP),
        ("skills/tdd", ConfigActor.APP),
    ]
    assert "+++ b/skills/tdd/SKILL.md" in picked.diff
    assert picked.hash is not None
    assert dropped.hash is None


def test_a_pick_set_that_changes_nothing_records_nothing(tmp_path):
    _, client, directory = _running(tmp_path)
    state = inspect_instance(directory).manifest.state_dir
    _pick(client, directory, "tdd")

    _pick(client, directory, "tdd")

    assert len(ConfigChangeLog(state).history(None, 10)) == 2


def test_a_set_from_a_stale_read_is_refused_and_drops_nothing(tmp_path):
    _, client, directory = _running(tmp_path)
    before = _picks(client, directory).hash
    _pick(client, directory, "tdd")

    _refused(_set(client, directory, ["unslop"], before), ErrorCode.STALE)

    assert (directory / "skills" / "tdd").is_dir()
    assert not (directory / "skills" / "unslop").exists()
    assert set(_lock(directory)) == {"tdd"}


def test_a_pick_deleted_from_the_skills_folder_is_no_pick_and_can_be_picked_again(tmp_path):
    _, client, directory = _running(tmp_path)
    _pick(client, directory, "tdd", "unslop")
    shutil.rmtree(directory / "skills" / "tdd")

    assert _picks(client, directory).picks == [UNSLOP]
    picked = _pick(client, directory, "tdd", "unslop")

    assert _picked(picked) == [TDD, UNSLOP]
    assert _files(SHIPPED_SKILLS / "tdd") == _files(directory / "skills" / "tdd")


def test_a_library_skill_holding_a_link_is_refused_before_anything_changes(tmp_path):
    hub, client, directory = _running(tmp_path)
    secret = tmp_path / "secret.env"
    secret.write_text("TOKEN=sk-hub\n", encoding="utf-8")
    (_written_skill(hub, "linked") / "notes.md").symlink_to(secret)

    refusal = _refused(_pick(client, directory, "tdd", "linked"))

    assert '"linked"' in refusal
    assert "link" in refusal
    assert not (directory / "skills" / "tdd").exists()
    assert not (directory / "skills" / "linked").exists()


def test_a_linked_skills_folder_or_lock_is_refused_and_nothing_outside_is_touched(tmp_path):
    hub, client, directory = _running(tmp_path)
    _pick(client, directory, "tdd")
    read = _picks(client, directory).hash
    library = _files(hub.directory / "skills" / "tdd")
    shutil.rmtree(directory / "skills")
    (directory / "skills").symlink_to(hub.directory / "skills")

    _refused(_read(client, directory))
    _refused(_set(client, directory, [], read))

    assert _files(hub.directory / "skills" / "tdd") == library
    (directory / "skills").unlink()
    (directory / "skills").mkdir()
    lock = directory / "skills-lock.json"
    lock.unlink()
    lock.symlink_to(directory / ".env")
    secrets = (directory / ".env").read_text(encoding="utf-8")

    assert "sk-test" not in _refused(_read(client, directory))
    _refused(_set(client, directory, ["tdd"], None))

    assert lock.is_symlink()
    assert (directory / ".env").read_text(encoding="utf-8") == secrets


def test_dropping_a_pick_the_agent_swapped_for_a_link_removes_only_the_link(tmp_path):
    hub, client, directory = _running(tmp_path)
    _pick(client, directory, "tdd")
    library = _files(hub.directory / "skills" / "tdd")
    shutil.rmtree(directory / "skills" / "tdd")
    (directory / "skills" / "tdd").symlink_to(hub.directory / "skills" / "tdd")

    _pick(client, directory)

    assert not (directory / "skills" / "tdd").is_symlink()
    assert _files(hub.directory / "skills" / "tdd") == library


def test_a_copy_that_fails_partway_changes_nothing_and_the_same_set_succeeds_again(
    tmp_path, monkeypatch
):
    _, client, directory = _running(tmp_path)
    copytree = shutil.copytree
    copies = []

    def failing_second(source: Path, destination: Path) -> object:
        copies.append(source)
        if len(copies) == 2:
            raise OSError("No space left on device")
        return copytree(source, destination)

    monkeypatch.setattr(shutil, "copytree", failing_second)
    _refused(_pick(client, directory, "tdd", "unslop"), ErrorCode.INTERNAL)
    monkeypatch.setattr(shutil, "copytree", copytree)

    assert not (directory / "skills" / "tdd").exists()
    assert not (directory / "skills-lock.json").exists()
    assert _picked(_pick(client, directory, "tdd", "unslop")) == [TDD, UNSLOP]


def test_the_content_hash_orders_files_outside_the_collation_after_it_by_code_point(tmp_path):
    skill = tmp_path / "skill"
    skill.mkdir()
    for name in ("日.md", "中.md", "a.md"):
        (skill / name).write_text(name, encoding="utf-8")

    expected = hashlib.sha256()
    for name in ("a.md", "中.md", "日.md"):
        expected.update(name.encode())
        expected.update(name.encode())

    assert skill_hash(skill) == expected.hexdigest()
