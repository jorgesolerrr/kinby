"""What an instance's summary tells the user to look at: a kinby revision behind the hub's."""

import asyncio

from kinby.cli.client import ContractClient
from kinby.contracts import (
    INSTANCE_LIST,
    InstanceListCommand,
    InstanceListResult,
    RevisionBehind,
)
from tests.test_hub import FakeImages, hub_at, hub_client, started_instance

#: The revision FakeImages builds, which is the hub's own until a test moves the hub on.
BUILT = "a" * 40


async def _notices(client: ContractClient) -> list[RevisionBehind]:
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
