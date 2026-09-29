"""What an instance's summary tells the user to look at: a kinby revision or a package template."""

import asyncio
from dataclasses import replace

from kinby.cli.client import ContractClient
from kinby.contracts import (
    INSTANCE_LIST,
    INSTANCE_UPDATE,
    InstanceListCommand,
    InstanceListResult,
    InstanceNotice,
    InstanceUpdateCommand,
    LifecycleOperationResult,
    OperationState,
    PackagePin,
    PackageTemplateOlder,
    RevisionBehind,
)
from tests.test_hub import FakeControl, FakeImages, finished_operation, hub_at, hub_client
from tests.test_hub import started_instance as started_vanilla
from tests.test_hub_update import NEXT_COMMIT, CandidateImages, started_writer, writer_package

#: The revision FakeImages builds, which is the hub's own until a test moves the hub on.
BUILT = "a" * 40


async def _notices(client: ContractClient) -> list[InstanceNotice]:
    listed = await client.call(INSTANCE_LIST, InstanceListCommand())
    assert isinstance(listed, InstanceListResult)
    [instance] = listed.instances
    return instance.notices


def test_an_instance_on_the_hubs_revision_and_its_template_version_has_no_notice(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=FakeImages(package=writer_package()))
        client = hub_client(hub)
        await started_writer(client, hub)

        assert await _notices(client) == []

    asyncio.run(scenario())


def test_an_instance_behind_the_hubs_revision_says_so(tmp_path):
    async def scenario() -> None:
        images = FakeImages()
        hub = hub_at(tmp_path / "hub", images=images)
        client = hub_client(hub)
        await started_vanilla(client, hub)

        images.hub_revision = "b" * 40

        assert await _notices(client) == [
            RevisionBehind(
                message="The instance runs kinby aaaaaaa, and the hub is at bbbbbbb.",
                instance_revision=BUILT,
                hub_revision="b" * 40,
            )
        ]

    asyncio.run(scenario())


def test_an_instance_whose_package_moved_past_its_template_says_so(tmp_path):
    async def scenario() -> None:
        images = CandidateImages(package=writer_package())
        hub = hub_at(tmp_path / "hub", images=images, control=FakeControl())
        client = hub_client(hub)
        created = await started_writer(client, hub)
        newer = writer_package()
        images.package = replace(newer, descriptor=replace(newer.descriptor, version="1.5.0"))
        # The hub moves to the revision the update builds, so only the template is behind.
        images.hub_revision = "v0.2.0-resolved"

        accepted = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(
                instance_id=created.instance_id,
                revision="v0.2.0",
                package=PackagePin(id="writer", sha=NEXT_COMMIT),
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)
        assert outcome.state is OperationState.SUCCEEDED, outcome.detail

        assert await _notices(client) == [
            PackageTemplateOlder(
                message=(
                    "The instance's configuration was copied from writer 1.4.2, "
                    "and 1.5.0 is installed. An update never copies it again."
                ),
                initialized_version="1.4.2",
                installed_version="1.5.0",
            )
        ]

    asyncio.run(scenario())
