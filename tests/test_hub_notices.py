"""What an instance's summary tells the user to look at: a kinby revision behind the hub's, or an
environment without the hub route it holds."""

import asyncio
import sqlite3
from pathlib import Path
from uuid import UUID

from kinby.cli.client import ContractClient
from kinby.contracts import (
    INSTANCE_LIST,
    INSTANCE_UPDATE,
    HubRouteMissing,
    InstanceListCommand,
    InstanceListResult,
    InstanceNotice,
    InstanceUpdateCommand,
    LifecycleOperationResult,
    OperationState,
    RevisionBehind,
)
from kinby.hub import Hub
from kinby.hub.channels import ChannelNotification
from tests.test_hub import (
    FakeControl,
    FakeImages,
    created_instance,
    finished_operation,
    hub_at,
    hub_client,
    instance_environment,
    started_instance,
)
from tests.test_hub_channels import (
    FakeService,
    next_sent,
    paired_channel,
    raised,
    raising,
    until,
)
from tests.test_hub_server import served

#: The revision FakeImages builds, which is the hub's own until a test moves the hub on.
BUILT = "a" * 40
PUBLIC_URL = "https://kinby.example"


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


ROUTE_MISSING = HubRouteMissing(
    message=(
        "The instance cannot reach the hub with the scopes it holds, "
        "line:intake,line:owner,mcp:token,notification:raise. An instance update gives it the "
        "route."
    )
)


def _rewrite_environment(hub: Hub, instance_id: UUID, environment: dict[str, str]) -> None:
    (hub.instances_directory / str(instance_id) / ".env").write_text(
        "".join(f"{name}='{value}'\n" for name, value in environment.items())
    )


def _routed_hub(tmp_path: Path) -> Hub:
    """A hub that gives each instance its hub route."""
    return hub_at(tmp_path / "hub", control=FakeControl(), private_url="http://hub:8080")


def test_an_instance_without_its_hub_route_says_so_until_an_update(tmp_path):
    hub = _routed_hub(tmp_path)

    async def scenario() -> None:
        client = hub_client(hub)
        alice = (await created_instance(client)).instance_id
        environment = instance_environment(hub, alice)
        del environment["KINBY_HUB_URL"]
        del environment["KINBY_HUB_SCOPES"]
        _rewrite_environment(hub, alice, environment)

        assert await _notices(client) == [ROUTE_MISSING]

        updated = await client.call(
            INSTANCE_UPDATE, InstanceUpdateCommand(instance_id=alice, revision=BUILT)
        )
        assert isinstance(updated, LifecycleOperationResult)
        assert (await finished_operation(client, updated)).state is OperationState.SUCCEEDED

        assert await _notices(client) == []
        hub_url = f"http://hub:8080/instances/{alice}/hub"
        assert instance_environment(hub, alice)["KINBY_HUB_URL"] == hub_url

    asyncio.run(scenario())


def test_an_instance_holding_other_scopes_than_the_registry_grants_says_so(tmp_path):
    hub = _routed_hub(tmp_path)

    async def scenario() -> None:
        client = hub_client(hub)
        alice = (await created_instance(client)).instance_id
        environment = instance_environment(hub, alice)
        _rewrite_environment(hub, alice, environment | {"KINBY_HUB_SCOPES": "hub:read"})

        assert await _notices(client) == [ROUTE_MISSING]

    asyncio.run(scenario())


def _notifying_hub(directory: Path, images: FakeImages, service: FakeService) -> Hub:
    return hub_at(
        directory,
        images=images,
        control=FakeControl(),
        channel_kinds={"fake": service.kind()},
        public_url=PUBLIC_URL,
    )


def test_an_instance_notice_raises_one_notification_across_two_hub_starts(tmp_path):
    directory = tmp_path / "hub"
    images = FakeImages()

    async def set_up() -> UUID:
        hub = _notifying_hub(directory, images, FakeService())
        client = hub_client(hub)
        alice = await created_instance(client)
        async with served(hub) as address:
            await paired_channel(client, address)
        hub.close()
        return alice.instance_id

    async def start(key: str) -> list[ChannelNotification]:
        service = FakeService()
        hub = _notifying_hub(directory, images, service)
        await hub.recover()
        await raised(raising(hub, alice), key)
        await until(lambda: any(sent.title == key for sent in service.attempts))
        hub.close()
        return [sent for sent in service.attempts if sent.header.startswith("alice")]

    alice = asyncio.run(set_up())
    images.hub_revision = "b" * 40
    first = asyncio.run(start("first start"))
    second = asyncio.run(start("second start"))

    assert {sent.title for sent in first} == {"Revision behind", "first start"}
    [notice] = [sent for sent in first if sent.title == "Revision behind"]
    assert notice.header == "alice · Instance notice"
    assert notice.text.startswith("The instance runs kinby aaaaaaa")
    assert notice.link == f"{PUBLIC_URL}/instances/{alice}"
    assert notice.options == ("Mute this instance",)
    assert [sent.title for sent in second] == ["second start"]


def test_a_registry_from_before_instance_notices_keeps_its_notifications_and_raises_one(
    tmp_path,
):
    directory = tmp_path / "hub"
    images = FakeImages()

    async def set_up() -> UUID:
        service = FakeService()
        hub = _notifying_hub(directory, images, service)
        client = hub_client(hub)
        alice = await created_instance(client)
        async with served(hub) as address:
            await paired_channel(client, address)
        await raised(raising(hub, alice.instance_id), "before")
        await next_sent(service)
        hub.close()
        return alice.instance_id

    async def start() -> list[ChannelNotification]:
        service = FakeService()
        hub = _notifying_hub(directory, images, service)
        await hub.recover()
        await until(lambda: any(sent.title == "Revision behind" for sent in service.attempts))
        hub.close()
        return service.attempts

    asyncio.run(set_up())
    with sqlite3.connect(directory / "registry.sqlite") as connection:
        connection.executescript(
            """
            CREATE TABLE notifications_before (
                id TEXT PRIMARY KEY,
                instance_id TEXT NOT NULL,
                source_key TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                header TEXT NOT NULL,
                title TEXT NOT NULL,
                text TEXT NOT NULL,
                link TEXT,
                options TEXT NOT NULL,
                raised_at TEXT NOT NULL,
                UNIQUE (instance_id, source_key)
            );
            INSERT INTO notifications_before
            SELECT id, instance_id, source_key, thread_id, header, title, text, link, options,
                raised_at
            FROM notifications;
            DROP TABLE notifications;
            ALTER TABLE notifications_before RENAME TO notifications;
            """
        )
    images.hub_revision = "b" * 40
    sent = asyncio.run(start())

    assert [notice.header for notice in sent] == ["alice · Instance notice"]
    with sqlite3.connect(directory / "registry.sqlite") as connection:
        rows = connection.execute("SELECT title FROM notifications ORDER BY rowid")
        titles = [row[0] for row in rows]
    assert titles == ["before", "Revision behind"]


def test_an_instance_update_raises_the_notices_that_appeared_since(tmp_path):
    async def scenario() -> None:
        images = FakeImages()
        service = FakeService()
        hub = _notifying_hub(tmp_path / "hub", images, service)
        client = hub_client(hub)
        await created_instance(client)
        bob = await created_instance(client, name="bob")
        async with served(hub) as address:
            await paired_channel(client, address)
        images.hub_revision = "b" * 40

        updated = await client.call(
            INSTANCE_UPDATE, InstanceUpdateCommand(instance_id=bob.instance_id, revision=BUILT)
        )
        assert isinstance(updated, LifecycleOperationResult)
        await finished_operation(client, updated)

        await until(
            lambda: any(sent.header == "alice · Instance notice" for sent in service.attempts)
        )

    asyncio.run(scenario())
