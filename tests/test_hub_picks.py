"""An instance picks library skills: a copy in its skills folder, and an entry in its lock."""

import asyncio
import json
from pathlib import Path
from uuid import UUID

from kinby.cli.client import ContractClient
from kinby.contracts import (
    INSTANCE_PICKS_GET,
    INSTANCE_PICKS_SET,
    ConfigActor,
    ErrorCode,
    ErrorEnvelope,
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


def _running(tmp_path: Path) -> tuple[Hub, ContractClient, Path]:
    """A hub with one running instance, and the instance's directory."""
    hub = hub_at(tmp_path / "hub")
    client = hub_client(hub)
    instance_id = asyncio.run(started_instance(client, hub)).instance_id
    return hub, client, hub.instances_directory / str(instance_id)


def _instance_id(directory: Path) -> UUID:
    return UUID(directory.name)


def _pick(client: ContractClient, directory: Path, *skills: str) -> object:
    return asyncio.run(
        client.call(
            INSTANCE_PICKS_SET,
            InstancePicksSetCommand(instance_id=_instance_id(directory), skills=list(skills)),
        )
    )


def _picks(client: ContractClient, directory: Path) -> InstancePicksResult:
    result = asyncio.run(
        client.call(
            INSTANCE_PICKS_GET, InstancePicksGetCommand(instance_id=_instance_id(directory))
        )
    )
    assert isinstance(result, InstancePicksResult), result
    return result


def _lock(directory: Path) -> dict[str, dict[str, str]]:
    return json.loads((directory / "skills-lock.json").read_text(encoding="utf-8"))["skills"]


def _catalogue(directory: Path) -> list[str]:
    skills, _ = load_skills(inspect_instance(directory))
    return [skill.name for skill in skills]


def _own_skill(directory: Path) -> Path:
    own = directory / "skills" / "mine" / "SKILL.md"
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text(OWN_SKILL, encoding="utf-8")
    return own


def test_a_picked_library_skill_lands_in_the_instance_tier_of_a_running_instance(tmp_path):
    _, client, directory = _running(tmp_path)

    picked = _pick(client, directory, "tdd", "unslop")

    shipped = SHIPPED_SKILLS / "tdd"
    assert {
        path.relative_to(shipped).as_posix(): path.read_bytes()
        for path in shipped.rglob("*")
        if path.is_file()
    } == {
        path.relative_to(directory / "skills" / "tdd").as_posix(): path.read_bytes()
        for path in (directory / "skills" / "tdd").rglob("*")
        if path.is_file()
    }
    assert {"tdd", "unslop"} <= set(_catalogue(directory))
    expected = InstancePicksResult(
        picks=[
            SkillPick(name="tdd", source=LibrarySource.SHIPPED),
            SkillPick(name="unslop", source=LibrarySource.SHIPPED),
        ]
    )
    assert picked == expected
    assert _picks(client, directory) == expected


def test_the_lock_records_each_pick_with_its_source_and_content_hash(tmp_path):
    hub, client, directory = _running(tmp_path)
    written = hub.directory / "skills" / "house-style"
    written.mkdir()
    (written / "SKILL.md").write_text(
        "---\nname: house-style\ndescription: Ours.\n---\nOurs.\n", encoding="utf-8"
    )

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

    assert dropped == InstancePicksResult(
        picks=[SkillPick(name="unslop", source=LibrarySource.SHIPPED)]
    )
    assert not (directory / "skills" / "tdd").exists()
    assert set(_lock(directory)) == {"unslop"}
    assert own.read_text(encoding="utf-8") == OWN_SKILL
    assert "mine" in _catalogue(directory)


def test_picking_a_name_the_instance_holds_as_its_own_skill_fails_and_says_so(tmp_path):
    _, client, directory = _running(tmp_path)
    own = directory / "skills" / "tdd" / "SKILL.md"
    own.parent.mkdir(parents=True)
    own.write_text("---\nname: tdd\ndescription: Mine.\n---\nMine.\n", encoding="utf-8")

    refused = _pick(client, directory, "unslop", "tdd")

    assert isinstance(refused, ErrorEnvelope)
    assert refused.code is ErrorCode.INVALID_ARGUMENT
    assert "own skill" in refused.fields["skills"]
    assert "tdd" in refused.fields["skills"]
    assert own.read_text(encoding="utf-8") == "---\nname: tdd\ndescription: Mine.\n---\nMine.\n"
    assert not (directory / "skills" / "unslop").exists()
    assert not (directory / "skills-lock.json").exists()


def test_picking_a_skill_the_library_does_not_hold_fails_before_anything_changes(tmp_path):
    _, client, directory = _running(tmp_path)
    _pick(client, directory, "tdd")

    refused = _pick(client, directory, "unslop", "absent", "../factories")

    assert isinstance(refused, ErrorEnvelope)
    assert refused.code is ErrorCode.INVALID_ARGUMENT
    assert 'The library has no skill "absent".' in refused.fields["skills"]
    assert not (directory / "skills" / "unslop").exists()
    assert set(_lock(directory)) == {"tdd"}
    assert _picks(client, directory).picks == [SkillPick(name="tdd", source=LibrarySource.SHIPPED)]


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
