"""Creating an instance whose secret fields take hub secrets, or save typed values as new ones."""

import asyncio
import os

import pytest

from kinby.cli.client import ContractClient
from kinby.contracts import (
    INSTANCE_CREATE,
    INSTANCE_STATUS,
    ErrorCode,
    ErrorEnvelope,
    InstanceCreateCommand,
    InstanceStatusCommand,
    InstanceStatusResult,
    LifecycleOperationResult,
    OperationState,
    SetupField,
    SetupFieldKind,
    SetupFieldType,
)
from tests.test_hub import (
    FakeRuntime,
    finished_operation,
    hub_at,
    hub_client,
    instance_environment,
    prepared,
)
from tests.test_hub_create import declaring
from tests.test_hub_secret_methods import set_hub_secret

_SENTINEL = "sentinel-linked-secret"

_GITHUB_TOKEN = SetupField(
    name="GITHUB_TOKEN",
    label="GitHub token",
    description="GitHub token.",
    kind=SetupFieldKind.SECRET,
    type=SetupFieldType.TEXT,
    required=True,
)


async def _status(client: ContractClient, command: InstanceCreateCommand) -> InstanceStatusResult:
    accepted = await client.call(INSTANCE_CREATE, command)
    assert isinstance(accepted, LifecycleOperationResult), accepted
    assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED
    status = await client.call(
        INSTANCE_STATUS, InstanceStatusCommand(instance_id=accepted.instance_id)
    )
    assert isinstance(status, InstanceStatusResult)
    return status


def test_linked_fields_hold_the_hub_secrets_values_and_status_shows_the_links(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=declaring(_GITHUB_TOKEN))
        client = hub_client(hub)
        await prepared(client)
        await set_hub_secret(client, "ANTHROPIC_API_KEY", "sk-hub")
        await set_hub_secret(client, "WORK_GITHUB_TOKEN", "ghp-work")

        status = await _status(
            client,
            InstanceCreateCommand(
                manifest_id="coder",
                model="anthropic:claude-opus-5-5",
                hub_secrets={"api_key": "ANTHROPIC_API_KEY", "GITHUB_TOKEN": "WORK_GITHUB_TOKEN"},
            ),
        )

        assert [
            (secret.name, secret.is_set, secret.hub_secret) for secret in status.setup.secrets
        ] == [("api_key", True, "ANTHROPIC_API_KEY"), ("GITHUB_TOKEN", True, "WORK_GITHUB_TOKEN")]
        environment = instance_environment(hub, status.instance_id)
        assert environment["ANTHROPIC_API_KEY"] == "sk-hub"
        assert environment["GITHUB_TOKEN"] == "ghp-work"

    asyncio.run(scenario())


def test_a_typed_field_links_no_hub_secret(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=declaring(_GITHUB_TOKEN))
        client = hub_client(hub)
        await prepared(client)
        await set_hub_secret(client, "GITHUB_TOKEN", "ghp-hub")

        status = await _status(
            client,
            InstanceCreateCommand(
                manifest_id="coder", model="openai:gpt-5", secrets={"GITHUB_TOKEN": "ghp-typed"}
            ),
        )

        assert [(secret.name, secret.hub_secret) for secret in status.setup.secrets] == [
            ("api_key", None),
            ("GITHUB_TOKEN", None),
        ]
        assert instance_environment(hub, status.instance_id)["GITHUB_TOKEN"] == "ghp-typed"

    asyncio.run(scenario())


def test_save_to_the_hub_stores_the_typed_value_and_links_the_field_to_it(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=declaring(_GITHUB_TOKEN))
        client = hub_client(hub)
        await prepared(client)

        status = await _status(
            client,
            InstanceCreateCommand(
                manifest_id="coder",
                model="openai:gpt-5",
                secrets={"api_key": "sk-typed", "GITHUB_TOKEN": "ghp-typed"},
                save_to_hub={"api_key": "OPENAI_API_KEY", "GITHUB_TOKEN": "PERSONAL_TOKEN"},
            ),
        )

        assert hub.secrets.values() == {
            "OPENAI_API_KEY": "sk-typed",
            "PERSONAL_TOKEN": "ghp-typed",
        }
        assert [(secret.name, secret.hub_secret) for secret in status.setup.secrets] == [
            ("api_key", "OPENAI_API_KEY"),
            ("GITHUB_TOKEN", "PERSONAL_TOKEN"),
        ]
        environment = instance_environment(hub, status.instance_id)
        assert environment["OPENAI_API_KEY"] == "sk-typed"
        assert environment["GITHUB_TOKEN"] == "ghp-typed"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("secrets", "hub_secrets", "save_to_hub", "fields"),
    [
        (
            {"api_key": "sk-typed"},
            {"GITHUB_TOKEN": "MISSING"},
            {"api_key": "OPENAI_API_KEY"},
            {"GITHUB_TOKEN": 'The hub keeps no secret named "MISSING".'},
        ),
        (
            {"GITHUB_TOKEN": "ghp-typed"},
            {"GITHUB_TOKEN": "GITHUB_TOKEN"},
            {},
            {"GITHUB_TOKEN": "Type a value or take a hub secret, not both."},
        ),
        (
            {"GITHUB_TOKEN": "  "},
            {},
            {"GITHUB_TOKEN": "GITHUB_TOKEN"},
            {"GITHUB_TOKEN": "Type a value to save it to the hub."},
        ),
        (
            {"api_key": "sk-typed"},
            {"GITHUB_TOKEN": "GITHUB_TOKEN"},
            {"api_key": "GITHUB_TOKEN"},
            {"api_key": 'The hub keeps a secret named "GITHUB_TOKEN" already.'},
        ),
        (
            {"api_key": "sk-typed", "GITHUB_TOKEN": "ghp-typed"},
            {},
            {"api_key": "SHARED", "GITHUB_TOKEN": "SHARED"},
            {
                "api_key": 'Another field saves to "SHARED" too.',
                "GITHUB_TOKEN": 'Another field saves to "SHARED" too.',
            },
        ),
    ],
    ids=[
        "unknown-hub-secret",
        "typed-and-linked",
        "nothing-to-save",
        "saved-under-a-kept-name",
        "two-saves-under-one-name",
    ],
)
def test_a_link_or_save_that_does_not_fit_is_refused_on_its_field_and_creates_nothing(
    tmp_path, secrets, hub_secrets, save_to_hub, fields
):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=declaring(_GITHUB_TOKEN))
        client = hub_client(hub)
        await prepared(client)
        await set_hub_secret(client, "GITHUB_TOKEN", "ghp-hub")

        refused = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="coder",
                model="openai:gpt-5",
                secrets=secrets,
                hub_secrets=hub_secrets,
                save_to_hub=save_to_hub,
            ),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_SETUP
        assert refused.fields == fields
        assert runtime.created == []
        assert list(hub.instances_directory.iterdir()) == []
        assert hub.secrets.values() == {"GITHUB_TOKEN": "ghp-hub"}

    asyncio.run(scenario())


def test_a_linked_value_of_the_wrong_type_is_refused_on_its_field(tmp_path):
    async def scenario() -> None:
        email = SetupField(
            name="GIT_USER_EMAIL",
            label="Commit email",
            description="Commit email.",
            kind=SetupFieldKind.SECRET,
            type=SetupFieldType.EMAIL,
            required=True,
        )
        hub = hub_at(tmp_path / "hub", images=declaring(email))
        client = hub_client(hub)
        await prepared(client)
        await set_hub_secret(client, "GIT_USER_EMAIL", _SENTINEL)

        refused = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="coder",
                model="openai:gpt-5",
                hub_secrets={"GIT_USER_EMAIL": "GIT_USER_EMAIL"},
            ),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.fields == {
            "GIT_USER_EMAIL": "Commit email is not an email address, like someone@example.com."
        }
        assert _SENTINEL not in refused.model_dump_json()

    asyncio.run(scenario())


def test_no_linked_or_saved_value_reaches_sqlite_or_the_operation_record(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=declaring(_GITHUB_TOKEN))
        client = hub_client(hub)
        await prepared(client)
        await set_hub_secret(client, "OPENAI_API_KEY", f"{_SENTINEL}-linked")

        accepted = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="coder",
                model="openai:gpt-5",
                secrets={"GITHUB_TOKEN": f"{_SENTINEL}-saved"},
                hub_secrets={"api_key": "OPENAI_API_KEY"},
                save_to_hub={"GITHUB_TOKEN": "GITHUB_TOKEN"},
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)
        status = await client.call(
            INSTANCE_STATUS, InstanceStatusCommand(instance_id=accepted.instance_id)
        )

        assert outcome.state is OperationState.SUCCEEDED
        assert _SENTINEL not in outcome.model_dump_json()
        assert _SENTINEL not in status.model_dump_json()
        for registry in hub.directory.glob("registry.sqlite*"):
            assert _SENTINEL not in registry.read_bytes().decode("utf-8", errors="ignore")
        assert _SENTINEL not in "".join(os.environ.values())

    asyncio.run(scenario())
