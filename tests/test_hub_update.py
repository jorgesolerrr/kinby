"""Updating a managed instance from an explicitly selected, prepared immutable image."""

import asyncio
from dataclasses import replace

from kinby.cli.client import ContractClient
from kinby.contracts import (
    INSTANCE_CREATE,
    INSTANCE_LIST,
    INSTANCE_STATUS,
    INSTANCE_STOP,
    INSTANCE_UPDATE,
    OPERATION_GET,
    ErrorCode,
    ErrorEnvelope,
    InstanceCreateCommand,
    InstanceListCommand,
    InstanceListResult,
    InstanceStatusCommand,
    InstanceStatusResult,
    InstanceStopCommand,
    InstanceUpdateCommand,
    IntendedState,
    LifecycleOperationResult,
    OperationGetCommand,
    OperationGetResult,
    OperationKind,
    OperationState,
    PackageSelection,
    ProcessState,
    Scope,
)
from kinby.hub import ImageSelection, PreparedImage, RecoveredState, RuntimeStatus
from kinby.packages import InstalledPackage, PackageDescriptor, RequiredSecret
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
from tests.test_hub_recreate import CrashOnReplacement


class CandidateImages(FakeImages):
    """One distinct immutable image per selected revision, so a replacement is visible."""

    async def prepare(self, selection: ImageSelection) -> PreparedImage:
        prepared = await super().prepare(selection)
        return replace(
            prepared,
            artifact=replace(
                prepared.artifact,
                image_id=f"sha256:{selection.revision}-image",
                revision=f"{selection.revision}-resolved",
            ),
        )


def test_an_update_drains_the_instance_and_replaces_it_with_the_prepared_image(tmp_path):
    async def scenario() -> None:
        control = FakeControl()
        runtime = FakeRuntime()
        images = CandidateImages()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images, control=control)
        client = hub_client(hub)
        created = await started_instance(client, hub)
        memory = hub.instances_directory / str(created.instance_id) / ".state" / "graph.sqlite"
        memory.parent.mkdir(parents=True, exist_ok=True)
        memory.write_text("remembered\n", encoding="utf-8")

        accepted = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(instance_id=created.instance_id, revision="v0.2.0"),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)

        assert outcome.kind is OperationKind.UPDATE
        assert outcome.state is OperationState.SUCCEEDED
        assert outcome.detail == "Instance updated."
        assert [step.name for step in outcome.steps] == [
            "validate",
            "image",
            "replace",
            "probe",
            "drain",
            "result",
            "container",
            "remove",
            "create",
            "start",
            "ready",
        ]
        assert [selection.revision for selection in images.selections] == ["HEAD", "v0.2.0"]
        assert control.forces == [False]
        assert runtime.removed == [(str(created.instance_id), False)]
        assert len(runtime.created) == 2
        assert runtime.created[1].image == "sha256:v0.2.0-image"
        assert runtime.started == [str(created.instance_id)] * 2
        assert memory.read_text(encoding="utf-8") == "remembered\n"

        retained = next(step for step in outcome.steps if step.name == "replace")
        assert "sha256:HEAD-image" in retained.detail
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert isinstance(listed, InstanceListResult)
        summary = listed.instances[0]
        assert summary.image_id == "sha256:v0.2.0-image"
        assert summary.source_revision == "v0.2.0-resolved"
        assert summary.intended_state is IntendedState.RUNNING

    asyncio.run(scenario())


def test_a_candidate_that_cannot_be_prepared_leaves_the_instance_running_on_its_image(tmp_path):
    async def scenario() -> None:
        control = FakeControl()
        runtime = FakeRuntime()
        images = CandidateImages()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images, control=control)
        client = hub_client(hub)
        created = await started_instance(client, hub)
        images.failure = "no such revision: v9.9.9"

        accepted = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(instance_id=created.instance_id, revision="v9.9.9"),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)

        assert outcome.state is OperationState.FAILED
        assert outcome.detail == "no such revision: v9.9.9"
        assert [step.name for step in outcome.steps] == ["validate", "image"]
        assert control.forces == []
        assert runtime.removed == []
        assert len(runtime.created) == 1
        status = await client.call(
            INSTANCE_STATUS,
            InstanceStatusCommand(instance_id=created.instance_id),
        )
        assert isinstance(status, InstanceStatusResult)
        assert status.process is ProcessState.RUNNING
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert isinstance(listed, InstanceListResult)
        assert listed.instances[0].image_id == "sha256:HEAD-image"

    asyncio.run(scenario())


def test_updating_a_stopped_instance_leaves_it_stopped(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        images = CandidateImages()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images)
        client = hub_client(hub)
        created = await created_instance(client)

        accepted = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(instance_id=created.instance_id, revision="v0.2.0"),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)

        assert outcome.state is OperationState.SUCCEEDED
        assert outcome.detail == "Instance updated and left stopped."
        assert [step.name for step in outcome.steps] == [
            "validate",
            "image",
            "replace",
            "remove",
            "create",
        ]
        assert runtime.started == []
        assert runtime.created[1].image == "sha256:v0.2.0-image"
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert isinstance(listed, InstanceListResult)
        assert listed.instances[0].image_id == "sha256:v0.2.0-image"
        assert listed.instances[0].intended_state is IntendedState.STOPPED

    asyncio.run(scenario())


class FailingBoot(FakeRuntime):
    """Let the replacement container exit the way an image that cannot run does."""

    def __init__(self) -> None:
        super().__init__()
        self.boots = 0

    async def start(self, instance_id: str) -> None:
        self.boots += 1
        if self.boots == 1:
            await super().start(instance_id)
            return
        self.started.append(instance_id)
        self.states[instance_id] = RuntimeStatus("failed", None, "exited (1)")


def test_a_replacement_that_does_not_boot_fails_and_starts_no_older_image(tmp_path):
    async def scenario() -> None:
        runtime = FailingBoot()
        images = CandidateImages()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images, control=FakeControl())
        client = hub_client(hub)
        created = await started_instance(client, hub)

        accepted = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(instance_id=created.instance_id, revision="v0.2.0"),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)

        assert outcome.state is OperationState.FAILED
        assert "exited (1)" in outcome.detail
        assert [step.name for step in outcome.steps][-1] == "ready"
        assert outcome.steps[-1].state is OperationState.FAILED
        assert len(runtime.created) == 2
        assert runtime.created[1].image == "sha256:v0.2.0-image"
        assert runtime.started == [str(created.instance_id)] * 2
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert isinstance(listed, InstanceListResult)
        assert listed.instances[0].image_id == "sha256:v0.2.0-image"

        hub.close()
        reopened = hub_at(tmp_path / "hub", runtime=runtime, images=images)
        recovery = await reopened.recover()

        assert [instance.state for instance in recovery.instances] == [RecoveredState.FAILED]
        assert "update" in recovery.instances[0].detail
        assert runtime.started == [str(created.instance_id)] * 2

    asyncio.run(scenario())


class CrashOnCandidate(CandidateImages):
    """Die the way a killed hub process does, while the candidate is being prepared."""

    def __init__(self) -> None:
        super().__init__()
        self.crashed = asyncio.Event()

    async def prepare(self, selection: ImageSelection) -> PreparedImage:
        if self.selections:
            self.crashed.set()
            raise asyncio.CancelledError
        return await super().prepare(selection)


def test_an_interruption_while_the_candidate_is_prepared_leaves_the_instance_running(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        images = CrashOnCandidate()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images, control=FakeControl())
        created = await started_instance(hub_client(hub), hub)
        interrupted = await hub_client(hub).call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(instance_id=created.instance_id, revision="v0.2.0"),
        )
        assert isinstance(interrupted, LifecycleOperationResult)
        await asyncio.wait_for(images.crashed.wait(), timeout=5)
        await asyncio.sleep(0)
        hub.close()

        reopened = hub_at(tmp_path / "hub", runtime=runtime, images=images)
        recovery = await reopened.recover()
        client = hub_client(reopened)
        failed = await client.call(
            OPERATION_GET,
            OperationGetCommand(operation_id=interrupted.operation_id),
        )

        assert [instance.state for instance in recovery.instances] == [RecoveredState.RUNNING]
        assert isinstance(failed, OperationGetResult)
        assert failed.state is OperationState.FAILED
        assert failed.detail == "The hub stopped before this operation finished."
        assert [step.name for step in failed.steps] == ["validate", "image"]
        assert runtime.removed == []
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert isinstance(listed, InstanceListResult)
        assert listed.instances[0].image_id == "sha256:HEAD-image"

    asyncio.run(scenario())


def test_an_interruption_around_the_replacement_is_visible_and_the_update_can_be_asked_again(
    tmp_path,
):
    async def scenario() -> None:
        runtime = CrashOnReplacement()
        images = CandidateImages()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images, control=FakeControl())
        created = await created_instance(hub_client(hub))
        interrupted = await hub_client(hub).call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(instance_id=created.instance_id, revision="v0.2.0"),
        )
        assert isinstance(interrupted, LifecycleOperationResult)
        await asyncio.wait_for(runtime.crashed.wait(), timeout=5)
        await asyncio.sleep(0)
        hub.close()

        reopened = hub_at(tmp_path / "hub", runtime=runtime, images=images)
        recovery = await reopened.recover()
        client = hub_client(reopened)
        again = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(instance_id=created.instance_id, revision="v0.2.0"),
        )
        assert isinstance(again, LifecycleOperationResult)
        outcome = await finished_operation(client, again)

        assert [instance.state for instance in recovery.instances] == [RecoveredState.MISSING]
        assert "update" in recovery.instances[0].detail
        assert outcome.state is OperationState.SUCCEEDED
        assert [step.name for step in outcome.steps] == ["validate", "image", "replace", "create"]
        assert len(runtime.created) == 2
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert isinstance(listed, InstanceListResult)
        assert listed.instances[0].image_id == "sha256:v0.2.0-image"

    asyncio.run(scenario())


def _writer_package() -> InstalledPackage:
    return InstalledPackage(
        descriptor=PackageDescriptor(
            id="writer",
            display_name="Writing teammate",
            description="Drafts and edits articles.",
            icon="pen",
            distribution="kinby-writer",
            version="1.4.2",
            required_secrets=(
                RequiredSecret(
                    name="EDITOR_TOKEN",
                    label="Editor token",
                    description="Authenticates the editor service.",
                ),
            ),
        ),
        files={"SYSTEM.md": "You are an exacting editor.\n"},
    )


async def _packaged_instance(
    client: ContractClient,
    selection: PackageSelection,
) -> LifecycleOperationResult:
    created = await client.call(
        INSTANCE_CREATE,
        InstanceCreateCommand(
            manifest_id="editor",
            model="openai:gpt-5",
            package=selection,
            secrets={"EDITOR_TOKEN": "private-editor-token"},
        ),
    )
    assert isinstance(created, LifecycleOperationResult)
    assert (await finished_operation(client, created)).state is OperationState.SUCCEEDED
    return created


def test_an_update_carries_the_package_selection_and_keeps_the_owned_configuration(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        images = CandidateImages(package=_writer_package())
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images)
        client = hub_client(hub)
        selection = PackageSelection(id="writer", distribution="kinby-writer", version="1.4.2")
        created = await _packaged_instance(client, selection)
        behavior = hub.instances_directory / str(created.instance_id) / "SYSTEM.md"
        behavior.write_text("Write the way I taught you.\n", encoding="utf-8")

        accepted = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(instance_id=created.instance_id, revision="v0.2.0"),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)

        assert outcome.state is OperationState.SUCCEEDED
        assert images.selections[-1].package == selection
        assert behavior.read_text(encoding="utf-8") == "Write the way I taught you.\n"
        assert runtime.created[1].image == "sha256:v0.2.0-image"
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert isinstance(listed, InstanceListResult)
        assert listed.instances[0].package is not None
        assert listed.instances[0].package.version == "1.4.2"

    asyncio.run(scenario())


def test_a_candidate_without_the_instance_package_leaves_the_container_alone(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        images = CandidateImages(package=_writer_package())
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images)
        client = hub_client(hub)
        created = await _packaged_instance(
            client,
            PackageSelection(id="writer", distribution="kinby-writer", version="1.4.2"),
        )
        images.package = None

        accepted = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(instance_id=created.instance_id, revision="v0.2.0"),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)

        assert outcome.state is OperationState.FAILED
        assert outcome.detail == 'Package "writer" was not found in the prepared image.'
        assert runtime.removed == []
        assert len(runtime.created) == 1

    asyncio.run(scenario())


def test_an_update_is_refused_while_another_operation_owns_the_instance(tmp_path):
    async def scenario() -> None:
        control = FakeControl(holds=True)
        runtime = FakeRuntime()
        images = CandidateImages()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images, control=control)
        client = hub_client(hub)
        created = await started_instance(client, hub)
        stopping = await client.call(
            INSTANCE_STOP,
            InstanceStopCommand(instance_id=created.instance_id),
        )
        assert isinstance(stopping, LifecycleOperationResult)
        await asyncio.wait_for(control.asked.wait(), timeout=5)

        refused = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(instance_id=created.instance_id, revision="v0.2.0"),
        )
        control.release.set()
        assert (await finished_operation(client, stopping)).state is OperationState.SUCCEEDED

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INSTANCE_BUSY
        assert refused.retryable
        assert images.selections == [ImageSelection(revision="HEAD")]
        assert len(runtime.created) == 1

    asyncio.run(scenario())


def test_an_unauthorized_update_has_no_effect(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        images = CandidateImages()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images)
        created = await started_instance(hub_client(hub), hub)

        refused = await hub.dispatcher.dispatch(
            INSTANCE_UPDATE.name,
            {"instance_id": str(created.instance_id), "revision": "v0.2.0"},
            {Scope.HUB_READ},
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.PERMISSION_DENIED
        assert images.selections == [ImageSelection(revision="HEAD")]
        assert len(runtime.created) == 1

    asyncio.run(scenario())
