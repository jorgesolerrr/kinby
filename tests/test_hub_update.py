"""Updating a managed instance from an explicitly selected, prepared immutable image."""

import asyncio
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from kinby.cli.client import ContractClient
from kinby.contracts import (
    INSTANCE_CREATE,
    INSTANCE_LIST,
    INSTANCE_RECREATE,
    INSTANCE_START,
    INSTANCE_STATUS,
    INSTANCE_STOP,
    INSTANCE_UPDATE,
    OPERATION_GET,
    ErrorCode,
    ErrorEnvelope,
    InstanceCreateCommand,
    InstanceListCommand,
    InstanceListResult,
    InstanceRecreateCommand,
    InstanceStartCommand,
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
    PackageCommit,
    PackageDescription,
    PackagePin,
    PackageSelection,
    PackageSummary,
    ProcessState,
    Scope,
    StorageItem,
)
from kinby.hub import (
    Hub,
    ImageArtifact,
    ImageSelection,
    InstanceSpec,
    PreparedImage,
    RecoveredState,
    RuntimeStatus,
)
from kinby.packages import (
    InstalledPackage,
    PackageDescriptor,
    RequiredSecret,
    installed_package_from_json,
    package_description,
)
from tests.fake_package import VALID_CONFIG, install_fake_package
from tests.test_hub import (
    FakeControl,
    FakeImages,
    FakeRuntime,
    created_instance,
    finished_operation,
    hub_at,
    hub_client,
    prepared,
    started_instance,
)
from tests.test_hub_recreate import CrashOnReplacement


class CandidateImages(FakeImages):
    """One distinct immutable image per selected revision, so a replacement is visible."""

    async def prepare(
        self,
        selection: ImageSelection,
        instance: StorageItem | None = None,
    ) -> PreparedImage:
        prepared = await super().prepare(selection, instance)
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
        # The preparation and the creation built HEAD, then the update built its revision.
        assert [selection.revision for selection in images.selections] == [
            "HEAD",
            "HEAD",
            "v0.2.0",
        ]
        assert control.forces == [False]
        assert runtime.removed == [(str(created.instance_id), False)]
        assert len(runtime.created) == 2
        assert runtime.created[1].image == "sha256:v0.2.0-image"
        assert runtime.created[1].storage == runtime.created[0].storage
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

    async def prepare(
        self,
        selection: ImageSelection,
        instance: StorageItem | None = None,
    ) -> PreparedImage:
        # The instance was created from HEAD. The update's candidate is the first other revision.
        if selection.revision != "HEAD":
            self.crashed.set()
            raise asyncio.CancelledError
        return await super().prepare(selection, instance)


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
        before = await client.call(INSTANCE_LIST, InstanceListCommand())
        again = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(instance_id=created.instance_id, revision="v0.2.0"),
        )
        assert isinstance(again, LifecycleOperationResult)
        outcome = await finished_operation(client, again)

        assert [instance.state for instance in recovery.instances] == [RecoveredState.MISSING]
        assert "update" in recovery.instances[0].detail
        assert isinstance(before, InstanceListResult)
        assert before.instances[0].image_id == "sha256:HEAD-image"
        assert outcome.state is OperationState.SUCCEEDED
        assert [step.name for step in outcome.steps] == ["validate", "image", "replace", "create"]
        assert len(runtime.created) == 2
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert isinstance(listed, InstanceListResult)
        assert listed.instances[0].image_id == "sha256:v0.2.0-image"

    asyncio.run(scenario())


class CrashAfterReplacement(FakeRuntime):
    """Die once the replacement container exists and before its image is recorded."""

    def __init__(self) -> None:
        super().__init__()
        self.crashed = asyncio.Event()

    async def create(self, spec: InstanceSpec) -> None:
        await super().create(spec)
        if len(self.created) > 1 and not self.crashed.is_set():
            self.crashed.set()
            raise asyncio.CancelledError


def test_an_interruption_after_the_replacement_exists_keeps_that_image(tmp_path):
    async def scenario() -> None:
        runtime = CrashAfterReplacement()
        images = CandidateImages()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images, control=FakeControl())
        created = await started_instance(hub_client(hub), hub)
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
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        again = await client.call(
            INSTANCE_RECREATE,
            InstanceRecreateCommand(instance_id=created.instance_id),
        )
        assert isinstance(again, LifecycleOperationResult)
        outcome = await finished_operation(client, again)

        assert [instance.state for instance in recovery.instances] == [RecoveredState.FAILED]
        assert "update" in recovery.instances[0].detail
        assert isinstance(listed, InstanceListResult)
        assert listed.instances[0].image_id == "sha256:v0.2.0-image"
        assert listed.instances[0].source_revision == "v0.2.0-resolved"
        assert outcome.state is OperationState.SUCCEEDED
        assert runtime.created[-1].image == "sha256:v0.2.0-image"
        assert runtime.created[-1].storage == runtime.created[0].storage

    asyncio.run(scenario())


def writer_package() -> InstalledPackage:
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
    await prepared(client, selection)
    created = await client.call(
        INSTANCE_CREATE,
        InstanceCreateCommand(
            manifest_id="editor",
            model="openai:gpt-5",
            package=selection,
            secrets={"api_key": "sk-test", "EDITOR_TOKEN": "private-editor-token"},
        ),
    )
    assert isinstance(created, LifecycleOperationResult)
    assert (await finished_operation(client, created)).state is OperationState.SUCCEEDED
    return created


def test_an_update_carries_the_package_selection_and_keeps_the_owned_configuration(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        images = CandidateImages(package=writer_package())
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
        images = CandidateImages(package=writer_package())
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


class CheckedCandidates(CandidateImages):
    """Run the candidate check as the image does: another Python with the package installed.

    The instance directory stands in for the read-only mount the Docker backend adds.
    """

    def __init__(self, site: Path, bin_directory: Path) -> None:
        super().__init__()
        self.environment = {
            **os.environ,
            "PATH": f"{bin_directory}:{os.path.dirname(sys.executable)}",
            "PYTHONPATH": str(site),
        }

    def _check(self, mounted: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "kinby.packages", "writer", *mounted],
            capture_output=True,
            text=True,
            env=self.environment,
            check=False,
        )

    async def _checked(self, mounted: list[str]) -> InstalledPackage:
        checked = await asyncio.to_thread(self._check, mounted)
        if checked.returncode != 0:
            raise ValueError(checked.stderr.strip())
        return installed_package_from_json(checked.stdout)

    async def prepare(
        self,
        selection: ImageSelection,
        instance: StorageItem | None = None,
    ) -> PreparedImage:
        prepared = await super().prepare(selection, instance)
        mounted = [] if instance is None else [_checked_directory(instance)]
        return replace(prepared, package=await self._checked(mounted))

    async def describe(self, artifact: ImageArtifact) -> PackageDescription:
        return package_description(await self._checked([]))


def _checked_directory(instance: StorageItem) -> str:
    """The directory the check reads, when the hub handed it only package.yaml."""
    source = Path(instance.source)
    if source.name == "package.yaml":
        return str(source.parent)
    return instance.source


async def _checked(
    client: ContractClient,
    accepted: LifecycleOperationResult,
) -> OperationGetResult:
    """Wait out an operation whose candidate check starts a separate Python."""
    async with asyncio.timeout(60):
        while True:
            result = await client.call(
                OPERATION_GET,
                OperationGetCommand(operation_id=accepted.operation_id),
            )
            assert isinstance(result, OperationGetResult)
            if result.state in {OperationState.SUCCEEDED, OperationState.FAILED}:
                return result
            await asyncio.sleep(0.05)


@pytest.mark.parametrize("failure", ["edited config", "candidate check"])
def test_a_failing_candidate_stops_the_update_before_the_container_stops(tmp_path, failure):
    async def scenario() -> None:
        package = install_fake_package(tmp_path / "site", executables=("kinby-fake-editor",))
        editor = tmp_path / "bin" / "kinby-fake-editor"
        editor.parent.mkdir()
        editor.write_text("#!/bin/sh\n", encoding="utf-8")
        editor.chmod(0o755)
        control = FakeControl()
        runtime = FakeRuntime()
        images = CheckedCandidates(package.site, editor.parent)
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images, control=control)
        client = hub_client(hub)
        selection = PackageSelection(id="writer", distribution=package.module, version="1.4.2")
        await prepared(client, selection)
        created = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="editor",
                model="openai:gpt-5",
                package=selection,
                secrets={"api_key": "sk-test", "EDITOR_TOKEN": "private-editor-token"},
            ),
        )
        assert isinstance(created, LifecycleOperationResult)
        assert (await _checked(client, created)).state is OperationState.SUCCEEDED
        started = await client.call(
            INSTANCE_START,
            InstanceStartCommand(instance_id=created.instance_id),
        )
        assert isinstance(started, LifecycleOperationResult)
        assert (await finished_operation(client, started)).state is OperationState.SUCCEEDED
        config = hub.instances_directory / str(created.instance_id) / "package.yaml"
        assert config.read_text(encoding="utf-8") == VALID_CONFIG
        if failure == "edited config":
            config.write_text(f"{VALID_CONFIG}tonne: formal\n", encoding="utf-8")
            expected = f"{config}: tonne: Extra inputs are not permitted"
        else:
            editor.unlink()
            expected = 'Executable "kinby-fake-editor" is not on PATH.'

        accepted = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(instance_id=created.instance_id, revision="v0.2.0"),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await _checked(client, accepted)

        assert outcome.state is OperationState.FAILED
        assert outcome.detail == expected
        assert [step.name for step in outcome.steps] == ["validate", "image"]
        assert control.forces == []
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
        assert images.selections == [ImageSelection(revision="HEAD")] * 2
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
        assert images.selections == [ImageSelection(revision="HEAD")] * 2
        assert len(runtime.created) == 1

    asyncio.run(scenario())


WRITER_REPOSITORY = "https://github.com/example/kinby-writer"
FIRST_COMMIT = "1" * 40
NEXT_COMMIT = "2" * 40


def writer_at(sha: str) -> PackageSelection:
    return PackageSelection(
        id="writer",
        distribution="kinby-writer",
        version=PackageCommit(url=WRITER_REPOSITORY, sha=sha),
        image_recipe="RUN install-writing-client\n",
    )


async def started_writer(client: ContractClient, hub: Hub) -> LifecycleOperationResult:
    created = await _packaged_instance(client, writer_at(FIRST_COMMIT))
    started = await client.call(
        INSTANCE_START, InstanceStartCommand(instance_id=created.instance_id)
    )
    assert isinstance(started, LifecycleOperationResult)
    assert (await finished_operation(client, started)).state is OperationState.SUCCEEDED
    runtime = hub._runtime
    assert isinstance(runtime, FakeRuntime)
    runtime.addresses[str(created.instance_id)] = f"http://kinby-{created.instance_id}:8787"
    return created


async def _package_of(client: ContractClient) -> PackageSummary | None:
    listed = await client.call(INSTANCE_LIST, InstanceListCommand())
    assert isinstance(listed, InstanceListResult)
    return listed.instances[0].package


def test_a_package_pin_moves_the_package_to_that_commit_once_the_replacement_is_up(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        images = CandidateImages(package=writer_package())
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images, control=FakeControl())
        client = hub_client(hub)
        created = await started_writer(client, hub)
        behavior = hub.instances_directory / str(created.instance_id) / "SYSTEM.md"
        behavior.write_text("Write the way I taught you.\n", encoding="utf-8")

        accepted = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(
                instance_id=created.instance_id,
                revision="v0.2.0",
                package=PackagePin(id="writer", sha=NEXT_COMMIT),
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)
        hub.close()
        reopened = hub_client(hub_at(tmp_path / "hub", runtime=runtime, images=images))

        assert outcome.state is OperationState.SUCCEEDED, outcome.detail
        assert outcome.detail == "Instance updated."
        assert images.selections[-1] == ImageSelection("v0.2.0", writer_at(NEXT_COMMIT))
        assert runtime.created[-1].image == "sha256:v0.2.0-image"
        assert behavior.read_text(encoding="utf-8") == "Write the way I taught you.\n"
        package = await _package_of(reopened)
        assert package is not None
        assert package.version == PackageCommit(url=WRITER_REPOSITORY, sha=NEXT_COMMIT)

    asyncio.run(scenario())


def test_an_update_without_a_pin_carries_the_package_commit_along(tmp_path):
    async def scenario() -> None:
        images = CandidateImages(package=writer_package())
        hub = hub_at(tmp_path / "hub", images=images, control=FakeControl())
        client = hub_client(hub)
        created = await started_writer(client, hub)

        accepted = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(instance_id=created.instance_id, revision="v0.2.0"),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)

        assert outcome.state is OperationState.SUCCEEDED, outcome.detail
        assert images.selections[-1] == ImageSelection("v0.2.0", writer_at(FIRST_COMMIT))
        package = await _package_of(client)
        assert package is not None
        assert package.version == PackageCommit(url=WRITER_REPOSITORY, sha=FIRST_COMMIT)

    asyncio.run(scenario())


def test_a_pin_with_an_image_recipe_builds_with_that_recipe_from_then_on(tmp_path):
    async def scenario() -> None:
        images = CandidateImages(package=writer_package())
        hub = hub_at(tmp_path / "hub", images=images, control=FakeControl())
        client = hub_client(hub)
        created = await started_writer(client, hub)
        recipe = "RUN install-writing-client\nRUN install-bun\n"

        pinned = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(
                instance_id=created.instance_id,
                revision="v0.2.0",
                package=PackagePin(id="writer", sha=NEXT_COMMIT, image_recipe=recipe),
            ),
        )
        assert isinstance(pinned, LifecycleOperationResult)
        assert (await finished_operation(client, pinned)).state is OperationState.SUCCEEDED
        unpinned = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(instance_id=created.instance_id, revision="v0.3.0"),
        )
        assert isinstance(unpinned, LifecycleOperationResult)
        assert (await finished_operation(client, unpinned)).state is OperationState.SUCCEEDED

        with_recipe = writer_at(NEXT_COMMIT).model_copy(update={"image_recipe": recipe})
        assert images.selections[-2] == ImageSelection("v0.2.0", with_recipe)
        assert images.selections[-1] == ImageSelection("v0.3.0", with_recipe)

    asyncio.run(scenario())


def test_a_failed_update_keeps_the_previous_pin(tmp_path):
    async def scenario() -> None:
        runtime = FailingBoot()
        images = CandidateImages(package=writer_package())
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images, control=FakeControl())
        client = hub_client(hub)
        created = await started_writer(client, hub)

        accepted = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(
                instance_id=created.instance_id,
                revision="v0.2.0",
                package=PackagePin(id="writer", sha=NEXT_COMMIT),
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)

        assert outcome.state is OperationState.FAILED
        assert "exited (1)" in outcome.detail
        assert images.selections[-1].package == writer_at(NEXT_COMMIT)
        package = await _package_of(client)
        assert package is not None
        assert package.version == PackageCommit(url=WRITER_REPOSITORY, sha=FIRST_COMMIT)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("selection", "pin", "refusal"),
    [
        (
            writer_at(FIRST_COMMIT),
            PackagePin(id="coder", sha=NEXT_COMMIT),
            'runs package "writer", not "coder"',
        ),
        (
            None,
            PackagePin(id="writer", sha=NEXT_COMMIT),
            'runs no package, not "writer"',
        ),
        (
            PackageSelection(id="writer", distribution="kinby-writer", version="1.4.2"),
            PackagePin(id="writer", sha=NEXT_COMMIT),
            'Package "writer" is installed from the package index',
        ),
    ],
)
def test_a_pin_must_move_the_instances_current_git_package(tmp_path, selection, pin, refusal):
    async def scenario() -> None:
        runtime = FakeRuntime()
        images = CandidateImages(package=writer_package() if selection else None)
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images)
        client = hub_client(hub)
        if selection is None:
            created = await created_instance(client)
        else:
            created = await _packaged_instance(client, selection)

        refused = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(instance_id=created.instance_id, revision="v0.2.0", package=pin),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert refusal in refused.message
        assert len(images.selections) == 2
        assert len(runtime.created) == 1

    asyncio.run(scenario())
