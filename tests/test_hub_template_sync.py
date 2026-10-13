"""An instance update brings the files a factory's template supplied up to the factory's."""

import asyncio
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import pytest

from kinby.cli.client import ContractClient
from kinby.contracts import (
    INSTANCE_CREATE,
    INSTANCE_STATUS,
    INSTANCE_UPDATE,
    FactoryRole,
    InstanceCreateCommand,
    InstanceStatusCommand,
    InstanceStatusResult,
    InstanceUpdateCommand,
    LifecycleOperationResult,
    OperationGetResult,
    OperationState,
    SecretSetup,
)
from kinby.hub import BuiltImage, ImageSelection
from tests.test_factory_templates import FILES, WITH_SETUP, created_roles, factory_hub
from tests.test_hub import FakeImages, FakeRuntime, finished_operation, hub_client
from tests.test_hub_notices import (
    CODER_BEHIND,
    SKILL,
    _edit,
    _no_record,
    _notices_by_instance,
)

OWN_SKILL = "---\nname: mine\ndescription: The agent's own.\n---\nMine.\n"
HOUSE_STYLE = "---\nname: house-style\ndescription: Ours.\n---\nOurs.\n"


class EditingImages(FakeImages):
    """Runs *during_build* while it builds, as an instance may edit itself while an update waits."""

    def __init__(self) -> None:
        super().__init__()
        self.during_build: Callable[[], object] = lambda: None

    async def build(self, selection: ImageSelection) -> BuiltImage:
        self.during_build()
        return await super().build(selection)


def _factory(tmp_path: Path) -> Path:
    return tmp_path / "hub" / "factories" / "tickets"


def _directory(tmp_path: Path, instance_id: UUID) -> Path:
    return tmp_path / "hub" / "instances" / str(instance_id)


async def _created_coder(client: ContractClient) -> UUID:
    return (await created_roles(client))["coder"]


async def _update(client: ContractClient, instance_id: UUID) -> OperationGetResult:
    accepted = await client.call(
        INSTANCE_UPDATE, InstanceUpdateCommand(instance_id=instance_id, revision="v2")
    )
    assert isinstance(accepted, LifecycleOperationResult)
    return await finished_operation(client, accepted)


def _template_step(outcome: OperationGetResult) -> str:
    assert outcome.state is OperationState.SUCCEEDED, outcome.detail
    [step] = [step for step in outcome.steps if step.name == "template"]
    return step.detail


def test_an_update_writes_a_file_the_factory_changed_and_clears_the_notice(tmp_path):
    async def scenario() -> None:
        client = hub_client(factory_hub(tmp_path))
        coder = await _created_coder(client)
        await _edit(client, {"instances/coder/SYSTEM.md": "You implement issues well.\n"})

        outcome = await _update(client, coder)

        assert _template_step(outcome) == "Wrote SYSTEM.md."
        assert (_directory(tmp_path, coder) / "SYSTEM.md").read_text(
            encoding="utf-8"
        ) == "You implement issues well.\n"
        assert await _notices_by_instance(client) == {"tickets-coder": [], "tickets-reviewer": []}

    asyncio.run(scenario())


def test_an_update_writes_the_template_while_the_container_is_down(tmp_path):
    async def scenario() -> None:
        client = hub_client(factory_hub(tmp_path))
        coder = await _created_coder(client)
        await _edit(client, {"instances/coder/SYSTEM.md": "You implement issues well.\n"})

        outcome = await _update(client, coder)

        names = [step.name for step in outcome.steps]
        assert names.index("remove") < names.index("template") < names.index("create")

    asyncio.run(scenario())


def test_an_update_removes_a_file_the_factory_dropped_and_leaves_the_instances_own(tmp_path):
    async def scenario() -> None:
        # A skill the library does not hold, which an update would make a pick instead.
        files = FILES | {"instances/coder/skills/house-style/SKILL.md": HOUSE_STYLE}
        client = hub_client(factory_hub(tmp_path, files))
        coder = await _created_coder(client)
        own = _directory(tmp_path, coder) / "skills" / "mine" / "SKILL.md"
        own.parent.mkdir(parents=True)
        own.write_text(OWN_SKILL, encoding="utf-8")
        shutil.rmtree(_factory(tmp_path) / "instances" / "coder" / "skills")

        outcome = await _update(client, coder)

        assert _template_step(outcome) == "Removed skills/house-style/SKILL.md."
        assert not (_directory(tmp_path, coder) / "skills" / "house-style").exists()
        assert own.read_text(encoding="utf-8") == OWN_SKILL

    asyncio.run(scenario())


def test_an_update_keeps_a_file_the_instance_changed_and_the_notice_stays(tmp_path):
    async def scenario() -> None:
        client = hub_client(factory_hub(tmp_path))
        coder = await _created_coder(client)
        system = _directory(tmp_path, coder) / "SYSTEM.md"
        system.write_text("You implement issues my way.\n", encoding="utf-8")
        await _edit(
            client,
            {
                "instances/coder/SYSTEM.md": "You implement issues well.\n",
                "instances/coder/skills/ponytail/SKILL.md": SKILL,
            },
        )

        outcome = await _update(client, coder)

        assert _template_step(outcome) == (
            "Wrote skills/ponytail/SKILL.md. Kept the instance's own SYSTEM.md."
        )
        assert system.read_text(encoding="utf-8") == "You implement issues my way.\n"
        assert (await _notices_by_instance(client))["tickets-coder"] == [CODER_BEHIND]

    asyncio.run(scenario())


def test_an_update_keeps_the_behavior_prompt_the_creation_was_given(tmp_path):
    async def scenario() -> None:
        client = hub_client(factory_hub(tmp_path))
        accepted = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                factory_role=FactoryRole(factory="tickets", name="coder"),
                model="openai:gpt-5",
                config={"behavior_prompt": "You implement issues my way.\n"},
                secrets={"api_key": "sk-test"},
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult), accepted
        assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED
        await _edit(client, {"instances/coder/SYSTEM.md": "You implement issues well.\n"})

        outcome = await _update(client, accepted.instance_id)

        assert _template_step(outcome) == "Kept the instance's own SYSTEM.md."
        system = _directory(tmp_path, accepted.instance_id) / "SYSTEM.md"
        assert system.read_text(encoding="utf-8") == "You implement issues my way.\n"

    asyncio.run(scenario())


def test_an_update_leaves_the_manifest_and_the_secrets_as_they_were(tmp_path):
    async def scenario() -> None:
        client = hub_client(factory_hub(tmp_path))
        coder = await _created_coder(client)
        directory = _directory(tmp_path, coder)
        manifest = (directory / "kinby.toml").read_bytes()
        secrets = (directory / ".env").read_bytes()
        await _edit(
            client,
            {
                "instances/coder/kinby.toml": '[feedback]\nask = "every-turn"\n',
                "instances/coder/SYSTEM.md": "You implement issues well.\n",
            },
        )

        outcome = await _update(client, coder)

        assert _template_step(outcome) == "Wrote SYSTEM.md."
        assert (directory / "kinby.toml").read_bytes() == manifest
        assert (directory / ".env").read_bytes() == secrets

    asyncio.run(scenario())


def test_an_instance_from_before_baselines_takes_every_template_file_once(tmp_path):
    async def scenario() -> None:
        hub = factory_hub(tmp_path)
        coder = await _created_coder(hub_client(hub))
        hub.close()
        with sqlite3.connect(tmp_path / "hub" / "registry.sqlite") as connection:
            connection.execute("UPDATE factory_members SET baseline = NULL")
        directory = _directory(tmp_path, coder)
        (directory / "SYSTEM.md").write_text("A hand copy.\n", encoding="utf-8")
        own = directory / "skills" / "mine" / "SKILL.md"
        own.parent.mkdir(parents=True)
        own.write_text(OWN_SKILL, encoding="utf-8")
        client = hub_client(factory_hub(tmp_path))

        outcome = await _update(client, coder)

        assert _template_step(outcome) == "Wrote SYSTEM.md."
        for path, content in FILES.items():
            if path.startswith("instances/coder/") and path != "instances/coder/kinby.toml":
                written = directory / path.removeprefix("instances/coder/")
                assert written.read_text(encoding="utf-8") == content
        assert own.read_text(encoding="utf-8") == OWN_SKILL
        assert await _notices_by_instance(client) == {
            "tickets-coder": [],
            "tickets-reviewer": [_no_record("reviewer")],
        }

    asyncio.run(scenario())


def test_an_update_whose_factory_fails_its_check_leaves_the_container_alone(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        client = hub_client(factory_hub(tmp_path, runtime=runtime))
        coder = await _created_coder(client)
        (_factory(tmp_path) / "factory.yaml").write_text("name: tickets\n", encoding="utf-8")

        outcome = await _update(client, coder)

        assert outcome.state is OperationState.FAILED
        assert [step.name for step in outcome.steps] == ["validate"]
        assert runtime.removed == []

    asyncio.run(scenario())


def test_an_update_stores_the_setup_the_template_asks_for_now(tmp_path):
    async def scenario() -> None:
        client = hub_client(factory_hub(tmp_path))
        coder = await _created_coder(client)
        await _edit(client, {"factory.yaml": WITH_SETUP["factory.yaml"]})

        await _update(client, coder)

        status = await client.call(INSTANCE_STATUS, InstanceStatusCommand(instance_id=coder))
        assert isinstance(status, InstanceStatusResult)
        assert status.setup.pending
        assert (
            SecretSetup(
                name="GITHUB_TOKEN",
                variable="GITHUB_TOKEN",
                label="GitHub token",
                required=True,
                is_set=False,
            )
            in status.setup.secrets
        )

    asyncio.run(scenario())


def test_an_update_never_writes_the_instances_memory(tmp_path):
    async def scenario() -> None:
        files = FILES | {"instances/coder/memory/profile.md": "Works on tickets.\n"}
        client = hub_client(factory_hub(tmp_path, files))
        coder = await _created_coder(client)
        await _edit(client, {"instances/coder/memory/profile.md": "Works on issues.\n"})

        outcome = await _update(client, coder)

        assert _template_step(outcome) == "Kept the instance's own memory/profile.md."
        profile = _directory(tmp_path, coder) / "memory" / "profile.md"
        assert profile.read_text(encoding="utf-8") == "Works on tickets.\n"

    asyncio.run(scenario())


def test_an_update_keeps_a_file_the_instance_changed_while_the_update_waited(tmp_path):
    async def scenario() -> None:
        images = EditingImages()
        client = hub_client(factory_hub(tmp_path, images=images))
        coder = await _created_coder(client)
        system = _directory(tmp_path, coder) / "SYSTEM.md"
        await _edit(client, {"instances/coder/SYSTEM.md": "You implement issues well.\n"})
        images.during_build = lambda: system.write_text("Edited meanwhile.\n", encoding="utf-8")

        outcome = await _update(client, coder)

        assert _template_step(outcome) == "Kept the instance's own SYSTEM.md."
        assert system.read_text(encoding="utf-8") == "Edited meanwhile.\n"

    asyncio.run(scenario())


def _link_file(directory: Path, outside: Path) -> str:
    (outside / "SYSTEM.md").write_text("Not the instance's.\n", encoding="utf-8")
    (directory / "SYSTEM.md").unlink()
    (directory / "SYSTEM.md").symlink_to(outside / "SYSTEM.md")
    return "SYSTEM.md"


def _link_folder(directory: Path, outside: Path) -> str:
    shutil.rmtree(directory / "skills")
    (directory / "skills").symlink_to(outside, target_is_directory=True)
    return "skills/ponytail/SKILL.md"


@pytest.mark.parametrize("link", [_link_file, _link_folder], ids=["file", "folder"])
def test_an_update_never_writes_through_a_link_the_instance_made(tmp_path, link):
    async def scenario() -> None:
        hub = factory_hub(tmp_path)
        coder = await _created_coder(hub_client(hub))
        hub.close()
        with sqlite3.connect(tmp_path / "hub" / "registry.sqlite") as connection:
            connection.execute("UPDATE factory_members SET baseline = NULL")
        outside = tmp_path / "outside"
        outside.mkdir()
        linked = link(_directory(tmp_path, coder), outside)
        before = {path: path.read_bytes() for path in outside.rglob("*")}
        client = hub_client(factory_hub(tmp_path))
        await _edit(
            client,
            {
                "instances/coder/SYSTEM.md": "You implement issues well.\n",
                "instances/coder/skills/ponytail/SKILL.md": SKILL,
            },
        )

        outcome = await _update(client, coder)

        assert f"Kept the instance's own {linked}." in _template_step(outcome)
        assert {path: path.read_bytes() for path in outside.rglob("*")} == before

    asyncio.run(scenario())


def test_an_update_moves_a_file_the_template_turned_into_a_folder(tmp_path):
    async def scenario() -> None:
        client = hub_client(factory_hub(tmp_path, FILES | {"instances/coder/notes": "Notes.\n"}))
        coder = await _created_coder(client)
        template = _factory(tmp_path) / "instances" / "coder"
        (template / "notes").unlink()
        (template / "notes").mkdir()
        (template / "notes" / "today.md").write_text("Today.\n", encoding="utf-8")

        outcome = await _update(client, coder)

        assert _template_step(outcome) == "Wrote notes/today.md. Removed notes."
        notes = _directory(tmp_path, coder) / "notes" / "today.md"
        assert notes.read_text(encoding="utf-8") == "Today.\n"

    asyncio.run(scenario())


def test_an_update_moves_a_folder_the_template_turned_into_a_file(tmp_path):
    async def scenario() -> None:
        files = FILES | {"instances/coder/notes/today.md": "Today.\n"}
        client = hub_client(factory_hub(tmp_path, files))
        coder = await _created_coder(client)
        template = _factory(tmp_path) / "instances" / "coder"
        shutil.rmtree(template / "notes")
        (template / "notes").write_text("Notes.\n", encoding="utf-8")

        outcome = await _update(client, coder)

        assert _template_step(outcome) == "Wrote notes. Removed notes/today.md."
        notes = _directory(tmp_path, coder) / "notes"
        assert notes.read_text(encoding="utf-8") == "Notes.\n"

    asyncio.run(scenario())


def test_an_update_keeps_a_folder_of_the_instances_own_where_the_template_wants_a_file(tmp_path):
    async def scenario() -> None:
        files = FILES | {"instances/coder/notes/today.md": "Today.\n"}
        client = hub_client(factory_hub(tmp_path, files))
        coder = await _created_coder(client)
        mine = _directory(tmp_path, coder) / "notes" / "mine.md"
        mine.write_text("Mine.\n", encoding="utf-8")
        template = _factory(tmp_path) / "instances" / "coder"
        shutil.rmtree(template / "notes")
        (template / "notes").write_text("Notes.\n", encoding="utf-8")

        outcome = await _update(client, coder)

        assert _template_step(outcome) == ("Removed notes/today.md. Kept the instance's own notes.")
        assert mine.read_text(encoding="utf-8") == "Mine.\n"

    asyncio.run(scenario())


def test_an_update_keeps_a_folder_where_the_template_dropped_its_file_and_the_notice_stays(
    tmp_path,
):
    async def scenario() -> None:
        client = hub_client(factory_hub(tmp_path, FILES | {"instances/coder/notes": "Notes.\n"}))
        coder = await _created_coder(client)
        notes = _directory(tmp_path, coder) / "notes"
        notes.unlink()
        notes.mkdir()
        (notes / "mine.md").write_text("Mine.\n", encoding="utf-8")
        (_factory(tmp_path) / "instances" / "coder" / "notes").unlink()

        outcome = await _update(client, coder)

        assert _template_step(outcome) == "Kept the instance's own notes."
        assert (notes / "mine.md").read_text(encoding="utf-8") == "Mine.\n"
        assert (await _notices_by_instance(client))["tickets-coder"] == [CODER_BEHIND]

    asyncio.run(scenario())


def test_an_update_keeps_a_link_where_the_template_dropped_its_file(tmp_path):
    async def scenario() -> None:
        client = hub_client(factory_hub(tmp_path))
        coder = await _created_coder(client)
        outside = tmp_path / "outside"
        outside.mkdir()
        system = _directory(tmp_path, coder) / "SYSTEM.md"
        _link_file(_directory(tmp_path, coder), outside)
        (_factory(tmp_path) / "instances" / "coder" / "SYSTEM.md").unlink()

        outcome = await _update(client, coder)

        assert _template_step(outcome) == "Kept the instance's own SYSTEM.md."
        assert system.is_symlink()
        assert (await _notices_by_instance(client))["tickets-coder"] == [CODER_BEHIND]

    asyncio.run(scenario())
