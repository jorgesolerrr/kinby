import asyncio
import sqlite3

from kinby.contracts import (
    IMAGE_PREPARE,
    INSTANCE_LIST,
    OPERATION_GET,
    PACKAGE_DESCRIBE,
    ErrorCode,
    ErrorEnvelope,
    ImagePrepareCommand,
    ImagePrepareResult,
    InstanceListCommand,
    OperationGetCommand,
    OperationKind,
    OperationState,
    PackageDescribeCommand,
    PackageSelection,
    SetupFieldKind,
    SetupFieldType,
)
from kinby.hub import Hub, ImageArtifact, ImageSelection
from kinby.packages import InstalledPackage, PackageDescriptor, RequiredSecret
from tests.test_hub import (
    FakeImages,
    FakeRuntime,
    created_instance,
    finished_operation,
    hub_at,
    hub_client,
    prepared,
)

WRITER = PackageSelection(id="writer", distribution="kinby-writer", version="1.4.2")


class HeldImages(FakeImages):
    """Hold every build until the test releases it, so a preparation stays in flight."""

    def __init__(self) -> None:
        super().__init__(package=writer_package())
        self.building = asyncio.Event()
        self.release = asyncio.Event()

    async def build(self, selection: ImageSelection) -> ImageArtifact:
        self.building.set()
        await self.release.wait()
        return await super().build(selection)


def writer_package() -> InstalledPackage:
    return InstalledPackage(
        descriptor=PackageDescriptor(
            id="writer",
            display_name="Writing teammate",
            description="Drafts articles.",
            icon="pen",
            distribution="kinby-writer",
            version="1.4.2",
            required_secrets=(
                RequiredSecret(
                    name="EDITOR_TOKEN",
                    label="Editor token",
                    description="Publishes drafts.",
                ),
            ),
        ),
        files={"SYSTEM.md": "Write clearly.\n"},
    )


def test_preparing_vanilla_builds_the_base_image_then_reads_its_descriptor(tmp_path):
    async def scenario() -> None:
        images = FakeImages()
        hub = hub_at(tmp_path / "hub", images=images)
        client = hub_client(hub)

        outcome = await prepared(client, None)

        assert outcome.kind is OperationKind.PREPARE
        assert outcome.instance_id is None
        assert outcome.state is OperationState.SUCCEEDED
        assert [(step.name, step.state) for step in outcome.steps] == [
            ("image", OperationState.SUCCEEDED),
            ("describe", OperationState.SUCCEEDED),
        ]
        assert images.selections == [ImageSelection("HEAD", None)]
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert not isinstance(listed, ErrorEnvelope)
        assert listed.instances == []

    asyncio.run(scenario())


def test_describing_vanilla_lists_the_built_in_fields_the_preparation_stored(tmp_path):
    async def scenario() -> None:
        directory = tmp_path / "hub"
        hub = hub_at(directory)
        await prepared(hub_client(hub), None)
        hub.close()
        images = FakeImages()
        reopened = Hub(directory, runtime=FakeRuntime(), images=images)

        described = await hub_client(reopened).call(
            PACKAGE_DESCRIBE, PackageDescribeCommand(package=None)
        )

        assert not isinstance(described, ErrorEnvelope)
        assert [
            (field.name, field.kind, field.type, field.required) for field in described.setup_fields
        ] == [
            ("model", SetupFieldKind.CONFIG, SetupFieldType.TEXT, True),
            ("api_key", SetupFieldKind.SECRET, SetupFieldType.TEXT, True),
            ("behavior_prompt", SetupFieldKind.CONFIG, SetupFieldType.MULTILINE, False),
        ]
        assert described.version
        assert images.selections == []
        assert images.described == []

    asyncio.run(scenario())


def test_describing_a_selection_never_prepared_is_not_prepared_and_builds_nothing(tmp_path):
    async def scenario() -> None:
        images = FakeImages()
        hub = hub_at(tmp_path / "hub", images=images)
        client = hub_client(hub)
        await prepared(client, None)

        described = await client.call(PACKAGE_DESCRIBE, PackageDescribeCommand(package=WRITER))

        assert isinstance(described, ErrorEnvelope)
        assert described.code is ErrorCode.NOT_PREPARED
        assert images.selections == [ImageSelection("HEAD", None)]

    asyncio.run(scenario())


def test_describing_a_package_puts_the_built_in_fields_before_its_own(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=FakeImages(package=writer_package()))
        client = hub_client(hub)
        await prepared(client, WRITER)

        described = await client.call(PACKAGE_DESCRIBE, PackageDescribeCommand(package=WRITER))

        assert not isinstance(described, ErrorEnvelope)
        assert (described.display_name, described.icon, described.version) == (
            "Writing teammate",
            "pen",
            "1.4.2",
        )
        assert [(field.name, field.kind, field.required) for field in described.setup_fields] == [
            ("model", SetupFieldKind.CONFIG, True),
            ("api_key", SetupFieldKind.SECRET, True),
            ("EDITOR_TOKEN", SetupFieldKind.SECRET, True),
        ]

    asyncio.run(scenario())


def test_preparing_a_selection_already_being_prepared_returns_the_running_operation(tmp_path):
    async def scenario() -> None:
        images = HeldImages()
        hub = hub_at(tmp_path / "hub", images=images)
        client = hub_client(hub)

        first = await client.call(IMAGE_PREPARE, ImagePrepareCommand(package=None))
        await images.building.wait()
        again = await client.call(IMAGE_PREPARE, ImagePrepareCommand(package=None))
        other = await client.call(IMAGE_PREPARE, ImagePrepareCommand(package=WRITER))
        images.release.set()

        assert isinstance(first, ImagePrepareResult)
        assert isinstance(again, ImagePrepareResult)
        assert isinstance(other, ImagePrepareResult)
        assert again.operation_id == first.operation_id
        assert other.operation_id != first.operation_id
        assert (await finished_operation(client, first)).state is OperationState.SUCCEEDED
        assert (await finished_operation(client, other)).state is OperationState.SUCCEEDED
        assert sorted(images.selections, key=lambda selection: selection.package is not None) == [
            ImageSelection("HEAD", None),
            ImageSelection("HEAD", WRITER),
        ]

    asyncio.run(scenario())


def test_a_failed_candidate_check_fails_the_describe_step_and_stores_nothing(tmp_path):
    async def scenario() -> None:
        hub = hub_at(
            tmp_path / "hub",
            images=FakeImages(check_failure='Executable "claude" is not on PATH.'),
        )
        client = hub_client(hub)

        outcome = await prepared(client, None)
        described = await client.call(PACKAGE_DESCRIBE, PackageDescribeCommand(package=None))

        assert outcome.state is OperationState.FAILED
        assert [(step.name, step.state, step.detail) for step in outcome.steps] == [
            ("image", OperationState.SUCCEEDED, "Building the image, or reusing the one prepared."),
            ("describe", OperationState.FAILED, 'Executable "claude" is not on PATH.'),
        ]
        assert isinstance(described, ErrorEnvelope)
        assert described.code is ErrorCode.NOT_PREPARED

    asyncio.run(scenario())


def test_a_preparation_the_hub_did_not_finish_is_failed_when_it_opens_again(tmp_path):
    async def scenario() -> None:
        directory = tmp_path / "hub"
        images = HeldImages()
        hub = hub_at(directory, images=images)
        accepted = await hub_client(hub).call(IMAGE_PREPARE, ImagePrepareCommand(package=None))
        assert isinstance(accepted, ImagePrepareResult)
        await images.building.wait()
        hub.close()

        reopened = Hub(directory, runtime=FakeRuntime(), images=FakeImages())
        outcome = await hub_client(reopened).call(
            OPERATION_GET, OperationGetCommand(operation_id=accepted.operation_id)
        )

        assert not isinstance(outcome, ErrorEnvelope)
        assert outcome.state is OperationState.FAILED
        assert outcome.detail == "The hub stopped before this operation finished."

    asyncio.run(scenario())


def test_a_registry_from_before_preparations_keeps_its_operations_and_prepares(tmp_path):
    async def scenario() -> None:
        directory = tmp_path / "hub"
        hub = hub_at(directory)
        created = await created_instance(hub_client(hub))
        hub.close()
        with sqlite3.connect(directory / "registry.sqlite") as connection:
            connection.executescript(
                """
                CREATE TABLE operations_before (
                    id TEXT PRIMARY KEY,
                    instance_id TEXT NOT NULL REFERENCES instances(id),
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    detail TEXT NOT NULL
                );
                INSERT INTO operations_before SELECT id, instance_id, kind, state, detail
                FROM operations WHERE instance_id IS NOT NULL;
                DROP TABLE operations;
                ALTER TABLE operations_before RENAME TO operations;
                """
            )

        reopened = hub_at(directory)
        client = hub_client(reopened)
        creation = await client.call(
            OPERATION_GET, OperationGetCommand(operation_id=created.operation_id)
        )
        preparation = await prepared(client, None)

        assert not isinstance(creation, ErrorEnvelope)
        assert (creation.instance_id, creation.state) == (
            created.instance_id,
            OperationState.SUCCEEDED,
        )
        assert preparation.state is OperationState.SUCCEEDED

    asyncio.run(scenario())
