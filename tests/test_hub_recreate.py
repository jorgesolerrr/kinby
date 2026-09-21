"""Explicit container recreation: the only way replaced secrets and a lost container come back."""

import asyncio

from kinby.contracts import (
    INSTANCE_LIST,
    INSTANCE_RECREATE,
    INSTANCE_SECRETS_SET,
    INSTANCE_STOP,
    OPERATION_GET,
    ErrorCode,
    ErrorEnvelope,
    InstanceListCommand,
    InstanceRecreateCommand,
    InstanceSecretsSetCommand,
    InstanceStopCommand,
    LifecycleOperationResult,
    OperationGetCommand,
    OperationKind,
    OperationState,
    Scope,
    StorageKind,
)
from kinby.hub import InstanceSpec, RecoveredState
from tests.test_hub import (
    FakeControl,
    FakeImages,
    FakeRuntime,
    created_instance,
    finished_operation,
    hub_at,
    hub_client,
    instance_environment,
    started_instance,
)
from tests.test_hub_recovery import claim_storage

_SENTINEL = "sentinel-applied-secret"


def test_a_recreation_applies_replaced_secrets_through_the_drain_and_stop_path(tmp_path):
    async def scenario() -> None:
        control = FakeControl()
        runtime = FakeRuntime()
        images = FakeImages()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images, control=control)
        client = hub_client(hub)
        created = await started_instance(client, hub, secrets={"PROVIDER_TOKEN": "first-value"})
        replaced = await client.call(
            INSTANCE_SECRETS_SET,
            InstanceSecretsSetCommand(
                instance_id=created.instance_id,
                secrets={"PROVIDER_TOKEN": _SENTINEL},
            ),
        )
        assert isinstance(replaced, LifecycleOperationResult)
        assert (await finished_operation(client, replaced)).state is OperationState.SUCCEEDED

        accepted = await client.call(
            INSTANCE_RECREATE,
            InstanceRecreateCommand(instance_id=created.instance_id),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)

        assert outcome.kind is OperationKind.RECREATE
        assert outcome.state is OperationState.SUCCEEDED
        assert outcome.detail == "Container recreated."
        assert [step.name for step in outcome.steps] == [
            "validate",
            "probe",
            "drain",
            "result",
            "container",
            "remove",
            "create",
            "start",
        ]
        assert control.forces == [False]
        assert len(runtime.created) == 2
        assert runtime.created[1].env["PROVIDER_TOKEN"] == _SENTINEL
        assert runtime.created[1].image == runtime.created[0].image
        assert images.revisions == ["HEAD"]
        assert runtime.removed == [(str(created.instance_id), False)]
        assert runtime.started == [str(created.instance_id)] * 2
        assert _SENTINEL not in outcome.model_dump_json()

    asyncio.run(scenario())


def test_a_recreation_brings_a_missing_container_back_on_request(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        client = hub_client(hub)
        created = await started_instance(client, hub)
        runtime.states.pop(str(created.instance_id))

        accepted = await client.call(
            INSTANCE_RECREATE,
            InstanceRecreateCommand(instance_id=created.instance_id),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)

        assert outcome.state is OperationState.SUCCEEDED
        assert [step.name for step in outcome.steps] == ["validate", "create", "start"]
        assert len(runtime.created) == 2
        assert runtime.removed == []
        assert runtime.started == [str(created.instance_id)] * 2

    asyncio.run(scenario())


def test_a_stopped_instance_is_recreated_stopped(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        client = hub_client(hub)
        created = await created_instance(client)

        accepted = await client.call(
            INSTANCE_RECREATE,
            InstanceRecreateCommand(instance_id=created.instance_id),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)

        assert outcome.state is OperationState.SUCCEEDED
        assert [step.name for step in outcome.steps] == ["validate", "remove", "create"]
        assert runtime.started == []
        assert runtime.removed == [(str(created.instance_id), False)]

    asyncio.run(scenario())


def test_a_recreation_keeps_the_storage_the_memory_and_the_stored_secrets(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        client = hub_client(hub)
        created = await created_instance(client, secrets={"PROVIDER_TOKEN": _SENTINEL})
        instance_path = hub.instances_directory / str(created.instance_id)
        memory = instance_path / ".state" / "graph.sqlite"
        memory.parent.mkdir(parents=True, exist_ok=True)
        memory.write_text("remembered\n", encoding="utf-8")
        before = await client.call(INSTANCE_LIST, InstanceListCommand())

        accepted = await client.call(
            INSTANCE_RECREATE,
            InstanceRecreateCommand(instance_id=created.instance_id),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED
        after = await client.call(INSTANCE_LIST, InstanceListCommand())

        assert not isinstance(before, ErrorEnvelope)
        assert not isinstance(after, ErrorEnvelope)
        assert after.instances == before.instances
        assert memory.read_text(encoding="utf-8") == "remembered\n"
        assert instance_environment(hub, created.instance_id)["PROVIDER_TOKEN"] == _SENTINEL
        assert runtime.removed == [(str(created.instance_id), False)]

    asyncio.run(scenario())


def test_a_recreation_is_refused_while_another_operation_owns_the_instance(tmp_path):
    async def scenario() -> None:
        control = FakeControl(holds=True)
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=control)
        client = hub_client(hub)
        created = await started_instance(client, hub)
        stopping = await client.call(
            INSTANCE_STOP,
            InstanceStopCommand(instance_id=created.instance_id),
        )
        assert isinstance(stopping, LifecycleOperationResult)
        await asyncio.wait_for(control.asked.wait(), timeout=5)

        refused = await client.call(
            INSTANCE_RECREATE,
            InstanceRecreateCommand(instance_id=created.instance_id),
        )
        control.release.set()
        assert (await finished_operation(client, stopping)).state is OperationState.SUCCEEDED

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INSTANCE_BUSY
        assert refused.retryable
        assert len(runtime.created) == 1
        assert runtime.removed == []

    asyncio.run(scenario())


def test_a_recreation_is_refused_when_another_instance_owns_the_storage(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        client = hub_client(hub)
        created = await created_instance(client)
        record = hub.registry.instance(created.instance_id)
        assert record is not None
        volume = next(item for item in record.storage if item.kind is StorageKind.VOLUME)
        claim_storage(hub.directory / "registry.sqlite", volume.source)

        accepted = await client.call(
            INSTANCE_RECREATE,
            InstanceRecreateCommand(instance_id=created.instance_id),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)

        assert outcome.state is OperationState.FAILED
        assert volume.source in outcome.detail
        assert "was not replaced" in outcome.detail
        assert len(runtime.created) == 1
        assert runtime.removed == []

    asyncio.run(scenario())


def test_a_recreation_revalidates_the_retained_configuration_before_any_effect(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        client = hub_client(hub)
        created = await created_instance(client)
        manifest = hub.instances_directory / str(created.instance_id) / "kinby.toml"
        manifest.write_text(
            'id = "alice"\n[models]\nmain = "not-a-provider-model"\n',
            encoding="utf-8",
        )

        accepted = await client.call(
            INSTANCE_RECREATE,
            InstanceRecreateCommand(instance_id=created.instance_id),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)

        assert outcome.state is OperationState.FAILED
        assert "models.main" in outcome.detail
        assert [step.name for step in outcome.steps] == ["validate"]
        assert len(runtime.created) == 1
        assert runtime.removed == []

    asyncio.run(scenario())


def test_an_interruption_between_the_removal_and_the_create_stays_visible_and_is_fixable(tmp_path):
    async def scenario() -> None:
        runtime = CrashOnReplacement()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        created = await created_instance(hub_client(hub))
        interrupted = await hub_client(hub).call(
            INSTANCE_RECREATE,
            InstanceRecreateCommand(instance_id=created.instance_id),
        )
        assert isinstance(interrupted, LifecycleOperationResult)
        await asyncio.wait_for(runtime.crashed.wait(), timeout=5)
        await asyncio.sleep(0)
        hub.close()

        reopened = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        recovery = await reopened.recover()
        client = hub_client(reopened)
        failed = await client.call(
            OPERATION_GET,
            OperationGetCommand(operation_id=interrupted.operation_id),
        )
        again = await client.call(
            INSTANCE_RECREATE,
            InstanceRecreateCommand(instance_id=created.instance_id),
        )
        assert isinstance(again, LifecycleOperationResult)
        outcome = await finished_operation(client, again)

        assert [instance.state for instance in recovery.instances] == [RecoveredState.MISSING]
        assert not isinstance(failed, ErrorEnvelope)
        assert failed.state is OperationState.FAILED
        assert failed.detail == "The hub stopped before this operation finished."
        assert [step.name for step in failed.steps] == ["validate", "remove", "create"]
        assert failed.steps[-1].state is OperationState.FAILED
        assert outcome.state is OperationState.SUCCEEDED
        assert [step.name for step in outcome.steps] == ["validate", "create"]
        assert len(runtime.created) == 2

    asyncio.run(scenario())


class CrashOnReplacement(FakeRuntime):
    """Die once between removing a container and creating its replacement."""

    def __init__(self) -> None:
        super().__init__()
        self.crashed = asyncio.Event()

    async def create(self, spec: InstanceSpec) -> None:
        if self.removed and not self.crashed.is_set():
            self.crashed.set()
            raise asyncio.CancelledError
        await super().create(spec)


def test_an_unauthorized_recreation_has_no_effect(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        created = await started_instance(hub_client(hub), hub)

        refused = await hub.dispatcher.dispatch(
            INSTANCE_RECREATE.name,
            {"instance_id": str(created.instance_id)},
            {Scope.HUB_READ},
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.PERMISSION_DENIED
        assert len(runtime.created) == 1
        assert runtime.removed == []

    asyncio.run(scenario())
