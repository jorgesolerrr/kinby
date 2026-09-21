"""Lifecycle recovery: what a reopened hub does with each managed instance, and what it reports."""

import asyncio
import sqlite3
from pathlib import Path
from uuid import uuid4

from kinby.contracts import (
    INSTANCE_CREATE,
    INSTANCE_LIST,
    INSTANCE_START,
    INSTANCE_STATUS,
    INSTANCE_STOP,
    OPERATION_GET,
    ErrorEnvelope,
    InstanceCreateCommand,
    InstanceListCommand,
    InstanceStartCommand,
    InstanceStatusCommand,
    InstanceStopCommand,
    LifecycleOperationResult,
    OperationGetCommand,
    OperationState,
    ProcessState,
    StorageKind,
)
from kinby.hub import (
    InstanceSpec,
    RecoveredInstance,
    RecoveredState,
    RuntimeStatus,
)
from tests.test_hub import (
    FakeControl,
    FakeImages,
    FakeRuntime,
    UnavailableRuntime,
    created_instance,
    finished_operation,
    hub_at,
    hub_client,
    started_instance,
)


def test_reopening_a_hub_keeps_a_running_instance_alive(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        created = await started_instance(hub_client(hub), hub)
        hub.close()

        reopened = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        recovery = await reopened.recover()

        assert recovery.instances == (
            RecoveredInstance(
                instance_id=created.instance_id,
                state=RecoveredState.RUNNING,
                detail="The container is still running.",
            ),
        )
        assert recovery.unknown_containers == ()
        assert runtime.started == [str(created.instance_id)]

    asyncio.run(scenario())


def test_a_container_the_machine_left_stopped_is_started_back_to_its_intended_state(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        client = hub_client(hub)
        created = await started_instance(client, hub)
        hub.close()
        runtime.states[str(created.instance_id)] = RuntimeStatus("stopped", None)

        reopened = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        recovery = await reopened.recover()
        status = await hub_client(reopened).call(
            INSTANCE_STATUS,
            InstanceStatusCommand(instance_id=created.instance_id),
        )

        assert [instance.state for instance in recovery.instances] == [RecoveredState.STARTED]
        assert runtime.started == [str(created.instance_id)] * 2
        assert not isinstance(status, ErrorEnvelope)
        assert status.process is ProcessState.RUNNING
        assert status.active_operation_id is None

    asyncio.run(scenario())


def test_an_instance_intended_stopped_is_left_stopped(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        created = await created_instance(hub_client(hub))
        hub.close()

        reopened = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        recovery = await reopened.recover()

        assert recovery.instances == (
            RecoveredInstance(
                instance_id=created.instance_id,
                state=RecoveredState.STOPPED,
                detail="The container is stopped, as intended.",
            ),
        )
        assert runtime.started == []

    asyncio.run(scenario())


def test_discovery_reports_a_missing_container_and_never_recreates_it(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        created = await started_instance(hub_client(hub), hub)
        hub.close()
        runtime.states.pop(str(created.instance_id))

        reopened = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        recovery = await reopened.recover()

        assert recovery.instances == (
            RecoveredInstance(
                instance_id=created.instance_id,
                state=RecoveredState.MISSING,
                detail="The container is gone. Recreate it to bring it back.",
            ),
        )
        assert len(runtime.created) == 1
        assert runtime.started == [str(created.instance_id)]

    asyncio.run(scenario())


def test_discovery_reports_a_container_it_does_not_own_without_adopting_it(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        created = await started_instance(hub_client(hub), hub)
        hub.close()
        runtime.states["a-stranger"] = RuntimeStatus("running", True)

        reopened = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        recovery = await reopened.recover()
        listed = await hub_client(reopened).call(INSTANCE_LIST, InstanceListCommand())

        assert recovery.unknown_containers == ("a-stranger",)
        assert [instance.instance_id for instance in recovery.instances] == [created.instance_id]
        assert not isinstance(listed, ErrorEnvelope)
        assert [summary.runtime_id for summary in listed.instances] == [str(created.instance_id)]

    asyncio.run(scenario())


def test_recovery_distinguishes_an_unhealthy_instance_from_an_unavailable_runtime(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        created = await started_instance(hub_client(hub), hub)
        hub.close()
        runtime.states[str(created.instance_id)] = RuntimeStatus("running", False, "unhealthy")

        unhealthy = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        reported = await unhealthy.recover()
        unhealthy.close()
        unavailable = hub_at(tmp_path / "hub", runtime=UnavailableRuntime(), images=FakeImages())
        unreachable = await unavailable.recover()

        assert reported.instances == (
            RecoveredInstance(
                instance_id=created.instance_id,
                state=RecoveredState.UNHEALTHY,
                detail="The container is running and the instance reports itself unhealthy.",
            ),
        )
        assert [instance.state for instance in unreachable.instances] == [
            RecoveredState.UNAVAILABLE
        ]
        assert "Docker daemon unavailable" in unreachable.instances[0].detail
        assert runtime.started == [str(created.instance_id)]

    asyncio.run(scenario())


def test_recovery_reports_storage_another_instance_owns_and_starts_nothing(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        created = await started_instance(hub_client(hub), hub)
        record = hub.registry.instance(created.instance_id)
        assert record is not None
        volume = next(item for item in record.storage if item.kind is StorageKind.VOLUME)
        hub.close()
        runtime.states[str(created.instance_id)] = RuntimeStatus("stopped", None)
        claim_storage(hub.directory / "registry.sqlite", volume.source)

        reopened = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        recovery = await reopened.recover()

        conflicted = next(
            instance
            for instance in recovery.instances
            if instance.instance_id == created.instance_id
        )
        assert conflicted.state is RecoveredState.CONFLICTED
        assert volume.source in conflicted.detail
        assert runtime.started == [str(created.instance_id)]

    asyncio.run(scenario())


class CrashingRuntime(FakeRuntime):
    """Die the way a killed hub process does, before or after the container effect."""

    def __init__(self, *, after_create: bool) -> None:
        super().__init__()
        self._after_create = after_create
        self.crashed = asyncio.Event()

    async def create(self, spec: InstanceSpec) -> None:
        if self._after_create:
            await super().create(spec)
        self.crashed.set()
        raise asyncio.CancelledError


class RefusingRuntime(FakeRuntime):
    """Refuse every start, the way Docker refuses a container it cannot mount."""

    async def start(self, instance_id: str) -> None:
        self.started.append(instance_id)
        raise RuntimeError("container start refused")


def test_a_create_interrupted_after_its_container_is_recorded_not_repeated(tmp_path):
    async def scenario() -> None:
        runtime = CrashingRuntime(after_create=True)
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        accepted = await hub_client(hub).call(
            INSTANCE_CREATE,
            InstanceCreateCommand(manifest_id="alice", model="openai:gpt-5"),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        await asyncio.wait_for(runtime.crashed.wait(), timeout=5)
        await asyncio.sleep(0)
        hub.close()

        reopened = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        recovery = await reopened.recover()
        client = hub_client(reopened)
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        interrupted = await client.call(
            OPERATION_GET,
            OperationGetCommand(operation_id=accepted.operation_id),
        )

        assert [instance.state for instance in recovery.instances] == [RecoveredState.STOPPED]
        assert "Creation reached this instance's container" in recovery.instances[0].detail
        assert len(runtime.created) == 1
        assert not isinstance(listed, ErrorEnvelope)
        assert [summary.instance_id for summary in listed.instances] == [accepted.instance_id]
        assert not isinstance(interrupted, ErrorEnvelope)
        assert interrupted.state is OperationState.FAILED
        assert [step.name for step in interrupted.steps] == ["configure", "image", "container"]

    asyncio.run(scenario())


def test_a_create_interrupted_before_its_container_stays_incomplete(tmp_path):
    async def scenario() -> None:
        runtime = CrashingRuntime(after_create=False)
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        await hub_client(hub).call(
            INSTANCE_CREATE,
            InstanceCreateCommand(manifest_id="alice", model="openai:gpt-5"),
        )
        await asyncio.wait_for(runtime.crashed.wait(), timeout=5)
        await asyncio.sleep(0)
        hub.close()

        reopened = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        recovery = await reopened.recover()
        listed = await hub_client(reopened).call(INSTANCE_LIST, InstanceListCommand())

        assert [instance.state for instance in recovery.instances] == [RecoveredState.INCOMPLETE]
        assert runtime.created == []
        assert not isinstance(listed, ErrorEnvelope)
        assert listed.instances == []

    asyncio.run(scenario())


def test_an_unfinished_stop_keeps_its_progress_and_is_not_started_again(tmp_path):
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
        hub.close()

        reopened = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        recovery = await reopened.recover()
        interrupted = await hub_client(reopened).call(
            OPERATION_GET,
            OperationGetCommand(operation_id=stopping.operation_id),
        )

        assert [instance.state for instance in recovery.instances] == [RecoveredState.UNSTOPPED]
        assert "Stop it again" in recovery.instances[0].detail
        assert runtime.started == [str(created.instance_id)]
        assert not isinstance(interrupted, ErrorEnvelope)
        assert interrupted.state is OperationState.FAILED
        assert interrupted.detail == "The hub stopped before this operation finished."
        assert [step.name for step in interrupted.steps] == ["probe", "drain"]

    asyncio.run(scenario())


def test_a_start_that_failed_is_reported_and_not_repeated(tmp_path):
    async def scenario() -> None:
        runtime = RefusingRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        client = hub_client(hub)
        created = await created_instance(client)
        refused = await client.call(
            INSTANCE_START,
            InstanceStartCommand(instance_id=created.instance_id),
        )
        assert isinstance(refused, LifecycleOperationResult)
        assert (await finished_operation(client, refused)).state is OperationState.FAILED
        hub.close()

        reopened = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages())
        recovery = await reopened.recover()

        assert [instance.state for instance in recovery.instances] == [RecoveredState.FAILED]
        assert "The last start operation failed" in recovery.instances[0].detail
        assert runtime.started == [str(created.instance_id)]

    asyncio.run(scenario())


def claim_storage(registry_path: Path, source: str) -> None:
    """Let a second record own this source, the way a registry restored from a backup can."""
    other = uuid4()
    with sqlite3.connect(registry_path) as connection:
        connection.execute(
            """
            INSERT INTO instances (
                id, path, manifest_id, requested_revision, intended_state, runtime_id, prepared
            ) VALUES (?, ?, 'other', 'HEAD', 'stopped', ?, 1)
            """,
            (str(other), f"/nowhere/{other}", str(other)),
        )
        connection.execute(
            """
            INSERT INTO storage (instance_id, kind, source, destination, writable)
            VALUES (?, 'volume', ?, '/instance/workspace', 1)
            """,
            (str(other), source),
        )
