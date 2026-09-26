"""Removal keeps an instance's data and record; restoration brings it back from that record."""

import asyncio
import shutil
import sqlite3
from pathlib import Path

import pytest

from kinby.cli.client import ContractClient
from kinby.contracts import (
    INSTANCE_ADOPT_PREVIEW,
    INSTANCE_CREATE,
    INSTANCE_LIST,
    INSTANCE_REMOVE,
    INSTANCE_RESTORE,
    INSTANCE_SECRETS_SET,
    INSTANCE_START,
    INSTANCE_STATUS,
    INSTANCE_STOP,
    OPERATION_GET,
    AdoptionFindingKind,
    ContainerOwner,
    ErrorCode,
    ErrorEnvelope,
    InstanceAdoptPreviewCommand,
    InstanceCreateCommand,
    InstanceListCommand,
    InstanceRemoveCommand,
    InstanceRestoreCommand,
    InstanceSecretsSetCommand,
    InstanceStartCommand,
    InstanceStatusCommand,
    InstanceStopCommand,
    InstanceSummary,
    IntendedState,
    LifecycleOperationResult,
    OperationGetCommand,
    OperationGetResult,
    OperationKind,
    OperationState,
    ProcessState,
    Scope,
    StorageItem,
    StorageKind,
)
from kinby.hub import ContainerDescription, Hub, InstanceSpec, RecoveredState, RuntimeStatus
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
from tests.test_hub_adoption import CODER_CONTAINER, coder_storage, existing_coder
from tests.test_hub_recovery import claim_storage

_SENTINEL = "sentinel-retained-secret"


async def removed(
    client: ContractClient,
    instance: LifecycleOperationResult,
) -> OperationGetResult:
    accepted = await client.call(
        INSTANCE_REMOVE,
        InstanceRemoveCommand(instance_id=instance.instance_id),
    )
    assert isinstance(accepted, LifecycleOperationResult)
    assert accepted.instance_id == instance.instance_id
    return await finished_operation(client, accepted)


async def restored(
    client: ContractClient,
    instance: LifecycleOperationResult,
) -> OperationGetResult:
    accepted = await client.call(
        INSTANCE_RESTORE,
        InstanceRestoreCommand(instance_id=instance.instance_id),
    )
    assert isinstance(accepted, LifecycleOperationResult)
    return await finished_operation(client, accepted)


async def listed(client: ContractClient, *, removed: bool = False) -> list[InstanceSummary]:
    result = await client.call(INSTANCE_LIST, InstanceListCommand(removed=removed))
    assert not isinstance(result, ErrorEnvelope)
    return result.instances


def storage_of(hub: Hub, instance: LifecycleOperationResult) -> tuple[StorageItem, ...]:
    record = hub.registry.instance(instance.instance_id)
    assert record is not None
    return record.storage


def test_a_removal_drains_the_instance_and_removes_only_its_container(tmp_path):
    async def scenario() -> None:
        control = FakeControl()
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, control=control)
        client = hub_client(hub)
        created = await started_instance(client, hub, secrets={"PROVIDER_TOKEN": _SENTINEL})
        instance_path = hub.instances_directory / str(created.instance_id)
        memory = instance_path / ".state" / "graph.sqlite"
        memory.parent.mkdir(parents=True, exist_ok=True)
        memory.write_text("remembered\n", encoding="utf-8")
        [before] = await listed(client)

        outcome = await removed(client, created)

        assert outcome.kind is OperationKind.REMOVE
        assert outcome.state is OperationState.SUCCEEDED
        assert outcome.detail == "Instance removed. Its data and record are retained."
        assert [step.name for step in outcome.steps] == [
            "probe",
            "drain",
            "result",
            "container",
            "remove",
        ]
        assert control.forces == [False]
        assert runtime.stopped == [30]
        assert runtime.removed == [(str(created.instance_id), False)]
        assert memory.read_text(encoding="utf-8") == "remembered\n"
        assert instance_environment(hub, created.instance_id)["PROVIDER_TOKEN"] == _SENTINEL
        assert await listed(client) == []
        assert await listed(client, removed=True) == [
            before.model_copy(update={"intended_state": IntendedState.REMOVED})
        ]

    asyncio.run(scenario())


def test_a_pending_removal_drain_escalates_to_a_force_stop_inside_the_same_operation(tmp_path):
    async def scenario() -> None:
        control = FakeControl(holds=True)
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, control=control)
        client = hub_client(hub)
        created = await started_instance(client, hub)

        removing = await client.call(
            INSTANCE_REMOVE,
            InstanceRemoveCommand(instance_id=created.instance_id),
        )
        assert isinstance(removing, LifecycleOperationResult)
        await asyncio.wait_for(control.asked.wait(), timeout=5)
        escalated = await client.call(
            INSTANCE_STOP,
            InstanceStopCommand(instance_id=created.instance_id, force=True),
        )
        assert isinstance(escalated, LifecycleOperationResult)
        outcome = await finished_operation(client, removing)

        assert escalated.operation_id == removing.operation_id
        assert outcome.state is OperationState.SUCCEEDED
        assert control.forces == [False, True]
        assert [step.name for step in outcome.steps] == [
            "probe",
            "drain",
            "force",
            "result",
            "container",
            "remove",
        ]
        assert runtime.removed == [(str(created.instance_id), False)]

    asyncio.run(scenario())


def test_a_removal_never_deletes_an_external_directory_the_manifest_references(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        client = hub_client(hub)
        created = await created_instance(client)
        external = tmp_path / "notes"
        external.mkdir()
        (external / "plan.md").write_text("the user's own work\n", encoding="utf-8")
        manifest = hub.instances_directory / str(created.instance_id) / "kinby.toml"
        with manifest.open("a", encoding="utf-8") as handle:
            handle.write(f'\n[workspace]\npath = "{external}"\n')

        outcome = await removed(client, created)

        assert outcome.state is OperationState.SUCCEEDED
        assert [step.name for step in outcome.steps] == ["remove"]
        assert (external / "plan.md").read_text(encoding="utf-8") == "the user's own work\n"
        assert (hub.instances_directory / str(created.instance_id) / "kinby.toml").exists()

    asyncio.run(scenario())


def test_retained_records_are_readable_under_hub_read_and_hidden_from_the_active_list(tmp_path):
    async def scenario() -> None:
        hub = hub_at(tmp_path / "hub")
        client = hub_client(hub)
        kept = await created_instance(client)
        gone = await created_instance(client)
        assert (await removed(client, gone)).state is OperationState.SUCCEEDED
        reader = hub_client(hub, {Scope.HUB_READ})

        active = await listed(reader)
        retained = await listed(reader, removed=True)

        assert [summary.instance_id for summary in active] == [kept.instance_id]
        assert [summary.instance_id for summary in retained] == [gone.instance_id]
        assert retained[0].storage == list(storage_of(hub, gone))
        assert retained[0].image_id == "sha256:selected-image"

    asyncio.run(scenario())


def test_a_removed_instance_takes_no_start_stop_or_status_until_it_is_restored(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        client = hub_client(hub)
        created = await created_instance(client)
        await removed(client, created)

        start = await client.call(
            INSTANCE_START,
            InstanceStartCommand(instance_id=created.instance_id),
        )
        stop = await client.call(
            INSTANCE_STOP,
            InstanceStopCommand(instance_id=created.instance_id),
        )
        status = await client.call(
            INSTANCE_STATUS,
            InstanceStatusCommand(instance_id=created.instance_id),
        )
        again = await client.call(
            INSTANCE_REMOVE,
            InstanceRemoveCommand(instance_id=created.instance_id),
        )

        for refused in (start, stop, status, again):
            assert isinstance(refused, ErrorEnvelope)
            assert refused.code is ErrorCode.NOT_FOUND
        assert runtime.started == []

    asyncio.run(scenario())


def test_a_start_queued_behind_a_removal_fails_instead_of_starting_it(tmp_path):
    async def scenario() -> None:
        control = FakeControl(holds=True)
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, control=control)
        client = hub_client(hub)
        created = await started_instance(client, hub)
        removing = await client.call(
            INSTANCE_REMOVE,
            InstanceRemoveCommand(instance_id=created.instance_id),
        )
        assert isinstance(removing, LifecycleOperationResult)
        await asyncio.wait_for(control.asked.wait(), timeout=5)

        starting = await client.call(
            INSTANCE_START,
            InstanceStartCommand(instance_id=created.instance_id),
        )
        assert isinstance(starting, LifecycleOperationResult)
        control.release.set()
        assert (await finished_operation(client, removing)).state is OperationState.SUCCEEDED
        start = await finished_operation(client, starting)

        assert start.state is OperationState.FAILED
        assert "was not found" in start.detail
        assert runtime.started == [str(created.instance_id)]

    asyncio.run(scenario())


def test_a_secrets_replacement_queued_behind_a_removal_fails_and_restoration_proceeds(tmp_path):
    async def scenario() -> None:
        control = FakeControl(holds=True)
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, control=control, images=FakeImages())
        client = hub_client(hub)
        created = await started_instance(
            client,
            hub,
            secrets={"PROVIDER_TOKEN": "first-value"},
        )
        removing = await client.call(
            INSTANCE_REMOVE,
            InstanceRemoveCommand(instance_id=created.instance_id),
        )
        assert isinstance(removing, LifecycleOperationResult)
        await asyncio.wait_for(control.asked.wait(), timeout=5)

        replacing = await client.call(
            INSTANCE_SECRETS_SET,
            InstanceSecretsSetCommand(
                instance_id=created.instance_id,
                secrets={"PROVIDER_TOKEN": _SENTINEL},
            ),
        )
        assert isinstance(replacing, LifecycleOperationResult)
        control.release.set()
        assert (await finished_operation(client, removing)).state is OperationState.SUCCEEDED
        replaced = await finished_operation(client, replacing)
        outcome = await restored(client, created)

        assert replaced.state is OperationState.FAILED
        assert "was not found" in replaced.detail
        assert instance_environment(hub, created.instance_id)["PROVIDER_TOKEN"] == "first-value"
        assert outcome.state is OperationState.SUCCEEDED
        assert [summary.instance_id for summary in await listed(client)] == [created.instance_id]

    asyncio.run(scenario())


def test_a_removal_is_refused_while_another_operation_owns_the_instance(tmp_path):
    async def scenario() -> None:
        control = FakeControl(holds=True)
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, control=control)
        client = hub_client(hub)
        created = await started_instance(client, hub)
        stopping = await client.call(
            INSTANCE_STOP,
            InstanceStopCommand(instance_id=created.instance_id),
        )
        assert isinstance(stopping, LifecycleOperationResult)
        await asyncio.wait_for(control.asked.wait(), timeout=5)

        refused = await client.call(
            INSTANCE_REMOVE,
            InstanceRemoveCommand(instance_id=created.instance_id),
        )
        control.release.set()
        assert (await finished_operation(client, stopping)).state is OperationState.SUCCEEDED

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INSTANCE_BUSY
        assert runtime.removed == []

    asyncio.run(scenario())


def test_a_removal_leaves_a_container_another_manager_labels(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        client = hub_client(hub)
        created = await created_instance(client)
        runtime_id = str(created.instance_id)
        runtime.descriptions[runtime_id] = ContainerDescription(
            runtime_id=runtime_id,
            image="sha256:selected-image",
            owner=ContainerOwner.COMPOSE,
            owner_name="kinby",
            storage=(),
        )

        outcome = await removed(client, created)

        assert outcome.state is OperationState.FAILED
        assert 'Compose project "kinby"' in outcome.detail
        assert runtime.removed == []
        assert [summary.instance_id for summary in await listed(client)] == [created.instance_id]

    asyncio.run(scenario())


def test_a_removal_that_failed_before_its_first_step_stays_unfinished_after_restart(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        client = hub_client(hub)
        created = await created_instance(client)
        runtime_id = str(created.instance_id)
        runtime.descriptions[runtime_id] = ContainerDescription(
            runtime_id=runtime_id,
            image="sha256:selected-image",
            owner=ContainerOwner.COMPOSE,
            owner_name="kinby",
            storage=(),
        )
        outcome = await removed(client, created)
        assert outcome.state is OperationState.FAILED
        hub.close()

        reopened = hub_at(tmp_path / "hub", runtime=runtime)
        recovery = await reopened.recover()

        assert [instance.state for instance in recovery.instances] == [RecoveredState.INCOMPLETE]
        assert "Remove the instance again" in recovery.instances[0].detail
        assert [summary.instance_id for summary in await listed(hub_client(reopened))] == [
            created.instance_id
        ]
        assert runtime.started == []

    asyncio.run(scenario())


def test_retained_storage_stays_reserved_against_another_adoption(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, control=FakeControl())
        client = hub_client(hub)
        created = await created_instance(client)
        await removed(client, created)
        workspace = next(
            item for item in storage_of(hub, created) if item.destination == "/instance/workspace"
        )
        directory = tmp_path / "box" / "coder"
        storage = (*coder_storage(directory)[:1], workspace)
        existing_coder(runtime, directory, storage=storage)

        previewed = await client.call(
            INSTANCE_ADOPT_PREVIEW,
            InstanceAdoptPreviewCommand(
                path=directory,
                runtime_id=CODER_CONTAINER,
                relinquished=True,
            ),
        )

        assert not isinstance(previewed, ErrorEnvelope)
        [retained] = [
            finding
            for finding in previewed.findings
            if finding.kind is AdoptionFindingKind.RETAINED_STORAGE
        ]
        assert retained.blocking
        assert workspace.source in retained.detail
        assert str(created.instance_id) in retained.detail

    asyncio.run(scenario())


def test_a_removed_instance_is_restored_stopped_with_its_identity_data_and_secrets(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        images = FakeImages()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images, control=FakeControl())
        client = hub_client(hub)
        await prepared(client, None)
        accepted = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="alice",
                persona_name="Ada",
                model="openai:gpt-5",
                secrets={"api_key": "sk-test", "PROVIDER_TOKEN": _SENTINEL},
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED
        instance_path = hub.instances_directory / str(accepted.instance_id)
        memory = instance_path / ".state" / "graph.sqlite"
        memory.parent.mkdir(parents=True, exist_ok=True)
        memory.write_text("remembered\n", encoding="utf-8")
        [before] = await listed(client)
        await removed(client, accepted)

        outcome = await restored(client, accepted)

        assert outcome.kind is OperationKind.RESTORE
        assert outcome.state is OperationState.SUCCEEDED
        assert outcome.detail == "Instance restored and left stopped. Start it to run it."
        assert [step.name for step in outcome.steps] == ["validate", "create"]
        assert await listed(client, removed=True) == []
        assert await listed(client) == [before]
        assert before.persona_name == "Ada"
        assert before.manifest_id == "alice"
        assert len(runtime.created) == 2
        assert runtime.created[1] == runtime.created[0]
        assert runtime.started == []
        # The preparation and the creation built HEAD. Nothing since built another image.
        assert images.revisions == ["HEAD", "HEAD"]
        assert memory.read_text(encoding="utf-8") == "remembered\n"
        assert instance_environment(hub, accepted.instance_id)["PROVIDER_TOKEN"] == _SENTINEL
        assert _SENTINEL not in outcome.model_dump_json()

        started = await client.call(
            INSTANCE_START,
            InstanceStartCommand(instance_id=accepted.instance_id),
        )
        assert isinstance(started, LifecycleOperationResult)
        assert (await finished_operation(client, started)).state is OperationState.SUCCEEDED
        status = await client.call(
            INSTANCE_STATUS,
            InstanceStatusCommand(instance_id=accepted.instance_id),
        )
        assert not isinstance(status, ErrorEnvelope)
        assert status.process is ProcessState.RUNNING

    asyncio.run(scenario())


def test_only_a_removed_instance_is_restored(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        client = hub_client(hub)
        created = await created_instance(client)

        refused = await client.call(
            INSTANCE_RESTORE,
            InstanceRestoreCommand(instance_id=created.instance_id),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.NOT_FOUND
        assert len(runtime.created) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("owner_state", ["stopped", "removed"])
def test_a_restoration_is_refused_when_another_instance_owns_the_storage(tmp_path, owner_state):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        client = hub_client(hub)
        created = await created_instance(client)
        await removed(client, created)
        volume = next(item for item in storage_of(hub, created) if item.kind is StorageKind.VOLUME)
        claim_storage(hub.directory / "registry.sqlite", volume.source, intended_state=owner_state)

        outcome = await restored(client, created)

        assert outcome.state is OperationState.FAILED
        assert volume.source in outcome.detail
        assert [step.name for step in outcome.steps] == ["validate"]
        assert len(runtime.created) == 1
        assert created.instance_id in [
            summary.instance_id for summary in await listed(client, removed=True)
        ]

    asyncio.run(scenario())


def test_a_restoration_reports_a_missing_image_and_selects_no_other(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        images = FakeImages()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images)
        client = hub_client(hub)
        created = await created_instance(client)
        await removed(client, created)
        runtime.missing.add("sha256:selected-image")

        outcome = await restored(client, created)

        assert outcome.state is OperationState.FAILED
        assert "sha256:selected-image" in outcome.detail
        # The preparation and the creation built HEAD. Nothing since built another image.
        assert images.revisions == ["HEAD", "HEAD"]
        assert len(runtime.created) == 1

    asyncio.run(scenario())


def test_a_restoration_reports_a_missing_volume_instead_of_mounting_an_empty_one(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        client = hub_client(hub)
        created = await created_instance(client)
        await removed(client, created)
        workspace = next(
            item for item in storage_of(hub, created) if item.destination == "/instance/workspace"
        )
        runtime.missing.add(workspace.source)

        outcome = await restored(client, created)

        assert outcome.state is OperationState.FAILED
        assert workspace.source in outcome.detail
        assert len(runtime.created) == 1

    asyncio.run(scenario())


def test_a_restoration_reports_a_missing_instance_directory(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        client = hub_client(hub)
        created = await created_instance(client)
        await removed(client, created)
        shutil.rmtree(hub.instances_directory / str(created.instance_id))

        outcome = await restored(client, created)

        assert outcome.state is OperationState.FAILED
        assert "kinby.toml" in outcome.detail
        assert len(runtime.created) == 1

    asyncio.run(scenario())


def test_a_restoration_refuses_another_instance_found_in_the_retained_directory(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        client = hub_client(hub)
        created = await created_instance(client)
        await removed(client, created)
        manifest = hub.instances_directory / str(created.instance_id) / "kinby.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace('id = "alice"', 'id = "mallory"'),
            encoding="utf-8",
        )

        outcome = await restored(client, created)

        assert outcome.state is OperationState.FAILED
        assert '"mallory"' in outcome.detail
        assert '"alice"' in outcome.detail
        assert len(runtime.created) == 1

    asyncio.run(scenario())


def test_a_restoration_refuses_a_container_already_under_the_instance_s_name(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        client = hub_client(hub)
        created = await created_instance(client)
        await removed(client, created)
        runtime_id = str(created.instance_id)
        runtime.descriptions[runtime_id] = ContainerDescription(
            runtime_id=runtime_id,
            image="sha256:someone-else",
            owner=ContainerOwner.UNMANAGED,
            owner_name="",
            storage=(),
        )

        outcome = await restored(client, created)

        assert outcome.state is OperationState.FAILED
        assert f'container "{runtime_id}" already exists' in outcome.detail
        assert len(runtime.created) == 1
        assert runtime.removed == [(runtime_id, False)]

    asyncio.run(scenario())


def test_a_restoration_is_refused_while_another_operation_owns_the_instance(tmp_path):
    async def scenario() -> None:
        runtime = HeldCreate()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        client = hub_client(hub)
        created = await created_instance(client)
        await removed(client, created)
        runtime.hold()
        restoring = await client.call(
            INSTANCE_RESTORE,
            InstanceRestoreCommand(instance_id=created.instance_id),
        )
        assert isinstance(restoring, LifecycleOperationResult)
        await asyncio.wait_for(runtime.holding.wait(), timeout=5)

        refused = await client.call(
            INSTANCE_RESTORE,
            InstanceRestoreCommand(instance_id=created.instance_id),
        )
        runtime.released.set()
        assert (await finished_operation(client, restoring)).state is OperationState.SUCCEEDED

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INSTANCE_BUSY
        assert len(runtime.created) == 2

    asyncio.run(scenario())


class HeldCreate(FakeRuntime):
    """Hold the next create until the test releases it, so a restoration stays in flight."""

    def __init__(self) -> None:
        super().__init__()
        self.holding = asyncio.Event()
        self.released = asyncio.Event()
        self.released.set()

    def hold(self) -> None:
        self.released.clear()

    async def create(self, spec: InstanceSpec) -> None:
        self.holding.set()
        await self.released.wait()
        await super().create(spec)


class CrashingRemoval(FakeRuntime):
    """Die once the way a killed hub process does, before or after the container goes."""

    def __init__(self, *, after_remove: bool) -> None:
        super().__init__()
        self._after_remove = after_remove
        self.crashed = asyncio.Event()

    async def remove(self, instance_id: str, *, delete_data: bool = False) -> None:
        if self.crashed.is_set():
            await super().remove(instance_id, delete_data=delete_data)
            return
        if self._after_remove:
            await super().remove(instance_id, delete_data=delete_data)
        self.crashed.set()
        raise asyncio.CancelledError


class CrashingRestoration(FakeRuntime):
    """Die once while a restoration creates the container, before or after it exists."""

    def __init__(self, *, after_create: bool) -> None:
        super().__init__()
        self._after_create = after_create
        self.crashed = asyncio.Event()
        self.armed = False

    async def create(self, spec: InstanceSpec) -> None:
        if not self.armed:
            await super().create(spec)
            return
        self.armed = False
        if self._after_create:
            await super().create(spec)
        self.crashed.set()
        raise asyncio.CancelledError


def _snapshot(source: Path, destination: Path) -> None:
    """Copy the registry the way a killed process leaves it on disk."""
    destination.mkdir(parents=True)
    database = source / "registry.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy(database, destination / "registry.sqlite")


async def _drop_scheduled_work(*hubs: Hub | None) -> None:
    """Cancel work still scheduled, then release each hub directory."""
    current = asyncio.current_task()
    pending = [task for task in asyncio.all_tasks() if task is not current]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    for hub in hubs:
        if hub is not None:
            hub.close()


def test_a_start_accepted_during_a_removal_does_not_hide_the_running_container(tmp_path):
    async def scenario() -> None:
        control = FakeControl(holds=True)
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, control=control)
        reopened: Hub | None = None
        try:
            client = hub_client(hub)
            created = await started_instance(client, hub)
            removing = await client.call(
                INSTANCE_REMOVE,
                InstanceRemoveCommand(instance_id=created.instance_id),
            )
            assert isinstance(removing, LifecycleOperationResult)
            await asyncio.wait_for(control.asked.wait(), timeout=5)
            starting = await client.call(
                INSTANCE_START,
                InstanceStartCommand(instance_id=created.instance_id),
            )
            assert isinstance(starting, LifecycleOperationResult)
            _snapshot(hub.directory, tmp_path / "crashed")

            runtime_id = str(created.instance_id)
            restarted = FakeRuntime()
            restarted.states[runtime_id] = RuntimeStatus("running", True)
            restarted.addresses[runtime_id] = runtime.addresses[runtime_id]
            restarted.descriptions[runtime_id] = runtime.descriptions[runtime_id]
            reopened = hub_at(tmp_path / "crashed", runtime=restarted, control=FakeControl())
            recovery = await reopened.recover()
            again = await removed(hub_client(reopened), created)

            assert [instance.state for instance in recovery.instances] == [
                RecoveredState.INCOMPLETE
            ]
            assert "Remove the instance again" in recovery.instances[0].detail
            assert restarted.started == []
            assert again.state is OperationState.SUCCEEDED
            assert [summary.instance_id for summary in await listed(hub_client(reopened))] == []
        finally:
            await _drop_scheduled_work(hub, reopened)

    asyncio.run(scenario())


def test_a_start_accepted_during_a_removal_does_not_hide_a_container_that_is_gone(tmp_path):
    async def scenario() -> None:
        control = FakeControl(holds=True)
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, control=control)
        reopened: Hub | None = None
        try:
            client = hub_client(hub)
            created = await started_instance(client, hub)
            removing = await client.call(
                INSTANCE_REMOVE,
                InstanceRemoveCommand(instance_id=created.instance_id),
            )
            assert isinstance(removing, LifecycleOperationResult)
            await asyncio.wait_for(control.asked.wait(), timeout=5)
            starting = await client.call(
                INSTANCE_START,
                InstanceStartCommand(instance_id=created.instance_id),
            )
            assert isinstance(starting, LifecycleOperationResult)
            _snapshot(hub.directory, tmp_path / "crashed")

            restarted = FakeRuntime()
            reopened = hub_at(tmp_path / "crashed", runtime=restarted)
            recovery = await reopened.recover()
            client = hub_client(reopened)

            assert [instance.state for instance in recovery.instances] == [RecoveredState.REMOVED]
            assert "Removal reached" in recovery.instances[0].detail
            assert await listed(client) == []
            assert [summary.instance_id for summary in await listed(client, removed=True)] == [
                created.instance_id
            ]
            assert restarted.created == []
            assert restarted.started == []
        finally:
            await _drop_scheduled_work(hub, reopened)

    asyncio.run(scenario())


def test_a_completed_removal_is_recovered_as_removed(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, control=FakeControl())
        created = await started_instance(hub_client(hub), hub)
        await removed(hub_client(hub), created)
        hub.close()

        reopened = hub_at(tmp_path / "hub", runtime=runtime)
        recovery = await reopened.recover()

        assert [instance.state for instance in recovery.instances] == [RecoveredState.REMOVED]
        assert "retained" in recovery.instances[0].detail
        assert runtime.started == [str(created.instance_id)]
        assert len(runtime.created) == 1

    asyncio.run(scenario())


def test_a_removal_interrupted_before_the_container_went_is_reported_and_repeatable(tmp_path):
    async def scenario() -> None:
        runtime = CrashingRemoval(after_remove=False)
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        created = await created_instance(hub_client(hub))
        interrupted = await hub_client(hub).call(
            INSTANCE_REMOVE,
            InstanceRemoveCommand(instance_id=created.instance_id),
        )
        assert isinstance(interrupted, LifecycleOperationResult)
        await asyncio.wait_for(runtime.crashed.wait(), timeout=5)
        await asyncio.sleep(0)
        hub.close()

        reopened = hub_at(tmp_path / "hub", runtime=runtime)
        recovery = await reopened.recover()
        client = hub_client(reopened)
        failed = await client.call(
            OPERATION_GET,
            OperationGetCommand(operation_id=interrupted.operation_id),
        )
        active = await listed(client)
        again = await removed(client, created)

        assert [instance.state for instance in recovery.instances] == [RecoveredState.INCOMPLETE]
        assert "Remove the instance again" in recovery.instances[0].detail
        assert not isinstance(failed, ErrorEnvelope)
        assert failed.state is OperationState.FAILED
        assert [step.name for step in failed.steps] == ["remove"]
        assert [summary.instance_id for summary in active] == [created.instance_id]
        assert runtime.started == []
        assert again.state is OperationState.SUCCEEDED
        assert runtime.removed == [(str(created.instance_id), False)]

    asyncio.run(scenario())


def test_a_removal_interrupted_after_the_container_went_is_recorded_not_repeated(tmp_path):
    async def scenario() -> None:
        runtime = CrashingRemoval(after_remove=True)
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        created = await created_instance(hub_client(hub))
        interrupted = await hub_client(hub).call(
            INSTANCE_REMOVE,
            InstanceRemoveCommand(instance_id=created.instance_id),
        )
        assert isinstance(interrupted, LifecycleOperationResult)
        await asyncio.wait_for(runtime.crashed.wait(), timeout=5)
        await asyncio.sleep(0)
        hub.close()

        reopened = hub_at(tmp_path / "hub", runtime=runtime)
        recovery = await reopened.recover()
        again = await reopened.recover()
        client = hub_client(reopened)
        failed = await client.call(
            OPERATION_GET,
            OperationGetCommand(operation_id=interrupted.operation_id),
        )

        assert [instance.state for instance in recovery.instances] == [RecoveredState.REMOVED]
        assert "Removal reached" in recovery.instances[0].detail
        assert [instance.state for instance in again.instances] == [RecoveredState.REMOVED]
        assert "retained" in again.instances[0].detail
        assert not isinstance(failed, ErrorEnvelope)
        assert failed.state is OperationState.FAILED
        assert await listed(client) == []
        assert [summary.instance_id for summary in await listed(client, removed=True)] == [
            created.instance_id
        ]
        assert runtime.removed == [(str(created.instance_id), False)]

    asyncio.run(scenario())


def test_a_restoration_interrupted_before_its_container_is_reported_and_repeatable(tmp_path):
    async def scenario() -> None:
        runtime = CrashingRestoration(after_create=False)
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        created = await created_instance(hub_client(hub))
        await removed(hub_client(hub), created)
        runtime.armed = True
        interrupted = await hub_client(hub).call(
            INSTANCE_RESTORE,
            InstanceRestoreCommand(instance_id=created.instance_id),
        )
        assert isinstance(interrupted, LifecycleOperationResult)
        await asyncio.wait_for(runtime.crashed.wait(), timeout=5)
        await asyncio.sleep(0)
        hub.close()

        reopened = hub_at(tmp_path / "hub", runtime=runtime)
        recovery = await reopened.recover()
        client = hub_client(reopened)
        outcome = await restored(client, created)

        assert [instance.state for instance in recovery.instances] == [RecoveredState.REMOVED]
        assert "Restore the instance again" in recovery.instances[0].detail
        assert outcome.state is OperationState.SUCCEEDED
        assert len(runtime.created) == 2
        assert [summary.instance_id for summary in await listed(client)] == [created.instance_id]

    asyncio.run(scenario())


def test_a_restoration_interrupted_after_its_container_is_recorded_not_repeated(tmp_path):
    async def scenario() -> None:
        runtime = CrashingRestoration(after_create=True)
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        created = await created_instance(hub_client(hub))
        await removed(hub_client(hub), created)
        runtime.armed = True
        await hub_client(hub).call(
            INSTANCE_RESTORE,
            InstanceRestoreCommand(instance_id=created.instance_id),
        )
        await asyncio.wait_for(runtime.crashed.wait(), timeout=5)
        await asyncio.sleep(0)
        hub.close()

        reopened = hub_at(tmp_path / "hub", runtime=runtime)
        recovery = await reopened.recover()
        client = hub_client(reopened)
        [summary] = await listed(client)

        assert [instance.state for instance in recovery.instances] == [RecoveredState.STOPPED]
        assert "Restoration reached" in recovery.instances[0].detail
        assert summary.instance_id == created.instance_id
        assert summary.intended_state is IntendedState.STOPPED
        assert len(runtime.created) == 2
        assert runtime.started == []

    asyncio.run(scenario())


def test_recovery_never_claims_a_foreign_container_for_a_removed_instance(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        created = await created_instance(hub_client(hub))
        await removed(hub_client(hub), created)
        hub.close()
        runtime_id = str(created.instance_id)
        runtime.states[runtime_id] = RuntimeStatus("created", None)
        runtime.descriptions[runtime_id] = ContainerDescription(
            runtime_id=runtime_id,
            image="sha256:someone-else",
            owner=ContainerOwner.UNMANAGED,
            owner_name="",
            storage=(),
        )

        reopened = hub_at(tmp_path / "hub", runtime=runtime)
        recovery = await reopened.recover()

        assert [instance.state for instance in recovery.instances] == [RecoveredState.CONFLICTED]
        assert "this hub did not create it" in recovery.instances[0].detail
        assert await listed(hub_client(reopened)) == []

    asyncio.run(scenario())


def test_an_unauthorized_removal_or_restoration_has_no_effect(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        created = await created_instance(hub_client(hub))

        refused = [
            await hub.dispatcher.dispatch(
                method.name,
                {"instance_id": str(created.instance_id)},
                {Scope.HUB_READ},
            )
            for method in (INSTANCE_REMOVE, INSTANCE_RESTORE)
        ]

        for envelope in refused:
            assert isinstance(envelope, ErrorEnvelope)
            assert envelope.code is ErrorCode.PERMISSION_DENIED
        assert runtime.removed == []
        assert len(runtime.created) == 1

    asyncio.run(scenario())
