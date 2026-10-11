import asyncio
import sqlite3

from kinby.contracts import (
    IMAGE_DESCRIBE,
    IMAGE_PREPARE,
    INSTANCE_LIST,
    OPERATION_GET,
    ErrorCode,
    ErrorEnvelope,
    ImageDescribeCommand,
    ImagePrepareCommand,
    ImagePrepareResult,
    InstanceListCommand,
    OperationGetCommand,
    OperationKind,
    OperationState,
    SetupFieldKind,
    SetupFieldType,
)
from kinby.hub import BuiltImage, Hub, ImageSelection
from tests.test_hub import (
    FakeImages,
    FakeRuntime,
    created_instance,
    finished_operation,
    hub_at,
    hub_client,
    prepared,
)


class HeldImages(FakeImages):
    """Hold every build until the test releases it, so a preparation stays in flight."""

    def __init__(self) -> None:
        super().__init__()
        self.building = asyncio.Event()
        self.release = asyncio.Event()

    async def build(self, selection: ImageSelection) -> BuiltImage:
        self.building.set()
        await self.release.wait()
        return await super().build(selection)


def test_preparing_vanilla_builds_the_base_image_then_reads_its_descriptor(tmp_path):
    async def scenario() -> None:
        images = FakeImages()
        hub = hub_at(tmp_path / "hub", images=images)
        client = hub_client(hub)

        outcome = await prepared(client)

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


def test_preparing_the_image_the_first_time_says_each_step_did_its_work(tmp_path):
    async def scenario() -> None:
        client = hub_client(hub_at(tmp_path / "hub"))

        outcome = await prepared(client)

        assert [(step.name, step.state, step.detail) for step in outcome.steps] == [
            ("image", OperationState.SUCCEEDED, "Built the image."),
            ("describe", OperationState.SUCCEEDED, "Read what the image declares."),
        ]
        assert outcome.detail == "Image prepared."

    asyncio.run(scenario())


def test_preparing_the_image_again_says_it_reused_the_image(tmp_path):
    async def scenario() -> None:
        client = hub_client(hub_at(tmp_path / "hub"))
        await prepared(client)

        again = await prepared(client)

        assert again.state is OperationState.SUCCEEDED
        assert [(step.name, step.detail) for step in again.steps] == [
            ("image", "Reused the image already prepared."),
            ("describe", "Read what the image declares."),
        ]

    asyncio.run(scenario())


def test_describing_vanilla_lists_the_built_in_fields_the_preparation_stored(tmp_path):
    async def scenario() -> None:
        directory = tmp_path / "hub"
        hub = hub_at(directory)
        await prepared(hub_client(hub))
        hub.close()
        images = FakeImages()
        reopened = Hub(directory, runtime=FakeRuntime(), images=images)

        described = await hub_client(reopened).call(IMAGE_DESCRIBE, ImageDescribeCommand())

        assert not isinstance(described, ErrorEnvelope)
        assert [
            (field.name, field.kind, field.type, field.required) for field in described.setup_fields
        ] == [
            ("model", SetupFieldKind.CONFIG, SetupFieldType.TEXT, True),
            ("api_key", SetupFieldKind.SECRET, SetupFieldType.TEXT, True),
            ("behavior_prompt", SetupFieldKind.CONFIG, SetupFieldType.MULTILINE, False),
        ]
        assert images.selections == []
        assert images.described == []

    asyncio.run(scenario())


def test_describing_an_image_never_prepared_is_not_prepared_and_builds_nothing(tmp_path):
    async def scenario() -> None:
        images = FakeImages()
        hub = hub_at(tmp_path / "hub", images=images)

        described = await hub_client(hub).call(IMAGE_DESCRIBE, ImageDescribeCommand())

        assert isinstance(described, ErrorEnvelope)
        assert described.code is ErrorCode.NOT_PREPARED
        assert images.selections == []

    asyncio.run(scenario())


def test_preparing_the_image_while_it_is_being_prepared_returns_the_running_operation(tmp_path):
    async def scenario() -> None:
        images = HeldImages()
        hub = hub_at(tmp_path / "hub", images=images)
        client = hub_client(hub)

        first = await client.call(IMAGE_PREPARE, ImagePrepareCommand())
        await images.building.wait()
        again = await client.call(IMAGE_PREPARE, ImagePrepareCommand())
        images.release.set()

        assert isinstance(first, ImagePrepareResult)
        assert isinstance(again, ImagePrepareResult)
        assert again.operation_id == first.operation_id
        assert (await finished_operation(client, first)).state is OperationState.SUCCEEDED
        assert images.selections == [ImageSelection("HEAD", None)]

    asyncio.run(scenario())


def test_a_failed_description_fails_the_describe_step_and_stores_nothing(tmp_path):
    async def scenario() -> None:
        hub = hub_at(
            tmp_path / "hub",
            images=FakeImages(check_failure='Executable "claude" is not on PATH.'),
        )
        client = hub_client(hub)

        outcome = await prepared(client)
        described = await client.call(IMAGE_DESCRIBE, ImageDescribeCommand())

        assert outcome.state is OperationState.FAILED
        assert [(step.name, step.state, step.detail) for step in outcome.steps] == [
            ("image", OperationState.SUCCEEDED, "Built the image."),
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
        accepted = await hub_client(hub).call(IMAGE_PREPARE, ImagePrepareCommand())
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
        preparation = await prepared(client)

        assert not isinstance(creation, ErrorEnvelope)
        assert (creation.instance_id, creation.state) == (
            created.instance_id,
            OperationState.SUCCEEDED,
        )
        assert preparation.state is OperationState.SUCCEEDED

    asyncio.run(scenario())
