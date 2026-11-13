"""A workbench's secrets behave as an instance's do: it takes hub secrets by link, and a replaced
secret reaches its container through a recreation."""

import asyncio
from uuid import UUID

from pydantic import SecretStr

from kinby.cli.client import ContractClient
from kinby.contracts import (
    HUB_SECRET_LIST,
    HUB_SECRET_SET,
    WORKBENCH_CREATE,
    WORKBENCH_RECREATE,
    WORKBENCH_SECRETS_SET,
    WORKBENCH_START,
    ErrorCode,
    ErrorEnvelope,
    HubSecretListCommand,
    HubSecretListResult,
    HubSecretSetCommand,
    HubSecretSetResult,
    OperationState,
    RecreateReason,
    WorkbenchCreateCommand,
    WorkbenchRecreateCommand,
    WorkbenchSecretsSetCommand,
    WorkbenchStartCommand,
)
from kinby.hub import Hub
from tests.test_hub import FakeControl, FakeRuntime, finished_operation, hub_at, hub_client
from tests.test_hub_secret_methods import set_hub_secret
from tests.test_hub_workbenches import created, finished, got

LINKED = WorkbenchCreateCommand(
    name="cec-backend", recipe="coder", hub_secrets={"GH_TOKEN": "github"}
)


def _hub(tmp_path, runtime: FakeRuntime) -> Hub:
    return hub_at(tmp_path / "hub", runtime=runtime, control=FakeControl())


async def _linked(client: ContractClient, runtime: FakeRuntime, *, running: bool) -> UUID:
    """Create the workbench whose GH_TOKEN takes the hub secret "github", and start it."""
    await set_hub_secret(client, "github", "ghp-first")
    workbench_id = (await created(client, LINKED)).workbench_id
    if running:
        start = await client.call(WORKBENCH_START, WorkbenchStartCommand(workbench_id=workbench_id))
        assert (await finished(client, start)).state is OperationState.SUCCEEDED
        runtime.addresses[str(workbench_id)] = f"http://kinby-{workbench_id}:8787"
    return workbench_id


async def _replaced(
    client: ContractClient, hub: Hub, value: str, recreate: list[UUID] | None = None
) -> None:
    accepted = await client.call(
        HUB_SECRET_SET, HubSecretSetCommand(name="github", value=value, recreate=recreate or [])
    )
    assert isinstance(accepted, HubSecretSetResult), accepted
    assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED
    # The recreations it queued run as operations of their own.
    await asyncio.gather(*hub._tasks)


def test_a_workbench_links_a_hub_secret_and_its_container_takes_the_value(tmp_path):
    runtime = FakeRuntime()
    hub = _hub(tmp_path, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        workbench_id = await _linked(client, runtime, running=True)
        workbench = await got(client, workbench_id)
        listed = await client.call(HUB_SECRET_LIST, HubSecretListCommand())

        assert (workbench.secrets, workbench.hub_secrets) == (["GH_TOKEN"], {"GH_TOKEN": "github"})
        assert "ghp-first" not in workbench.model_dump_json()
        [container] = runtime.created
        assert container.env["GH_TOKEN"] == "ghp-first"
        assert runtime.started == [str(workbench_id)]
        assert isinstance(listed, HubSecretListResult)
        [secret] = listed.secrets
        assert [(link.manifest_id, link.field) for link in secret.links] == [
            ("cec-backend", "GH_TOKEN")
        ]

    asyncio.run(scenario())


def test_a_link_to_a_hub_secret_the_hub_does_not_keep_creates_nothing(tmp_path):
    runtime = FakeRuntime()
    client = hub_client(_hub(tmp_path, runtime))

    async def scenario() -> None:
        await set_hub_secret(client, "github", "ghp-first")
        refused = await client.call(
            WORKBENCH_CREATE,
            WorkbenchCreateCommand(
                name="cec-backend",
                secrets={"GH_TOKEN": SecretStr("ghp-typed")},
                hub_secrets={"GH_TOKEN": "github", "NPM_TOKEN": "npm"},
            ),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_SETUP
        assert refused.fields == {
            "GH_TOKEN": "Type a value or take a hub secret, not both.",
            "NPM_TOKEN": 'The hub keeps no secret named "npm".',
        }
        assert runtime.created == []

    asyncio.run(scenario())


def test_a_replaced_hub_secret_asks_for_a_recreation_that_delivers_it(tmp_path):
    runtime = FakeRuntime()
    hub = _hub(tmp_path, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        workbench_id = await _linked(client, runtime, running=True)

        await _replaced(client, hub, "ghp-second")
        pending = await got(client, workbench_id)
        recreate = await client.call(
            WORKBENCH_RECREATE, WorkbenchRecreateCommand(workbench_id=workbench_id)
        )
        recreation = await finished(client, recreate)
        applied = await got(client, workbench_id)

        assert pending.recreate_reasons == [RecreateReason.SECRETS]
        assert recreation.state is OperationState.SUCCEEDED, recreation.detail
        assert [spec.env["GH_TOKEN"] for spec in runtime.created] == ["ghp-first", "ghp-second"]
        assert runtime.created[1].command == runtime.created[0].command
        assert applied.recreate_reasons == []

    asyncio.run(scenario())


def test_a_hub_secret_replacement_recreates_a_workbench_it_names(tmp_path):
    runtime = FakeRuntime()
    hub = _hub(tmp_path, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        workbench_id = await _linked(client, runtime, running=True)

        await _replaced(client, hub, "ghp-second", recreate=[workbench_id])

        assert [spec.env["GH_TOKEN"] for spec in runtime.created] == ["ghp-first", "ghp-second"]
        assert (await got(client, workbench_id)).recreate_reasons == []

    asyncio.run(scenario())


def test_a_stopped_workbench_applies_a_replaced_hub_secret_when_it_starts(tmp_path):
    runtime = FakeRuntime()
    hub = _hub(tmp_path, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        workbench_id = await _linked(client, runtime, running=False)
        await _replaced(client, hub, "ghp-second")

        start = await client.call(WORKBENCH_START, WorkbenchStartCommand(workbench_id=workbench_id))

        assert (await finished(client, start)).state is OperationState.SUCCEEDED
        assert [spec.env["GH_TOKEN"] for spec in runtime.created] == ["ghp-first", "ghp-second"]

    asyncio.run(scenario())


def test_a_replaced_workbench_secret_reaches_its_container_through_a_recreation(tmp_path):
    runtime = FakeRuntime()
    hub = _hub(tmp_path, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        workbench_id = await _linked(client, runtime, running=True)
        await set_hub_secret(client, "npm", "npm-first")

        replace = await client.call(
            WORKBENCH_SECRETS_SET,
            WorkbenchSecretsSetCommand(
                workbench_id=workbench_id,
                secrets={"GH_TOKEN": SecretStr("ghp-typed")},
                hub_secrets={"NPM_TOKEN": "npm"},
            ),
        )
        replacement = await finished(client, replace)
        pending = await got(client, workbench_id)
        recreate = await client.call(
            WORKBENCH_RECREATE, WorkbenchRecreateCommand(workbench_id=workbench_id)
        )
        await finished(client, recreate)

        assert replacement.state is OperationState.SUCCEEDED, replacement.detail
        assert "ghp-typed" not in replacement.model_dump_json()
        assert pending.secrets == ["GH_TOKEN", "NPM_TOKEN"]
        assert pending.hub_secrets == {"NPM_TOKEN": "npm"}
        assert pending.recreate_reasons == [RecreateReason.SECRETS]
        assert (runtime.created[-1].env["GH_TOKEN"], runtime.created[-1].env["NPM_TOKEN"]) == (
            "ghp-typed",
            "npm-first",
        )

    asyncio.run(scenario())
