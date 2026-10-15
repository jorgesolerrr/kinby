"""Replacing or deleting a hub secret reaches the instances linked to it (ADR 0095)."""

import asyncio
import os
from pathlib import Path
from uuid import UUID

from kinby.cli.client import ContractClient
from kinby.contracts import (
    HUB_SECRET_DELETE,
    HUB_SECRET_LIST,
    HUB_SECRET_SET,
    INSTANCE_CREATE,
    INSTANCE_START,
    INSTANCE_STATUS,
    HubSecretDeleteCommand,
    HubSecretDeleteResult,
    HubSecretListCommand,
    HubSecretListResult,
    HubSecretSetCommand,
    HubSecretSetResult,
    InstanceCreateCommand,
    InstanceStartCommand,
    InstanceStatusCommand,
    InstanceStatusResult,
    LifecycleOperationResult,
    OperationGetResult,
    OperationState,
    RecreateReason,
    SetupField,
    SetupFieldKind,
    SetupFieldType,
)
from kinby.hub import Hub
from tests.test_hub import (
    FakeControl,
    FakeRuntime,
    finished_operation,
    hub_at,
    hub_client,
    instance_environment,
    prepared,
)
from tests.test_hub_create import declaring
from tests.test_hub_deletion import deleted, previewed
from tests.test_hub_removal import removed, restored
from tests.test_hub_secret_methods import set_hub_secret

_SENTINEL = "sentinel-replaced-hub-secret"

_GITHUB_TOKEN = SetupField(
    name="GITHUB_TOKEN",
    label="GitHub token",
    description="GitHub token.",
    kind=SetupFieldKind.SECRET,
    type=SetupFieldType.TEXT,
    required=True,
)


def _hub(tmp_path, runtime: FakeRuntime | None = None) -> Hub:
    return hub_at(
        tmp_path / "hub", runtime=runtime, images=declaring(_GITHUB_TOKEN), control=FakeControl()
    )


async def _linked(
    client: ContractClient,
    hub: Hub,
    name: str,
    hub_secrets: dict[str, str] | None = None,
    *,
    running: bool = False,
) -> LifecycleOperationResult:
    """Create an instance whose fields take the hub secrets named, by default its GitHub token
    the one of the same name. The API key is typed unless it takes one."""
    hub_secrets = hub_secrets or {"GITHUB_TOKEN": "GITHUB_TOKEN"}
    await prepared(client)
    accepted = await client.call(
        INSTANCE_CREATE,
        InstanceCreateCommand(
            manifest_id=name,
            model="openai:gpt-5",
            secrets={} if "api_key" in hub_secrets else {"api_key": "sk-typed"},
            hub_secrets=hub_secrets,
        ),
    )
    assert isinstance(accepted, LifecycleOperationResult), accepted
    assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED
    if running:
        started = await client.call(
            INSTANCE_START, InstanceStartCommand(instance_id=accepted.instance_id)
        )
        assert isinstance(started, LifecycleOperationResult)
        assert (await finished_operation(client, started)).state is OperationState.SUCCEEDED
        runtime = hub._runtime
        assert isinstance(runtime, FakeRuntime)
        runtime.addresses[str(accepted.instance_id)] = f"http://kinby-{accepted.instance_id}:8787"
    return accepted


async def _replaced(
    client: ContractClient, hub: Hub, value: str, recreate: list[UUID] | None = None
) -> OperationGetResult:
    accepted = await client.call(
        HUB_SECRET_SET,
        HubSecretSetCommand(name="GITHUB_TOKEN", value=value, recreate=recreate or []),
    )
    assert isinstance(accepted, HubSecretSetResult), accepted
    outcome = await finished_operation(client, accepted)
    # The recreations it queued run as operations of their own.
    await asyncio.gather(*hub._tasks)
    return outcome


async def _status(client: ContractClient, instance_id: UUID) -> InstanceStatusResult:
    status = await client.call(INSTANCE_STATUS, InstanceStatusCommand(instance_id=instance_id))
    assert isinstance(status, InstanceStatusResult)
    return status


async def _github_link(client: ContractClient, instance_id: UUID) -> str | None:
    secrets = (await _status(client, instance_id)).setup.secrets
    return next(secret.hub_secret for secret in secrets if secret.name == "GITHUB_TOKEN")


async def _links(client: ContractClient) -> list[tuple[str, list[tuple[str, str]]]]:
    listed = await client.call(HUB_SECRET_LIST, HubSecretListCommand())
    assert isinstance(listed, HubSecretListResult)
    return [
        (secret.name, [(link.manifest_id, link.field) for link in secret.links])
        for secret in listed.secrets
    ]


def test_a_replacement_rewrites_each_linked_value_and_a_running_instance_asks_to_recreate(
    tmp_path,
):
    async def scenario() -> None:
        hub = _hub(tmp_path)
        client = hub_client(hub)
        await set_hub_secret(client, "GITHUB_TOKEN", "ghp-first")
        running = await _linked(client, hub, "runner", running=True)
        stopped = await _linked(client, hub, "idle")

        outcome = await _replaced(client, hub, _SENTINEL)

        assert outcome.state is OperationState.SUCCEEDED
        assert _SENTINEL not in outcome.model_dump_json()
        for instance in (running, stopped):
            assert instance_environment(hub, instance.instance_id)["GITHUB_TOKEN"] == _SENTINEL
            assert await _github_link(client, instance.instance_id) == "GITHUB_TOKEN"
        assert (await _status(client, running.instance_id)).recreate_reasons == [
            RecreateReason.SECRETS
        ]

    asyncio.run(scenario())


def test_a_replacement_recreates_the_running_instances_it_names_and_only_those(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = _hub(tmp_path, runtime)
        client = hub_client(hub)
        await set_hub_secret(client, "GITHUB_TOKEN", "ghp-first")
        named = await _linked(client, hub, "named", running=True)
        unnamed = await _linked(client, hub, "unnamed", running=True)

        outcome = await _replaced(client, hub, "ghp-second", recreate=[named.instance_id])

        assert outcome.state is OperationState.SUCCEEDED
        assert [(spec.instance_id, spec.env["GITHUB_TOKEN"]) for spec in runtime.created] == [
            (str(named.instance_id), "ghp-first"),
            (str(unnamed.instance_id), "ghp-first"),
            (str(named.instance_id), "ghp-second"),
        ]
        assert (await _status(client, named.instance_id)).recreate_reasons == []
        assert (await _status(client, unnamed.instance_id)).recreate_reasons == [
            RecreateReason.SECRETS
        ]

    asyncio.run(scenario())


def test_a_stopped_linked_instance_applies_a_replacement_when_it_starts(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = _hub(tmp_path, runtime)
        client = hub_client(hub)
        await set_hub_secret(client, "GITHUB_TOKEN", "ghp-first")
        stopped = await _linked(client, hub, "idle")
        await _replaced(client, hub, "ghp-second")

        started = await client.call(
            INSTANCE_START, InstanceStartCommand(instance_id=stopped.instance_id)
        )
        assert isinstance(started, LifecycleOperationResult)

        assert (await finished_operation(client, started)).state is OperationState.SUCCEEDED
        assert [spec.env["GITHUB_TOKEN"] for spec in runtime.created] == [
            "ghp-first",
            "ghp-second",
        ]

    asyncio.run(scenario())


def test_a_replacement_unlinks_a_field_that_holds_another_value_and_names_its_instance(tmp_path):
    async def scenario() -> None:
        hub = _hub(tmp_path)
        client = hub_client(hub)
        await set_hub_secret(client, "GITHUB_TOKEN", "ghp-first")
        kept = await _linked(client, hub, "kept")
        drifted = await _linked(client, hub, "drifted")
        secrets = hub.instances_directory / str(drifted.instance_id) / ".env"
        secrets.write_text(secrets.read_text().replace("ghp-first", "ghp-by-hand"))

        outcome = await _replaced(client, hub, "ghp-second")

        assert outcome.state is OperationState.SUCCEEDED
        assert "drifted" in outcome.detail
        assert "kept" not in outcome.detail
        assert instance_environment(hub, drifted.instance_id)["GITHUB_TOKEN"] == "ghp-by-hand"
        assert instance_environment(hub, kept.instance_id)["GITHUB_TOKEN"] == "ghp-second"
        assert await _github_link(client, drifted.instance_id) is None
        assert await _links(client) == [("GITHUB_TOKEN", [("kept", "GITHUB_TOKEN")])]

    asyncio.run(scenario())


def test_deleting_a_hub_secret_unlinks_every_instance_and_leaves_its_values(tmp_path):
    async def scenario() -> None:
        hub = _hub(tmp_path)
        client = hub_client(hub)
        await set_hub_secret(client, "GITHUB_TOKEN", "ghp-first")
        first = await _linked(client, hub, "first")
        second = await _linked(client, hub, "second")
        before = {
            instance.instance_id: instance_environment(hub, instance.instance_id)
            for instance in (first, second)
        }

        result = await client.call(HUB_SECRET_DELETE, HubSecretDeleteCommand(name="GITHUB_TOKEN"))

        assert isinstance(result, HubSecretDeleteResult)
        assert [(link.instance_id, link.manifest_id, link.field) for link in result.unlinked] == [
            (first.instance_id, "first", "GITHUB_TOKEN"),
            (second.instance_id, "second", "GITHUB_TOKEN"),
        ]
        for instance in (first, second):
            assert instance_environment(hub, instance.instance_id) == before[instance.instance_id]
            assert await _github_link(client, instance.instance_id) is None
        assert await _links(client) == []

    asyncio.run(scenario())


def test_the_list_names_each_linked_instance_and_field_and_never_a_value(tmp_path):
    async def scenario() -> None:
        hub = _hub(tmp_path)
        client = hub_client(hub)
        await set_hub_secret(client, "GITHUB_TOKEN", f"{_SENTINEL}-github")
        await set_hub_secret(client, "OPENAI_API_KEY", f"{_SENTINEL}-openai")
        await set_hub_secret(client, "UNUSED", f"{_SENTINEL}-unused")
        await _linked(
            client, hub, "coder", {"api_key": "OPENAI_API_KEY", "GITHUB_TOKEN": "GITHUB_TOKEN"}
        )
        await _linked(client, hub, "reviewer")

        listed = await client.call(HUB_SECRET_LIST, HubSecretListCommand())

        assert await _links(client) == [
            ("GITHUB_TOKEN", [("coder", "GITHUB_TOKEN"), ("reviewer", "GITHUB_TOKEN")]),
            ("OPENAI_API_KEY", [("coder", "api_key")]),
            ("UNUSED", []),
        ]
        assert _SENTINEL not in listed.model_dump_json()

    asyncio.run(scenario())


def test_removing_or_deleting_an_instance_drops_its_links_and_keeps_the_hub_secrets(tmp_path):
    async def scenario() -> None:
        hub = _hub(tmp_path)
        client = hub_client(hub)
        await set_hub_secret(client, "GITHUB_TOKEN", "ghp-first")
        gone = await _linked(client, hub, "gone")
        back = await _linked(client, hub, "back")
        await _linked(client, hub, "staying")

        for instance in (gone, back):
            assert (await removed(client, instance)).state is OperationState.SUCCEEDED
        assert (await deleted(client, await previewed(client, gone))).state is (
            OperationState.SUCCEEDED
        )
        assert (await restored(client, back)).state is OperationState.SUCCEEDED

        assert await _links(client) == [("GITHUB_TOKEN", [("staying", "GITHUB_TOKEN")])]
        assert await _github_link(client, back.instance_id) is None
        assert instance_environment(hub, back.instance_id)["GITHUB_TOKEN"] == "ghp-first"
        assert hub.secrets.values() == {"GITHUB_TOKEN": "ghp-first"}

    asyncio.run(scenario())


def test_a_replacement_that_fails_a_write_rolls_back_so_a_retry_rewrites_every_instance(
    tmp_path, monkeypatch
):
    async def scenario() -> None:
        hub = _hub(tmp_path)
        client = hub_client(hub)
        await set_hub_secret(client, "GITHUB_TOKEN", "ghp-first")
        instances = [await _linked(client, hub, name) for name in ("first", "second")]
        original = os.open
        writes = 0

        def refuse_the_second_write(path: object, *arguments: object, **options: object) -> int:
            """Stand in for the disk failing under the second instance's write."""
            nonlocal writes
            if isinstance(path, str | Path) and Path(path).name == ".env.replacing":
                writes += 1
                if writes == 2:
                    raise OSError(f"no space left on device while writing {_SENTINEL}")
            return original(path, *arguments, **options)  # ty: ignore[invalid-argument-type]

        monkeypatch.setattr(os, "open", refuse_the_second_write)
        failed = await _replaced(client, hub, _SENTINEL)
        monkeypatch.undo()

        assert failed.state is OperationState.FAILED
        assert _SENTINEL not in failed.model_dump_json()
        assert hub.secrets.values() == {"GITHUB_TOKEN": "ghp-first"}
        for instance in instances:
            assert instance_environment(hub, instance.instance_id)["GITHUB_TOKEN"] == "ghp-first"

        retried = await _replaced(client, hub, "ghp-second")

        assert retried.state is OperationState.SUCCEEDED
        for instance in instances:
            assert instance_environment(hub, instance.instance_id)["GITHUB_TOKEN"] == "ghp-second"
            assert await _github_link(client, instance.instance_id) == "GITHUB_TOKEN"

    asyncio.run(scenario())
