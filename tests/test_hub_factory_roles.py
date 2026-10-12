"""Creating an instance that picks a factory role, beside the others that hold it."""

import asyncio
from uuid import UUID

from kinby.cli.client import ContractClient
from kinby.contracts import (
    FACTORY_REMOVE,
    INSTANCE_CREATE,
    INSTANCE_LIST,
    INSTANCE_REMOVE,
    INSTANCE_RESTORE,
    INSTANCE_STATUS,
    ErrorCode,
    ErrorEnvelope,
    FactoryRemoveCommand,
    FactoryRole,
    InstanceCreateCommand,
    InstanceListCommand,
    InstanceListResult,
    InstanceRemoveCommand,
    InstanceRestoreCommand,
    InstanceStatusCommand,
    InstanceStatusResult,
    LifecycleOperationResult,
    LoginState,
    OperationState,
)
from kinby.hub import ImageSelection
from kinby.instance import inspect_instance
from kinby.instance.config_changes import file_hash
from tests.test_factory_install import FACTORY, FILES, WITH_SETUP, install_hub, listed
from tests.test_hub import (
    FakeImages,
    FakeRuntime,
    finished_operation,
    hub_client,
    instance_environment,
)
from tests.test_hub_factories import HOOKS, TOOLS

CODER = FactoryRole(factory="tickets", name="coder")
CODER_SECRETS = {"api_key": "sk-test", "GITHUB_TOKEN": "ghp-test"}


def create_command(
    role: FactoryRole | None = CODER, name: str | None = None
) -> InstanceCreateCommand:
    return InstanceCreateCommand(
        manifest_id=name, factory_role=role, model="", secrets=CODER_SECRETS
    )


async def created(
    client: ContractClient, role: FactoryRole = CODER, name: str | None = None
) -> UUID:
    """Create an instance that picks *role*, and wait for it to be published stopped."""
    accepted = await client.call(INSTANCE_CREATE, create_command(role, name))
    assert isinstance(accepted, LifecycleOperationResult), accepted
    assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED
    return accepted.instance_id


def test_an_instance_that_picks_a_role_is_written_from_the_roles_template(tmp_path):
    images = FakeImages()
    hub = install_hub(tmp_path, WITH_SETUP, images=images)
    client = hub_client(hub)

    async def scenario() -> None:
        coder = await created(client)
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        status = await client.call(INSTANCE_STATUS, InstanceStatusCommand(instance_id=coder))

        directory = hub.instances_directory / str(coder)
        assert isinstance(listed, InstanceListResult)
        [summary] = listed.instances
        assert (summary.manifest_id, summary.factory_role) == ("tickets-coder", CODER)
        assert (directory / "SYSTEM.md").read_text() == "You implement issues.\n"
        assert (directory / "tools" / "github.py").read_text() == TOOLS
        assert (directory / "hooks" / "record.py").read_text() == HOOKS
        manifest = inspect_instance(directory).manifest
        assert manifest.models.main == "anthropic:claude-sonnet-5"
        assert manifest.budgets.steps == 40
        assert instance_environment(hub, coder)["GITHUB_TOKEN"] == "ghp-test"
        assert isinstance(status, InstanceStatusResult)
        assert [(login.id, login.state) for login in status.setup.logins] == [
            ("claude", LoginState.PENDING)
        ]
        assert images.selections == [ImageSelection("HEAD", recipe="coder")]
        record = hub.registry.instance(coder)
        assert record is not None
        assert record.factory is not None
        assert record.factory.baseline == {
            path: file_hash((directory / path).read_bytes())
            for path in (
                "SYSTEM.md",
                "hooks/record.py",
                "routines/scan/ROUTINE.md",
                "tools/github.py",
            )
        }

    asyncio.run(scenario())


def test_a_create_with_a_role_is_checked_whole_before_anything_is_created(tmp_path):
    images = FakeImages()
    runtime = FakeRuntime()
    hub = install_hub(tmp_path, WITH_SETUP, images=images, runtime=runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        missing = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(factory_role=CODER, model="", secrets={"api_key": "sk-test"}),
        )
        unknown = await client.call(
            INSTANCE_CREATE, create_command(FactoryRole(factory="tickets", name="tester"))
        )

        assert isinstance(missing, ErrorEnvelope)
        assert missing.code is ErrorCode.INVALID_SETUP
        assert missing.fields == {"GITHUB_TOKEN": "GitHub token is required."}
        assert isinstance(unknown, ErrorEnvelope)
        assert unknown.fields == {"factory_role": 'Factory "tickets" has no role "tester".'}
        assert hub.registry.managed_instances() == []
        assert images.selections == []
        assert runtime.created == []

    asyncio.run(scenario())


def test_a_create_whose_factory_fails_its_check_creates_nothing(tmp_path):
    runtime = FakeRuntime()
    broken = FILES | {"factory.yaml": FACTORY.replace("hook: read_verdict", "hook: read_review")}
    hub = install_hub(tmp_path, broken, runtime=runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        refused = await client.call(INSTANCE_CREATE, create_command())

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert 'names hook "read_review"' in refused.message
        assert hub.registry.managed_instances() == []
        assert runtime.created == []

    asyncio.run(scenario())


def test_a_create_whose_name_is_taken_fails_and_says_so(tmp_path):
    hub = install_hub(tmp_path, WITH_SETUP)
    client = hub_client(hub)

    async def scenario() -> None:
        first = await created(client)
        refused = await client.call(INSTANCE_CREATE, create_command())

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_SETUP
        assert refused.fields == {
            "manifest_id": 'An instance named "tickets-coder" exists already.'
        }
        assert [record.instance_id for record in hub.registry.managed_instances()] == [first]

    asyncio.run(scenario())


def test_a_vanilla_instance_needs_a_name(tmp_path):
    hub = install_hub(tmp_path)
    client = hub_client(hub)

    async def scenario() -> None:
        refused = await client.call(
            INSTANCE_CREATE, InstanceCreateCommand(model="openai:gpt-5", secrets=CODER_SECRETS)
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.fields == {"manifest_id": "Name the instance, or pick a factory role."}

    asyncio.run(scenario())


def test_two_instances_hold_one_role_each_with_its_own_name_and_directory(tmp_path):
    hub = install_hub(tmp_path, WITH_SETUP)
    client = hub_client(hub)

    async def scenario() -> None:
        first = await created(client)
        second = await created(client, name="billing-coder")
        found = await client.call(INSTANCE_LIST, InstanceListCommand())

        assert isinstance(found, InstanceListResult)
        assert {
            summary.instance_id: (summary.manifest_id, summary.factory_role)
            for summary in found.instances
        } == {first: ("tickets-coder", CODER), second: ("billing-coder", CODER)}
        for coder in (first, second):
            directory = hub.instances_directory / str(coder)
            assert (directory / "SYSTEM.md").read_text() == "You implement issues.\n"

    asyncio.run(scenario())


def test_a_factory_with_instances_is_not_removed_and_its_instances_are_named(tmp_path):
    hub = install_hub(tmp_path, WITH_SETUP)
    client = hub_client(hub)

    async def scenario() -> None:
        await created(client)
        await created(client, name="billing-coder")

        refused = await client.call(FACTORY_REMOVE, FactoryRemoveCommand(name="tickets"))

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert refused.message == (
            'Factory "tickets" has instances tickets-coder, billing-coder. '
            "Remove them before removing the factory."
        )
        assert len(hub.registry.managed_instances()) == 2

    asyncio.run(scenario())


def test_removing_one_instance_of_a_role_leaves_the_others(tmp_path):
    hub = install_hub(tmp_path, WITH_SETUP)
    client = hub_client(hub)

    async def scenario() -> None:
        first = await created(client)
        second = await created(client, name="billing-coder")

        removal = await client.call(INSTANCE_REMOVE, InstanceRemoveCommand(instance_id=first))
        assert isinstance(removal, LifecycleOperationResult)
        assert (await finished_operation(client, removal)).state is OperationState.SUCCEEDED

        assert await listed(client) == {second}
        assert await listed(client, removed=True) == {first}

    asyncio.run(scenario())


def test_a_removed_instance_of_a_role_is_restored_beside_another_that_holds_it(tmp_path):
    hub = install_hub(tmp_path, WITH_SETUP)
    client = hub_client(hub)

    async def scenario() -> None:
        first = await created(client)
        removal = await client.call(INSTANCE_REMOVE, InstanceRemoveCommand(instance_id=first))
        assert isinstance(removal, LifecycleOperationResult)
        assert (await finished_operation(client, removal)).state is OperationState.SUCCEEDED
        second = await created(client, name="billing-coder")

        restoration = await client.call(INSTANCE_RESTORE, InstanceRestoreCommand(instance_id=first))
        assert isinstance(restoration, LifecycleOperationResult), restoration
        assert (await finished_operation(client, restoration)).state is OperationState.SUCCEEDED

        assert await listed(client) == {first, second}

    asyncio.run(scenario())
