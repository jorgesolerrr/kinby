"""Creating an instance from a prepared image, with its setup values and its avatar."""

import asyncio
import sqlite3
import tomllib
from dataclasses import replace
from uuid import UUID

import pytest

from kinby.cli.client import ContractClient
from kinby.contracts import (
    DEFAULT_AVATAR,
    IMAGE_DESCRIBE,
    INSTANCE_CREATE,
    INSTANCE_LIST,
    INSTANCE_STATUS,
    Avatar,
    AvatarColor,
    AvatarShape,
    DeclaredSetup,
    ErrorCode,
    ErrorEnvelope,
    ImageDescribeCommand,
    InstanceCreateCommand,
    InstanceListCommand,
    InstanceStatusCommand,
    InstanceStatusResult,
    LifecycleOperationResult,
    OperationState,
    SetupField,
    SetupFieldKind,
    SetupFieldType,
    SetupValue,
)
from kinby.hub import BuiltImage, ImageArtifact, ImageSelection
from kinby.instance import init_instance, inspect_instance
from kinby.setup import vanilla_description, with_built_in_fields
from tests.test_factory_templates import FILES, created_roles, factory_hub
from tests.test_hub import (
    FakeImages,
    FakeRuntime,
    finished_operation,
    hub_at,
    hub_client,
    instance_environment,
    prepared,
)


def test_creating_before_the_image_is_prepared_is_refused_and_records_nothing(tmp_path):
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
            {"model": "Model is required."},
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
    ids=["missing", "invalid-model", "undeclared-and-malformed-names"],
)
def test_missing_or_invalid_setup_values_are_refused_by_field_and_create_nothing(
    tmp_path, command, fields
):
    async def scenario() -> None:
        runtime = FakeRuntime()
        images = FakeImages()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images)
        client = hub_client(hub)
        await prepared(client)

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
        await prepared(client)

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
        await prepared(client)

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
    """Every build after the first makes a new image that asks for one more setting."""

    async def build(self, selection: ImageSelection) -> BuiltImage:
        built = await super().build(selection)
        if len(self.selections) == 1:
            return built
        return BuiltImage(replace(built.artifact, image_id="sha256:rebuilt-image"), reused=False)

    async def describe(self, artifact: ImageArtifact) -> DeclaredSetup:
        description = await super().describe(artifact)
        if artifact.image_id != "sha256:rebuilt-image":
            return description
        extra = SetupField(
            name="search_region",
            label="Search region",
            description="Where the search service runs.",
            kind=SetupFieldKind.CONFIG,
            type=SetupFieldType.TEXT,
            required=True,
        )
        return description.model_copy(update={"setup_fields": [*description.setup_fields, extra]})


def test_values_the_rebuilt_image_does_not_take_fail_validation_and_publish_nothing(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=RebuiltImages())
        client = hub_client(hub)
        await prepared(client)

        accepted = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="ada", model="openai:gpt-5", secrets={"api_key": "sk-private"}
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)
        described = await client.call(IMAGE_DESCRIBE, ImageDescribeCommand())

        assert outcome.state is OperationState.FAILED
        assert [(step.name, step.state) for step in outcome.steps] == [
            ("image", OperationState.SUCCEEDED),
            ("validate", OperationState.FAILED),
        ]
        assert "search_region: Search region is required." in outcome.detail
        assert runtime.created == []
        assert list(hub.instances_directory.iterdir()) == []
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert not isinstance(listed, ErrorEnvelope)
        assert listed.instances == []
        assert not isinstance(described, ErrorEnvelope)
        assert described.setup_fields[-1].name == "search_region"

    asyncio.run(scenario())


class MovingCheckout(FakeImages):
    """The hub's checkout builds whichever image `image_id` names, as its HEAD moves."""

    def __init__(self, declared: dict[str, DeclaredSetup]) -> None:
        super().__init__()
        self.declarations = declared
        self.image_id = next(iter(declared))

    async def build(self, selection: ImageSelection) -> BuiltImage:
        built = await super().build(selection)
        return BuiltImage(replace(built.artifact, image_id=self.image_id), reused=built.reused)

    async def describe(self, artifact: ImageArtifact) -> DeclaredSetup:
        self.described.append(artifact)
        return self.declarations[artifact.image_id]


def test_a_creation_refused_by_an_image_described_before_shows_that_images_fields(tmp_path):
    async def scenario() -> None:
        searching = DeclaredSetup(
            setup_fields=with_built_in_fields(
                [
                    SetupField(
                        name="search_region",
                        label="Search region",
                        description="Where the search service runs.",
                        kind=SetupFieldKind.CONFIG,
                        type=SetupFieldType.TEXT,
                        required=True,
                    )
                ]
            )
        )
        images = MovingCheckout(
            {"sha256:searching": searching, "sha256:plain": vanilla_description()}
        )
        hub = hub_at(tmp_path / "hub", images=images)
        client = hub_client(hub)
        await prepared(client)
        images.image_id = "sha256:plain"
        await prepared(client)
        images.image_id = "sha256:searching"

        accepted = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="ada", model="openai:gpt-5", secrets={"api_key": "sk-private"}
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)
        described = await client.call(IMAGE_DESCRIBE, ImageDescribeCommand())

        assert outcome.state is OperationState.FAILED
        assert "search_region: Search region is required." in outcome.detail
        assert [artifact.image_id for artifact in images.described] == [
            "sha256:searching",
            "sha256:plain",
        ]
        assert described == searching

    asyncio.run(scenario())


def test_the_hub_keeps_the_avatar_and_lists_it_with_the_instance(tmp_path):
    async def scenario() -> None:
        directory = tmp_path / "hub"
        hub = hub_at(directory)
        client = hub_client(hub)
        await prepared(client)
        chosen = await created(client, Avatar(shape=AvatarShape.SQUIRCLE, color=AvatarColor.GREEN))
        default = await created(client, name="bea")
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
        await prepared(client)
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


def _config_field(name: str, label: str, type: SetupFieldType, **declared) -> SetupField:
    return SetupField(
        name=name,
        label=label,
        description=f"{label}.",
        kind=SetupFieldKind.CONFIG,
        type=type,
        required=False,
        **declared,
    )


def declaring(*fields: SetupField) -> FakeImages:
    """Images that declare kinby's built-in fields, then *fields*."""
    return FakeImages(declared=DeclaredSetup(setup_fields=with_built_in_fields(fields)))


def test_a_blank_optional_secret_stays_unset(tmp_path):
    async def scenario() -> None:
        optional = [
            SetupField(
                name=name,
                label=label,
                description="Optional.",
                kind=SetupFieldKind.SECRET,
                type=SetupFieldType.TEXT,
                required=False,
            )
            for name, label in [("DRAFT_TOKEN", "Draft token"), ("LIVE_TOKEN", "Live token")]
        ]
        hub = hub_at(tmp_path / "hub", images=declaring(*optional))
        client = hub_client(hub)
        await prepared(client)

        accepted = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="editor",
                model="openai:gpt-5",
                secrets={
                    "api_key": "sk-private",
                    "DRAFT_TOKEN": "   ",
                    "LIVE_TOKEN": "live-private",
                },
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED

        environment = instance_environment(hub, accepted.instance_id)
        assert environment["LIVE_TOKEN"] == "live-private"
        assert environment["OPENAI_API_KEY"] == "sk-private"
        assert "DRAFT_TOKEN" not in environment
        assert "" not in environment.values()

    asyncio.run(scenario())


def _secret_field(name: str, label: str, type: SetupFieldType) -> SetupField:
    return SetupField(
        name=name,
        label=label,
        description=f"{label}.",
        kind=SetupFieldKind.SECRET,
        type=type,
        required=True,
    )


@pytest.mark.parametrize(
    "secrets",
    [{}, {"api_key": "  ", "SEARCH_TOKEN": ""}],
    ids=["missing", "blank"],
)
def test_a_required_secret_left_empty_is_created_pending_setup(tmp_path, secrets):
    async def scenario() -> None:
        images = declaring(_secret_field("SEARCH_TOKEN", "Search token", SetupFieldType.TEXT))
        hub = hub_at(tmp_path / "hub", images=images)
        client = hub_client(hub)
        await prepared(client)

        accepted = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(manifest_id="ada", model="openai:gpt-5", secrets=secrets),
        )
        assert isinstance(accepted, LifecycleOperationResult), accepted
        assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED
        status = await client.call(
            INSTANCE_STATUS, InstanceStatusCommand(instance_id=accepted.instance_id)
        )

        assert isinstance(status, InstanceStatusResult)
        assert status.setup.pending
        assert [(secret.name, secret.is_set) for secret in status.setup.secrets] == [
            ("api_key", False),
            ("SEARCH_TOKEN", False),
            ("BRAVE_API_KEY", False),
        ]
        environment = instance_environment(hub, accepted.instance_id)
        assert "OPENAI_API_KEY" not in environment
        assert "SEARCH_TOKEN" not in environment

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            _secret_field("GIT_USER_EMAIL", "Commit email", SetupFieldType.EMAIL),
            "not-an-email",
            "Commit email is not an email address, like someone@example.com.",
        ),
        (
            _secret_field("WEBHOOK_URL", "Webhook", SetupFieldType.URL),
            "owner/name",
            "Webhook is not a URL with a scheme and a host, like https://example.com.",
        ),
    ],
    ids=["email", "url"],
)
def test_a_present_secret_of_the_wrong_type_is_refused_on_its_field(
    tmp_path, field, value, message
):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=declaring(field))
        client = hub_client(hub)
        await prepared(client)

        refused = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="coder",
                model="openai:gpt-5",
                secrets={"api_key": "sk-private", field.name: value},
            ),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_SETUP
        assert refused.fields == {field.name: message}
        assert value not in refused.model_dump_json()
        assert runtime.created == []
        assert list(hub.instances_directory.iterdir()) == []

    asyncio.run(scenario())


def test_a_template_that_declares_serve_gets_the_hub_listen_address_in_that_one_table(tmp_path):
    async def scenario() -> None:
        served = '[serve]\nlisten = "127.0.0.1:9000"\n\n[budgets]\nsteps = 3\n'
        files = FILES | {"instances/coder/kinby.toml": served}
        hub = factory_hub(tmp_path, files)

        accepted = await created_roles(hub_client(hub))

        coder = accepted["coder"]
        written = (hub.instances_directory / str(coder) / "kinby.toml").read_text(encoding="utf-8")
        manifest = tomllib.loads(written)
        assert manifest["serve"] == {"listen": "0.0.0.0:8787"}
        assert manifest["budgets"]["steps"] == 3
        assert written.count("[serve]") == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("config", "fields"),
    [
        ({"tone": "loud"}, {"tone": "Tone is not one of plain, formal."}),
        ({"review": "yes"}, {"review": "Review is not true or false."}),
        ({"steps": "12"}, {"steps": "Steps per turn is not a whole number."}),
        ({"steps": True}, {"steps": "Steps per turn is not a whole number."}),
    ],
    ids=["choice", "boolean", "integer-as-text", "integer-as-boolean"],
)
def test_a_value_of_the_wrong_type_is_refused_on_its_field(
    tmp_path, config: dict[str, SetupValue], fields
):
    async def scenario() -> None:
        runtime = FakeRuntime()
        images = declaring(
            _config_field(
                "tone", "Tone", SetupFieldType.CHOICE, default="plain", choices=["plain", "formal"]
            ),
            _config_field("review", "Review", SetupFieldType.BOOLEAN, default=False),
            _config_field("steps", "Steps per turn", SetupFieldType.INTEGER, default=7),
        )
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images)
        client = hub_client(hub)
        await prepared(client)

        refused = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="editor",
                model="openai:gpt-5",
                config=config,
                secrets={"api_key": "sk-private"},
            ),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_SETUP
        assert refused.fields == fields
        assert runtime.created == []
        assert list(hub.instances_directory.iterdir()) == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "repository",
    ["not a repository", "owner/name", "git@github.com:owner/name.git", "https://"],
)
@pytest.mark.parametrize("email", ["not-an-email", "someone@example", "some one@example.com"])
def test_a_malformed_url_or_email_is_refused_on_its_field_and_creates_nothing(
    tmp_path, repository, email
):
    async def scenario() -> None:
        runtime = FakeRuntime()
        images = declaring(
            _config_field("repository", "Repository", SetupFieldType.URL),
            _config_field("commit_email", "Commit email", SetupFieldType.EMAIL),
        )
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images)
        client = hub_client(hub)
        await prepared(client)

        refused = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="coder",
                model="openai:gpt-5",
                config={"repository": repository, "commit_email": email},
                secrets={"api_key": "sk-private"},
            ),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_SETUP
        assert refused.fields == {
            "repository": "Repository is not a URL with a scheme and a host, "
            "like https://example.com.",
            "commit_email": "Commit email is not an email address, like someone@example.com.",
        }
        assert runtime.created == []
        assert list(hub.instances_directory.iterdir()) == []

    asyncio.run(scenario())


async def created(
    client: ContractClient, avatar: Avatar = DEFAULT_AVATAR, name: str = "ada"
) -> UUID:
    """Create a vanilla instance from the prepared image, and wait until it is published."""
    accepted = await client.call(
        INSTANCE_CREATE,
        InstanceCreateCommand(
            manifest_id=name,
            model="openai:gpt-5",
            secrets={"api_key": "sk-private"},
            avatar=avatar,
        ),
    )
    assert isinstance(accepted, LifecycleOperationResult)
    assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED
    return accepted.instance_id
