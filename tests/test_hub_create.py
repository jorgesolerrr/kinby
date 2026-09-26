"""Creating an instance from a prepared image, with its setup values and its avatar."""

import asyncio
import sqlite3
from dataclasses import replace
from uuid import UUID

import pytest

from kinby.cli.client import ContractClient
from kinby.contracts import (
    DEFAULT_AVATAR,
    INSTANCE_CREATE,
    INSTANCE_LIST,
    PACKAGE_DESCRIBE,
    Avatar,
    AvatarColor,
    AvatarShape,
    ErrorCode,
    ErrorEnvelope,
    InstanceCreateCommand,
    InstanceListCommand,
    LifecycleOperationResult,
    OperationState,
    PackageDescribeCommand,
    PackageDescription,
    SetupField,
    SetupFieldKind,
    SetupFieldType,
)
from kinby.hub import ImageArtifact, ImageSelection
from kinby.instance import init_instance, inspect_instance
from tests.test_hub import (
    FakeImages,
    FakeRuntime,
    finished_operation,
    hub_at,
    hub_client,
    instance_environment,
    prepared,
)


def test_creating_from_a_selection_never_prepared_is_not_prepared_and_records_nothing(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        images = FakeImages()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images)
        client = hub_client(hub)

        refused = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="ada",
                model="openai:gpt-5",
                secrets={"api_key": "sk-private"},
            ),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.NOT_PREPARED
        assert images.selections == []
        assert runtime.created == []
        assert list(hub.instances_directory.iterdir()) == []
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert not isinstance(listed, ErrorEnvelope)
        assert listed.instances == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("command", "fields"),
    [
        (
            InstanceCreateCommand(manifest_id="ada", model=""),
            {"model": "Model is required.", "api_key": "API key is required."},
        ),
        (
            InstanceCreateCommand(
                manifest_id="ada", model="openai:gpt-5", secrets={"api_key": "  "}
            ),
            {"api_key": "API key is required."},
        ),
        (
            InstanceCreateCommand(
                manifest_id="ada", model="gpt-5", secrets={"api_key": "sk-private"}
            ),
            {"model": "Name the provider and the model, like openai:gpt-5."},
        ),
        (
            InstanceCreateCommand(
                manifest_id="ada",
                model="openai:gpt-5",
                config={"temperature": "0.2"},
                secrets={"api_key": "sk-private", "BAD KEY": "sk-other"},
            ),
            {
                "temperature": "This image declares no configuration field by this name.",
                "BAD KEY": "A secret's name must be an environment variable name.",
            },
        ),
    ],
    ids=["missing", "blank-secret", "invalid-model", "undeclared-and-malformed-names"],
)
def test_missing_or_invalid_setup_values_are_refused_by_field_and_create_nothing(
    tmp_path, command, fields
):
    async def scenario() -> None:
        runtime = FakeRuntime()
        images = FakeImages()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images)
        client = hub_client(hub)
        await prepared(client, None)

        refused = await client.call(INSTANCE_CREATE, command)

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_SETUP
        assert refused.fields == fields
        assert "sk-private" not in refused.model_dump_json()
        assert images.selections == [ImageSelection("HEAD", None)]
        assert runtime.created == []
        assert list(hub.instances_directory.iterdir()) == []
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert not isinstance(listed, ErrorEnvelope)
        assert listed.instances == []

    asyncio.run(scenario())


def test_a_valid_vanilla_creation_writes_the_built_in_fields_then_publishes(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        client = hub_client(hub)
        await prepared(client, None)

        accepted = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="ada",
                persona_name="Ada",
                model="anthropic:claude-opus-5-5",
                config={"behavior_prompt": "Answer in haiku.\n"},
                secrets={"api_key": "sk-private"},
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)

        assert outcome.state is OperationState.SUCCEEDED
        assert [(step.name, step.state) for step in outcome.steps] == [
            ("image", OperationState.SUCCEEDED),
            ("validate", OperationState.SUCCEEDED),
            ("initialize", OperationState.SUCCEEDED),
            ("publish", OperationState.SUCCEEDED),
        ]
        instance_path = hub.instances_directory / str(accepted.instance_id)
        assert inspect_instance(instance_path).manifest.models.main == "anthropic:claude-opus-5-5"
        assert (instance_path / "SYSTEM.md").read_text() == "Answer in haiku.\n"
        environment = instance_environment(hub, accepted.instance_id)
        assert environment["ANTHROPIC_API_KEY"] == "sk-private"
        assert "api_key" not in environment
        assert runtime.created[0].env["ANTHROPIC_API_KEY"] == "sk-private"
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert not isinstance(listed, ErrorEnvelope)
        assert [summary.instance_id for summary in listed.instances] == [accepted.instance_id]

    asyncio.run(scenario())


def test_a_vanilla_creation_without_a_behavior_prompt_keeps_the_default_one(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub")
        client = hub_client(hub)
        await prepared(client, None)

        accepted = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="ada",
                model="openai:gpt-5",
                config={"behavior_prompt": ""},
                secrets={"api_key": "sk-private"},
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED

        default = tmp_path / "default"
        init_instance(default, model="openai:gpt-5")
        system = (hub.instances_directory / str(accepted.instance_id) / "SYSTEM.md").read_text()
        assert system == (default / "SYSTEM.md").read_text()

    asyncio.run(scenario())


class RebuiltImages(FakeImages):
    """Every build after the first makes a new image that asks for one more secret."""

    async def build(self, selection: ImageSelection) -> ImageArtifact:
        artifact = await super().build(selection)
        if len(self.selections) == 1:
            return artifact
        return replace(artifact, image_id="sha256:rebuilt-image")

    async def describe(self, artifact: ImageArtifact) -> PackageDescription:
        description = await super().describe(artifact)
        if artifact.image_id != "sha256:rebuilt-image":
            return description
        extra = SetupField(
            name="SEARCH_TOKEN",
            label="Search token",
            description="Reaches the search service.",
            kind=SetupFieldKind.SECRET,
            type=SetupFieldType.TEXT,
            required=True,
        )
        return description.model_copy(update={"setup_fields": [*description.setup_fields, extra]})


def test_values_the_rebuilt_image_does_not_take_fail_validation_and_publish_nothing(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=RebuiltImages())
        client = hub_client(hub)
        await prepared(client, None)

        accepted = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="ada", model="openai:gpt-5", secrets={"api_key": "sk-private"}
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)
        described = await client.call(PACKAGE_DESCRIBE, PackageDescribeCommand(package=None))

        assert outcome.state is OperationState.FAILED
        assert [(step.name, step.state) for step in outcome.steps] == [
            ("image", OperationState.SUCCEEDED),
            ("validate", OperationState.FAILED),
        ]
        assert "SEARCH_TOKEN: Search token is required." in outcome.detail
        assert runtime.created == []
        assert list(hub.instances_directory.iterdir()) == []
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert not isinstance(listed, ErrorEnvelope)
        assert listed.instances == []
        assert not isinstance(described, ErrorEnvelope)
        assert described.setup_fields[-1].name == "SEARCH_TOKEN"

    asyncio.run(scenario())


def test_the_hub_keeps_the_avatar_and_lists_it_with_the_instance(tmp_path):
    async def scenario() -> None:
        directory = tmp_path / "hub"
        hub = hub_at(directory)
        client = hub_client(hub)
        await prepared(client, None)
        chosen = await created(client, Avatar(shape=AvatarShape.SQUIRCLE, color=AvatarColor.GREEN))
        default = await created(client)
        hub.close()

        listed = await hub_client(hub_at(directory)).call(INSTANCE_LIST, InstanceListCommand())

        assert not isinstance(listed, ErrorEnvelope)
        avatars = {summary.instance_id: summary.avatar for summary in listed.instances}
        assert avatars == {
            chosen: Avatar(shape=AvatarShape.SQUIRCLE, color=AvatarColor.GREEN),
            default: Avatar(shape=AvatarShape.CIRCLE, color=AvatarColor.BLUE),
        }
        instance_files = directory / "instances" / str(chosen)
        assert not any(
            "squircle" in path.read_text(errors="ignore")
            for path in instance_files.rglob("*")
            if path.is_file()
        )

    asyncio.run(scenario())


def test_an_instance_created_before_avatars_lists_the_default_one(tmp_path):
    async def scenario() -> None:
        directory = tmp_path / "hub"
        hub = hub_at(directory)
        client = hub_client(hub)
        await prepared(client, None)
        instance_id = await created(client)
        hub.close()
        with sqlite3.connect(directory / "registry.sqlite") as connection:
            connection.executescript(
                """
                ALTER TABLE instances DROP COLUMN avatar_shape;
                ALTER TABLE instances DROP COLUMN avatar_color;
                """
            )

        listed = await hub_client(hub_at(directory)).call(INSTANCE_LIST, InstanceListCommand())

        assert not isinstance(listed, ErrorEnvelope)
        assert [(summary.instance_id, summary.avatar) for summary in listed.instances] == [
            (instance_id, Avatar(shape=AvatarShape.CIRCLE, color=AvatarColor.BLUE))
        ]

    asyncio.run(scenario())


async def created(client: ContractClient, avatar: Avatar = DEFAULT_AVATAR) -> UUID:
    """Create a vanilla instance from the prepared image, and wait until it is published."""
    accepted = await client.call(
        INSTANCE_CREATE,
        InstanceCreateCommand(
            manifest_id="ada",
            model="openai:gpt-5",
            secrets={"api_key": "sk-private"},
            avatar=avatar,
        ),
    )
    assert isinstance(accepted, LifecycleOperationResult)
    assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED
    return accepted.instance_id
