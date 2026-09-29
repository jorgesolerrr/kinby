"""The hub collects every recreate reason, and a start applies the ones a stopped instance has."""

import asyncio
from dataclasses import replace
from uuid import UUID

from kinby.cli.client import ContractClient
from kinby.contracts import (
    INSTANCE_RECREATE,
    INSTANCE_SECRETS_SET,
    INSTANCE_START,
    INSTANCE_STATUS,
    InstanceRecreateCommand,
    InstanceSecretsSetCommand,
    InstanceStartCommand,
    InstanceStatusCommand,
    InstanceStatusResult,
    LifecycleOperationResult,
    OperationState,
    RecreateReason,
)
from kinby.hub import RuntimeStatus
from tests.test_hub import (
    FakeControl,
    FakeImages,
    FakeRuntime,
    created_instance,
    finished_operation,
    hub_at,
    hub_client,
    started_instance,
)


async def _status(client: ContractClient, instance_id: UUID) -> InstanceStatusResult:
    status = await client.call(INSTANCE_STATUS, InstanceStatusCommand(instance_id=instance_id))
    assert isinstance(status, InstanceStatusResult)
    return status


async def _replaced(client: ContractClient, instance_id: UUID) -> None:
    accepted = await client.call(
        INSTANCE_SECRETS_SET,
        InstanceSecretsSetCommand(instance_id=instance_id, secrets={"PROVIDER_TOKEN": "second"}),
    )
    assert isinstance(accepted, LifecycleOperationResult)
    assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED


def test_replaced_secrets_are_a_reason_until_the_container_is_recreated(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub", images=FakeImages(), control=FakeControl())
        client = hub_client(hub)
        created = await started_instance(client, hub, secrets={"PROVIDER_TOKEN": "first"})

        before = await _status(client, created.instance_id)
        await _replaced(client, created.instance_id)
        pending = await _status(client, created.instance_id)
        recreated = await client.call(
            INSTANCE_RECREATE, InstanceRecreateCommand(instance_id=created.instance_id)
        )
        assert isinstance(recreated, LifecycleOperationResult)
        assert (await finished_operation(client, recreated)).state is OperationState.SUCCEEDED
        after = await _status(client, created.instance_id)

        assert before.recreate_reasons == []
        assert pending.recreate_reasons == [RecreateReason.SECRETS]
        assert after.recreate_reasons == []

    asyncio.run(scenario())


def test_an_edited_package_config_is_the_reason_the_instance_reports(tmp_path):
    async def scenario() -> None:
        control = FakeControl()
        hub = hub_at(tmp_path / "hub", images=FakeImages(), control=control)
        client = hub_client(hub)
        created = await started_instance(client, hub)

        before = await _status(client, created.instance_id)
        control.reasons = [RecreateReason.PACKAGE_CONFIG]
        pending = await _status(client, created.instance_id)

        assert before.recreate_reasons == []
        assert pending.recreate_reasons == [RecreateReason.PACKAGE_CONFIG]

    asyncio.run(scenario())


def test_both_reasons_are_listed_together(tmp_path):
    async def scenario() -> None:
        control = FakeControl(reasons=[RecreateReason.PACKAGE_CONFIG])
        hub = hub_at(tmp_path / "hub", images=FakeImages(), control=control)
        client = hub_client(hub)
        created = await started_instance(client, hub, secrets={"PROVIDER_TOKEN": "first"})

        await _replaced(client, created.instance_id)

        assert (await _status(client, created.instance_id)).recreate_reasons == [
            RecreateReason.SECRETS,
            RecreateReason.PACKAGE_CONFIG,
        ]

    asyncio.run(scenario())


def test_an_instance_that_does_not_answer_still_reports_its_replaced_secrets(tmp_path):
    async def scenario() -> None:
        control = FakeControl(reasons=[RecreateReason.PACKAGE_CONFIG])
        hub = hub_at(tmp_path / "hub", images=FakeImages(), control=control)
        client = hub_client(hub)
        created = await started_instance(client, hub, secrets={"PROVIDER_TOKEN": "first"})
        await _replaced(client, created.instance_id)

        control.reachable = False
        status = await _status(client, created.instance_id)

        assert status.recreate_reasons == [RecreateReason.SECRETS]

    asyncio.run(scenario())


def test_a_container_created_before_the_label_reports_no_replaced_secrets(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        client = hub_client(hub)
        created = await created_instance(client, secrets={"PROVIDER_TOKEN": "first"})
        runtime_id = str(created.instance_id)
        runtime.descriptions[runtime_id] = replace(
            runtime.descriptions[runtime_id], secrets_digest=None
        )

        await _replaced(client, created.instance_id)

        assert (await _status(client, created.instance_id)).recreate_reasons == []

    asyncio.run(scenario())


def test_starting_a_stopped_instance_with_replaced_secrets_recreates_it_first(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        client = hub_client(hub)
        created = await created_instance(client, secrets={"PROVIDER_TOKEN": "first"})
        await _replaced(client, created.instance_id)
        stopped = await _status(client, created.instance_id)

        accepted = await client.call(
            INSTANCE_START, InstanceStartCommand(instance_id=created.instance_id)
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)
        started = await _status(client, created.instance_id)

        assert stopped.recreate_reasons == [RecreateReason.SECRETS]
        assert outcome.state is OperationState.SUCCEEDED
        assert [step.name for step in outcome.steps] == [
            "recreate",
            "validate",
            "remove",
            "create",
            "start",
        ]
        assert [spec.env["PROVIDER_TOKEN"] for spec in runtime.created] == ["first", "second"]
        assert runtime.started == [str(created.instance_id)]
        assert started.process == "running"
        assert started.recreate_reasons == []

    asyncio.run(scenario())


def test_recovery_starts_a_stopped_container_without_recreating_it(tmp_path):
    """Recovery restores intended state and starts no replacement (ADR 0051)."""

    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        client = hub_client(hub)
        created = await started_instance(client, hub, secrets={"PROVIDER_TOKEN": "first"})
        await _replaced(client, created.instance_id)
        hub.close()
        runtime.states[str(created.instance_id)] = RuntimeStatus("stopped", None)

        reopened = hub_at(
            tmp_path / "hub", runtime=runtime, images=FakeImages(), control=FakeControl()
        )
        await reopened.recover()
        status = await _status(hub_client(reopened), created.instance_id)

        assert len(runtime.created) == 1
        assert runtime.started == [str(created.instance_id)] * 2
        assert status.recreate_reasons == [RecreateReason.SECRETS]

    asyncio.run(scenario())
