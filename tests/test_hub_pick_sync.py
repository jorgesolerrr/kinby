"""An instance update brings an instance's picks up to the library's, keeping a pick it edited."""

import asyncio
import shutil
from pathlib import Path

from kinby.contracts import (
    INSTANCE_UPDATE,
    InstanceUpdateCommand,
    LibrarySource,
    LifecycleOperationResult,
    OperationGetResult,
    OperationState,
    PickState,
    SkillPick,
)
from kinby.hub import Hub
from kinby.instance.skills_lock import skill_hash
from tests.test_hub import FakeControl, finished_operation, hub_at, hub_client, started_instance
from tests.test_hub_picks import _instance_id, _lock, _pick, _picks

CHANGED = "\nThe library's newer line.\n"
EDITED = "\nThe instance's own line.\n"


def _hub(tmp_path: Path) -> Hub:
    return hub_at(tmp_path / "hub", control=FakeControl())


def _started(hub: Hub, name: str = "alice") -> Path:
    instance_id = asyncio.run(started_instance(hub_client(hub), hub, name=name)).instance_id
    return hub.instances_directory / str(instance_id)


def _append(skill_file: Path, line: str) -> None:
    skill_file.write_text(skill_file.read_text(encoding="utf-8") + line, encoding="utf-8")


def _update(hub: Hub, directory: Path) -> OperationGetResult:
    client = hub_client(hub)

    async def scenario() -> OperationGetResult:
        accepted = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(instance_id=_instance_id(directory), revision="v2"),
        )
        assert isinstance(accepted, LifecycleOperationResult), accepted
        return await finished_operation(client, accepted)

    outcome = asyncio.run(scenario())
    assert outcome.state is OperationState.SUCCEEDED, outcome.detail
    return outcome


def _picks_step(outcome: OperationGetResult) -> str:
    [step] = [step for step in outcome.steps if step.name == "picks"]
    return step.detail


def _states(hub: Hub, directory: Path) -> dict[str, PickState]:
    return {pick.name: pick.state for pick in _picks(hub_client(hub), directory).picks}


def test_an_update_writes_a_changed_library_skill_into_a_pick_the_instance_left_alone(tmp_path):
    hub = _hub(tmp_path)
    directory = _started(hub)
    _pick(hub_client(hub), directory, "tdd", "unslop")
    library = hub.directory / "skills" / "tdd"
    _append(library / "SKILL.md", CHANGED)

    outcome = _update(hub, directory)

    assert _picks_step(outcome) == "Wrote tdd."
    assert (directory / "skills" / "tdd" / "SKILL.md").read_text(encoding="utf-8").endswith(CHANGED)
    assert _lock(directory)["tdd"]["computedHash"] == skill_hash(library)
    assert _states(hub, directory) == {"tdd": PickState.UP_TO_DATE, "unslop": PickState.UP_TO_DATE}


def test_an_update_keeps_a_pick_the_instance_edited_and_names_it(tmp_path):
    hub = _hub(tmp_path)
    directory = _started(hub)
    _pick(hub_client(hub), directory, "tdd")
    locked = _lock(directory)["tdd"]
    _append(directory / "skills" / "tdd" / "SKILL.md", EDITED)
    _append(hub.directory / "skills" / "tdd" / "SKILL.md", CHANGED)

    outcome = _update(hub, directory)

    assert _picks_step(outcome) == "Kept the instance's own tdd."
    assert (directory / "skills" / "tdd" / "SKILL.md").read_text(encoding="utf-8").endswith(EDITED)
    assert _lock(directory)["tdd"] == locked
    assert _states(hub, directory) == {"tdd": PickState.EDITED}


def test_a_pick_whose_library_entry_was_deleted_stays_through_an_update(tmp_path):
    hub = _hub(tmp_path)
    directory = _started(hub)
    _pick(hub_client(hub), directory, "tdd")
    held = skill_hash(directory / "skills" / "tdd")
    shutil.rmtree(hub.directory / "skills" / "tdd")

    outcome = _update(hub, directory)

    assert _picks_step(outcome) == "No pick is behind the library."
    assert skill_hash(directory / "skills" / "tdd") == held
    assert _lock(directory)["tdd"]["computedHash"] == held
    assert _picks(hub_client(hub), directory).picks == [
        SkillPick(name="tdd", source=LibrarySource.SHIPPED, state=PickState.UP_TO_DATE)
    ]


def test_a_library_change_reaches_only_the_instance_that_is_updated(tmp_path):
    hub = _hub(tmp_path)
    updated = _started(hub)
    waiting = _started(hub, name="bob")
    for directory in (updated, waiting):
        _pick(hub_client(hub), directory, "tdd")
    _append(hub.directory / "skills" / "tdd" / "SKILL.md", CHANGED)

    assert _states(hub, updated) == {"tdd": PickState.BEHIND}

    _update(hub, updated)

    assert (updated / "skills" / "tdd" / "SKILL.md").read_text(encoding="utf-8").endswith(CHANGED)
    assert (
        not (waiting / "skills" / "tdd" / "SKILL.md").read_text(encoding="utf-8").endswith(CHANGED)
    )
    assert _states(hub, updated) == {"tdd": PickState.UP_TO_DATE}
    assert _states(hub, waiting) == {"tdd": PickState.BEHIND}


def test_the_hub_reports_each_pick_up_to_date_behind_or_edited(tmp_path):
    hub = _hub(tmp_path)
    directory = _started(hub)
    _pick(hub_client(hub), directory, "ponytail", "tdd", "unslop")
    _append(hub.directory / "skills" / "tdd" / "SKILL.md", CHANGED)
    _append(directory / "skills" / "unslop" / "SKILL.md", EDITED)

    assert _states(hub, directory) == {
        "ponytail": PickState.UP_TO_DATE,
        "tdd": PickState.BEHIND,
        "unslop": PickState.EDITED,
    }


def test_an_update_keeps_a_pick_whose_skills_folder_links_out_of_the_instance(tmp_path):
    hub = _hub(tmp_path)
    directory = _started(hub)
    _pick(hub_client(hub), directory, "tdd")
    outside = tmp_path / "outside"
    (directory / "skills").rename(outside)
    (directory / "skills").symlink_to(outside, target_is_directory=True)
    held = skill_hash(outside / "tdd")
    _append(hub.directory / "skills" / "tdd" / "SKILL.md", CHANGED)

    assert _states(hub, directory) == {"tdd": PickState.EDITED}

    outcome = _update(hub, directory)

    assert _picks_step(outcome) == "Kept the instance's own tdd."
    assert skill_hash(outside / "tdd") == held


def test_an_update_that_stopped_after_writing_a_pick_finishes_its_lock_entry(tmp_path):
    hub = _hub(tmp_path)
    directory = _started(hub)
    _pick(hub_client(hub), directory, "tdd")
    library = hub.directory / "skills" / "tdd"
    _append(library / "SKILL.md", CHANGED)
    # What an update leaves when it stops after writing the folder and before writing the lock.
    shutil.rmtree(directory / "skills" / "tdd")
    shutil.copytree(library, directory / "skills" / "tdd")

    assert _states(hub, directory) == {"tdd": PickState.UP_TO_DATE}

    _update(hub, directory)

    assert _lock(directory)["tdd"]["computedHash"] == skill_hash(library)
    assert _states(hub, directory) == {"tdd": PickState.UP_TO_DATE}
