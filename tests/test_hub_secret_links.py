"""Linking an instance's secret fields to hub secrets: at creation, where a typed value can be
saved as a new one, and afterwards from its secrets."""

import asyncio
import os

import pytest

from kinby.cli.client import ContractClient
from kinby.contracts import (
    INSTANCE_CREATE,
    INSTANCE_SECRETS_SET,
    INSTANCE_START,
    INSTANCE_STATUS,
    ErrorCode,
    ErrorEnvelope,
    InstanceCreateCommand,
    InstanceSecretsSetCommand,
    InstanceSetup,
    InstanceStartCommand,
    InstanceStatusCommand,
    InstanceStatusResult,
    LifecycleOperationResult,
    OperationGetResult,
    OperationState,
    RecreateReason,
    SecretSetup,
    SetupField,
    SetupFieldKind,
    SetupFieldType,
)
from tests.test_hub import (
    FakeControl,
    FakeImages,
    FakeRuntime,
    created_instance,
    finished_operation,
    hub_at,
    hub_client,
    instance_environment,
    prepared,
    started_instance,
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
        ] == [
            ("api_key", True, "ANTHROPIC_API_KEY"),
            ("GITHUB_TOKEN", True, "WORK_GITHUB_TOKEN"),
            ("BRAVE_API_KEY", False, None),
        ]
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
            ("BRAVE_API_KEY", None),
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
            ("BRAVE_API_KEY", None),
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


async def _secrets_set(
    client: ContractClient, command: InstanceSecretsSetCommand
) -> OperationGetResult:
    accepted = await client.call(INSTANCE_SECRETS_SET, command)
    assert isinstance(accepted, LifecycleOperationResult), accepted
    return await finished_operation(client, accepted)


async def _linked(client: ContractClient, instance_id) -> list[tuple[str, bool, str | None]]:
    status = await client.call(INSTANCE_STATUS, InstanceStatusCommand(instance_id=instance_id))
    assert isinstance(status, InstanceStatusResult)
    return [(secret.name, secret.is_set, secret.hub_secret) for secret in status.setup.secrets]


def test_linking_a_field_of_an_existing_instance_writes_the_value_and_status_shows_it(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=declaring(_GITHUB_TOKEN))
        client = hub_client(hub)
        created = await created_instance(client, secrets={"GITHUB_TOKEN": "ghp-typed"})
        await set_hub_secret(client, "OPENAI_API_KEY", "sk-hub")
        await set_hub_secret(client, "WORK_GITHUB_TOKEN", "ghp-work")

        outcome = await _secrets_set(
            client,
            InstanceSecretsSetCommand(
                instance_id=created.instance_id,
                hub_secrets={"api_key": "OPENAI_API_KEY", "GITHUB_TOKEN": "WORK_GITHUB_TOKEN"},
            ),
        )

        assert outcome.state is OperationState.SUCCEEDED
        assert await _linked(client, created.instance_id) == [
            ("api_key", True, "OPENAI_API_KEY"),
            ("GITHUB_TOKEN", True, "WORK_GITHUB_TOKEN"),
            ("BRAVE_API_KEY", False, None),
        ]
        environment = instance_environment(hub, created.instance_id)
        assert environment["OPENAI_API_KEY"] == "sk-hub"
        assert environment["GITHUB_TOKEN"] == "ghp-work"

    asyncio.run(scenario())


def test_a_typed_value_for_a_linked_field_replaces_it_and_unlinks_the_field(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=declaring(_GITHUB_TOKEN))
        client = hub_client(hub)
        await prepared(client)
        await set_hub_secret(client, "OPENAI_API_KEY", "sk-hub")
        await set_hub_secret(client, "GITHUB_TOKEN", "ghp-hub")
        status = await _status(
            client,
            InstanceCreateCommand(
                manifest_id="coder",
                model="openai:gpt-5",
                hub_secrets={"api_key": "OPENAI_API_KEY", "GITHUB_TOKEN": "GITHUB_TOKEN"},
            ),
        )

        outcome = await _secrets_set(
            client,
            InstanceSecretsSetCommand(
                instance_id=status.instance_id,
                secrets={"api_key": "sk-typed", "GITHUB_TOKEN": "ghp-typed"},
            ),
        )

        assert outcome.state is OperationState.SUCCEEDED
        assert await _linked(client, status.instance_id) == [
            ("api_key", True, None),
            ("GITHUB_TOKEN", True, None),
            ("BRAVE_API_KEY", False, None),
        ]
        environment = instance_environment(hub, status.instance_id)
        assert environment["OPENAI_API_KEY"] == "sk-typed"
        assert environment["GITHUB_TOKEN"] == "ghp-typed"

    asyncio.run(scenario())


def test_linking_a_field_of_a_running_instance_asks_for_a_recreation(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=FakeImages(), control=FakeControl())
        client = hub_client(hub)
        created = await started_instance(client, hub, secrets={"PROVIDER_TOKEN": "first"})
        await set_hub_secret(client, "PROVIDER_TOKEN", "from-the-hub")

        await _secrets_set(
            client,
            InstanceSecretsSetCommand(
                instance_id=created.instance_id, hub_secrets={"PROVIDER_TOKEN": "PROVIDER_TOKEN"}
            ),
        )
        status = await client.call(
            INSTANCE_STATUS, InstanceStatusCommand(instance_id=created.instance_id)
        )

        assert isinstance(status, InstanceStatusResult)
        assert status.recreate_reasons == [RecreateReason.SECRETS]

    asyncio.run(scenario())


def test_a_stopped_instance_applies_a_linked_value_when_it_starts(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        client = hub_client(hub)
        created = await created_instance(client, secrets={"PROVIDER_TOKEN": "first"})
        await set_hub_secret(client, "PROVIDER_TOKEN", "from-the-hub")
        await _secrets_set(
            client,
            InstanceSecretsSetCommand(
                instance_id=created.instance_id, hub_secrets={"PROVIDER_TOKEN": "PROVIDER_TOKEN"}
            ),
        )

        accepted = await client.call(
            INSTANCE_START, InstanceStartCommand(instance_id=created.instance_id)
        )
        assert isinstance(accepted, LifecycleOperationResult)

        assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED
        assert [spec.env["PROVIDER_TOKEN"] for spec in runtime.created] == [
            "first",
            "from-the-hub",
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("secrets", "hub_secrets", "detail"),
    [
        (
            {"api_key": "sk-typed"},
            {"GITHUB_TOKEN": "MISSING"},
            'The hub keeps no secret named "MISSING".',
        ),
        (
            {"GITHUB_TOKEN": "ghp-typed"},
            {"GITHUB_TOKEN": "GITHUB_TOKEN"},
            "GITHUB_TOKEN takes a typed value or a hub secret, not both.",
        ),
        (
            {"OPENAI_API_KEY": "sk-typed"},
            {"api_key": "OPENAI_API_KEY"},
            "OPENAI_API_KEY takes a typed value or a hub secret, not both.",
        ),
    ],
    ids=["unknown-hub-secret", "typed-and-linked", "api-key-typed-under-its-variable-and-linked"],
)
def test_a_link_that_does_not_fit_fails_the_operation_and_changes_nothing(
    tmp_path, secrets, hub_secrets, detail
):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=declaring(_GITHUB_TOKEN))
        client = hub_client(hub)
        await prepared(client)
        await set_hub_secret(client, "GITHUB_TOKEN", f"{_SENTINEL}-hub")
        await set_hub_secret(client, "OPENAI_API_KEY", "sk-hub")
        status = await _status(
            client,
            InstanceCreateCommand(
                manifest_id="coder",
                model="openai:gpt-5",
                secrets={"GITHUB_TOKEN": "ghp-first"},
                hub_secrets={"api_key": "OPENAI_API_KEY"},
            ),
        )
        before = instance_environment(hub, status.instance_id)

        outcome = await _secrets_set(
            client,
            InstanceSecretsSetCommand(
                instance_id=status.instance_id, secrets=secrets, hub_secrets=hub_secrets
            ),
        )

        assert outcome.state is OperationState.FAILED
        assert outcome.detail == detail
        assert _SENTINEL not in outcome.model_dump_json()
        assert instance_environment(hub, status.instance_id) == before
        assert await _linked(client, status.instance_id) == [
            ("api_key", True, "OPENAI_API_KEY"),
            ("GITHUB_TOKEN", True, None),
            ("BRAVE_API_KEY", False, None),
        ]

    asyncio.run(scenario())


async def _setup(client: ContractClient, instance_id) -> InstanceSetup:
    status = await client.call(INSTANCE_STATUS, InstanceStatusCommand(instance_id=instance_id))
    assert isinstance(status, InstanceStatusResult)
    return status.setup


def test_web_search_waits_as_pending_setup_until_a_hub_secret_fills_its_key(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=FakeImages())
        client = hub_client(hub)
        created = await created_instance(client)

        before = await _setup(client, created.instance_id)
        await set_hub_secret(client, "BRAVE", "brave-key")
        outcome = await _secrets_set(
            client,
            InstanceSecretsSetCommand(
                instance_id=created.instance_id, hub_secrets={"BRAVE_API_KEY": "BRAVE"}
            ),
        )
        after = await _setup(client, created.instance_id)

        assert before.pending
        assert before.secrets[-1] == SecretSetup(
            name="BRAVE_API_KEY",
            variable="BRAVE_API_KEY",
            label="Web search key",
            required=True,
            is_set=False,
        )
        assert outcome.state is OperationState.SUCCEEDED
        assert not after.pending
        assert (after.secrets[-1].is_set, after.secrets[-1].hub_secret) == (True, "BRAVE")
        assert instance_environment(hub, created.instance_id)["BRAVE_API_KEY"] == "brave-key"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("tools", "variable"),
    [
        ('search = "tavily"', "TAVILY_API_KEY"),
        ('search = "searxng"', "SEARXNG_URL"),
        ('off = ["web_search"]', None),
        ("defaults = false", None),
    ],
)
def test_the_search_key_pending_setup_follows_the_manifest(tmp_path, tools, variable):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=FakeImages())
        client = hub_client(hub)
        created = await created_instance(client)
        manifest = hub.instances_directory / str(created.instance_id) / "kinby.toml"
        manifest.write_text(
            f"{manifest.read_text(encoding='utf-8')}\n[tools]\n{tools}\n", encoding="utf-8"
        )

        setup = await _setup(client, created.instance_id)

        assert [secret.variable for secret in setup.secrets] == [
            "OPENAI_API_KEY",
            *([variable] if variable else []),
        ]
        assert setup.pending is (variable is not None)

    asyncio.run(scenario())


def test_a_template_declaring_the_search_key_shows_it_once(tmp_path):
    async def scenario() -> None:
        brave = _GITHUB_TOKEN.model_copy(update={"name": "BRAVE_API_KEY", "label": "Brave key"})
        hub = hub_at(tmp_path / "hub", images=declaring(brave))
        client = hub_client(hub)
        created = await created_instance(client, secrets={"BRAVE_API_KEY": "brave-key"})

        setup = await _setup(client, created.instance_id)

        assert [(secret.label, secret.is_set) for secret in setup.secrets] == [
            ("API key", True),
            ("Brave key", True),
        ]

    asyncio.run(scenario())
