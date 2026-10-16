"""What an instance's summary tells the user to look at: a kinby revision behind the hub's,
files behind its factory's template, or an environment without the hub route it holds."""

import asyncio
import shutil
import sqlite3
from pathlib import Path
from uuid import UUID

import pytest

from kinby.cli.client import ContractClient
from kinby.contracts import (
    FACTORY_EDIT,
    INSTANCE_LIST,
    INSTANCE_UPDATE,
    FactoryEditCommand,
    FactoryResult,
    HubRouteMissing,
    InstanceListCommand,
    InstanceListResult,
    InstanceNotice,
    InstanceUpdateCommand,
    LifecycleOperationResult,
    OperationState,
    RevisionBehind,
    TemplateBehind,
)
from kinby.hub import Hub
from tests.test_factory_runs import factory_hub as routed_hub
from tests.test_factory_runs import started_coder
from tests.test_factory_templates import created_roles, factory_hub
from tests.test_hub import (
    FakeControl,
    FakeImages,
    FakeRuntime,
    created_instance,
    finished_operation,
    hub_at,
    hub_client,
    instance_environment,
    started_instance,
)
from tests.test_hub_factories import read

#: The revision FakeImages builds, which is the hub's own until a test moves the hub on.
BUILT = "a" * 40
SKILL = "---\nname: ponytail\ndescription: Be lazy.\n---\nBe lazy.\n"
CODER_BEHIND = TemplateBehind(
    message=(
        'Factory "tickets" changed template "coder" since the hub copied it. '
        "An instance update brings the files up to the factory's."
    )
)


def _folder(tmp_path: Path) -> Path:
    return tmp_path / "hub" / "factories" / "tickets"


async def _notices(client: ContractClient) -> list[InstanceNotice]:
    listed = await client.call(INSTANCE_LIST, InstanceListCommand())
    assert isinstance(listed, InstanceListResult)
    [instance] = listed.instances
    return instance.notices


def test_an_instance_on_the_hubs_revision_has_no_notice(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub")
        client = hub_client(hub)
        await started_instance(client, hub)

        assert await _notices(client) == []

    asyncio.run(scenario())


def test_an_instance_behind_the_hubs_revision_says_so(tmp_path):
    async def scenario() -> None:
        images = FakeImages()
        hub = hub_at(tmp_path / "hub", images=images)
        client = hub_client(hub)
        await started_instance(client, hub)

        images.hub_revision = "b" * 40

        assert await _notices(client) == [
            RevisionBehind(
                message="The instance runs kinby aaaaaaa, and the hub is at bbbbbbb.",
                instance_revision=BUILT,
                hub_revision="b" * 40,
            )
        ]

    asyncio.run(scenario())


async def _notices_by_instance(client: ContractClient) -> dict[str, list[InstanceNotice]]:
    listed = await client.call(INSTANCE_LIST, InstanceListCommand())
    assert isinstance(listed, InstanceListResult)
    return {instance.manifest_id: instance.notices for instance in listed.instances}


async def _edit(client: ContractClient, files: dict[str, str]) -> None:
    before = await read(client)
    edited = await client.call(
        FACTORY_EDIT, FactoryEditCommand(name="tickets", files=files, hash=before.hash)
    )
    assert isinstance(edited, FactoryResult)


def test_a_freshly_created_factory_instance_has_no_notice(tmp_path):
    async def scenario() -> None:
        client = hub_client(factory_hub(tmp_path))
        await created_roles(client)

        assert await _notices_by_instance(client) == {"tickets-coder": [], "tickets-reviewer": []}

    asyncio.run(scenario())


def test_an_instance_whose_template_file_the_factory_changed_says_so(tmp_path):
    async def scenario() -> None:
        client = hub_client(factory_hub(tmp_path))
        await created_roles(client)
        await created_instance(client)

        await _edit(client, {"instances/coder/SYSTEM.md": "You implement issues well.\n"})

        assert await _notices_by_instance(client) == {
            "tickets-coder": [CODER_BEHIND],
            "tickets-reviewer": [],
            "alice": [],
        }

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "files",
    [
        {"instances/coder/skills/ponytail/SKILL.md": SKILL},
        {"instances/coder/SYSTEM.md": "You implement issues well.\n"},
    ],
    ids=["added", "changed"],
)
def test_a_file_the_factory_added_or_changed_puts_its_instance_behind(tmp_path, files):
    async def scenario() -> None:
        client = hub_client(factory_hub(tmp_path))
        await created_roles(client)

        await _edit(client, files)

        assert await _notices_by_instance(client) == {
            "tickets-coder": [CODER_BEHIND],
            "tickets-reviewer": [],
        }

    asyncio.run(scenario())


def test_a_file_the_factory_dropped_puts_its_instance_behind(tmp_path):
    async def scenario() -> None:
        client = hub_client(factory_hub(tmp_path))
        await created_roles(client)

        (_folder(tmp_path) / "instances" / "coder" / "SYSTEM.md").unlink()

        assert await _notices_by_instance(client) == {
            "tickets-coder": [CODER_BEHIND],
            "tickets-reviewer": [],
        }

    asyncio.run(scenario())


def _remove(folder: Path) -> None:
    shutil.rmtree(folder)


def _break(folder: Path) -> None:
    (folder / "factory.yaml").write_text("name: tickets\n", encoding="utf-8")


def _undecodable_factory_file(folder: Path) -> None:
    (folder / "factory.yaml").write_bytes(b"name: tickets\n\xff\n")


def _unreadable_factory_file(folder: Path) -> None:
    (folder / "factory.yaml").unlink()
    (folder / "factory.yaml").mkdir()


def _undecodable_template_file(folder: Path) -> None:
    (folder / "instances" / "coder" / "SYSTEM.md").write_bytes(b"\xff\n")


@pytest.mark.parametrize(
    "unserve",
    [
        _remove,
        _break,
        _undecodable_factory_file,
        _unreadable_factory_file,
        _undecodable_template_file,
    ],
    ids=[
        "removed",
        "failing its check",
        "factory file not UTF-8",
        "factory file unreadable",
        "template file not UTF-8",
    ],
)
def test_an_instance_whose_factory_the_hub_does_not_serve_has_no_template_notice(tmp_path, unserve):
    async def scenario() -> None:
        client = hub_client(factory_hub(tmp_path))
        await created_roles(client)
        await _edit(client, {"instances/coder/SYSTEM.md": "You implement issues well.\n"})

        unserve(_folder(tmp_path))

        assert await _notices_by_instance(client) == {"tickets-coder": [], "tickets-reviewer": []}

    asyncio.run(scenario())


def test_a_factory_instance_from_before_baselines_says_the_hub_has_no_record(tmp_path):
    async def scenario() -> None:
        hub = factory_hub(tmp_path)
        await created_roles(hub_client(hub))
        hub.close()
        with sqlite3.connect(tmp_path / "hub" / "registry.sqlite") as connection:
            connection.execute("ALTER TABLE factory_members DROP COLUMN baseline")

        client = hub_client(factory_hub(tmp_path))

        assert await _notices_by_instance(client) == {
            "tickets-coder": [_no_record("coder")],
            "tickets-reviewer": [_no_record("reviewer")],
        }

    asyncio.run(scenario())


def _no_record(template: str) -> TemplateBehind:
    return TemplateBehind(
        message=(
            f'The hub kept no record of the files template "{template}" of factory "tickets" '
            "supplied. An instance update brings the files up to the factory's."
        )
    )


def test_a_template_file_the_hub_cannot_read_leaves_the_list_whole(tmp_path, monkeypatch):
    unreadable = _folder(tmp_path) / "instances" / "coder" / "SYSTEM.md"
    read_text = Path.read_text

    def denied(path: Path, *args, **kwargs) -> str:
        if path == unreadable:
            raise PermissionError(13, "Permission denied", str(path))
        return read_text(path, *args, **kwargs)

    async def scenario() -> None:
        client = hub_client(factory_hub(tmp_path))
        await created_roles(client)
        await _edit(client, {"instances/coder/SYSTEM.md": "You implement issues well.\n"})

        # The tests run as root, which reads past any file mode.
        monkeypatch.setattr(Path, "read_text", denied)

        assert await _notices_by_instance(client) == {"tickets-coder": [], "tickets-reviewer": []}

    asyncio.run(scenario())


ROUTE_MISSING = HubRouteMissing(
    message=(
        "The instance cannot reach the hub with the scopes it holds, factory:intake. "
        "An instance update gives it the route."
    )
)


def _rewrite_environment(hub: Hub, instance_id: UUID, environment: dict[str, str]) -> None:
    (hub.instances_directory / str(instance_id) / ".env").write_text(
        "".join(f"{name}='{value}'\n" for name, value in environment.items())
    )


def test_a_factory_instance_without_its_hub_route_says_so_until_an_update(tmp_path):
    runtime = FakeRuntime()
    hub = routed_hub(tmp_path / "hub", FakeControl(), runtime)

    async def scenario() -> None:
        client = hub_client(hub)
        coder = await started_coder(hub, runtime)
        environment = instance_environment(hub, coder)
        del environment["KINBY_HUB_URL"]
        _rewrite_environment(hub, coder, environment)

        assert await _notices(client) == [ROUTE_MISSING]

        updated = await client.call(
            INSTANCE_UPDATE, InstanceUpdateCommand(instance_id=coder, revision=BUILT)
        )
        assert isinstance(updated, LifecycleOperationResult)
        assert (await finished_operation(client, updated)).state is OperationState.SUCCEEDED

        assert await _notices(client) == []
        hub_url = f"http://hub:8080/instances/{coder}/hub"
        assert instance_environment(hub, coder)["KINBY_HUB_URL"] == hub_url

    asyncio.run(scenario())


def test_an_instance_holding_other_scopes_than_the_registry_grants_says_so(tmp_path):
    runtime = FakeRuntime()
    hub = routed_hub(tmp_path / "hub", FakeControl(), runtime)

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        environment = instance_environment(hub, coder)
        _rewrite_environment(hub, coder, environment | {"KINBY_HUB_SCOPES": "hub:read"})

        assert await _notices(hub_client(hub)) == [ROUTE_MISSING]

    asyncio.run(scenario())


def test_an_instance_the_registry_grants_no_scope_has_no_hub_route_notice(tmp_path):
    hub = routed_hub(tmp_path / "hub", FakeControl(), FakeRuntime())

    async def scenario() -> None:
        client = hub_client(hub)
        created = await created_instance(client)
        environment = instance_environment(hub, created.instance_id)
        _rewrite_environment(
            hub, created.instance_id, environment | {"KINBY_HUB_SCOPES": "factory:intake"}
        )

        assert await _notices(client) == []

    asyncio.run(scenario())
