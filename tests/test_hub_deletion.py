"""Deletion previews a removed instance's owned storage, then deletes exactly that storage."""

import asyncio
import shutil
from uuid import uuid4

import pytest

from kinby.cli.client import ContractClient
from kinby.contracts import (
    INSTANCE_ADOPT,
    INSTANCE_ADOPT_PREVIEW,
    INSTANCE_DELETE,
    INSTANCE_DELETE_PREVIEW,
    INSTANCE_RESTORE,
    OPERATION_GET,
    AdoptionFindingKind,
    ContainerOwner,
    ErrorCode,
    ErrorEnvelope,
    InstanceAdoptCommand,
    InstanceAdoptPreviewCommand,
    InstanceDeleteCommand,
    InstanceDeletePreviewCommand,
    InstanceDeletePreviewResult,
    InstanceRestoreCommand,
    LifecycleOperationResult,
    OperationGetCommand,
    OperationGetResult,
    OperationKind,
    OperationState,
    Scope,
    StorageItem,
    StorageKind,
)
from kinby.hub import ContainerDescription, Hub, RecoveredState
from tests.test_hub import (
    FakeControl,
    FakeImages,
    FakeRuntime,
    created_instance,
    finished_operation,
    hub_at,
    hub_client,
)
from tests.test_hub_adoption import CODER_CONTAINER, adopted, coder_storage, existing_coder
from tests.test_hub_recovery import claim_storage
from tests.test_hub_removal import HeldCreate, listed, removed, storage_of


async def previewed(
    client: ContractClient,
    instance: LifecycleOperationResult,
) -> InstanceDeletePreviewResult:
    result = await client.call(
        INSTANCE_DELETE_PREVIEW,
        InstanceDeletePreviewCommand(instance_id=instance.instance_id),
    )
    assert isinstance(result, InstanceDeletePreviewResult)
    return result


async def deleted(
    client: ContractClient,
    preview: InstanceDeletePreviewResult,
) -> OperationGetResult:
    accepted = await client.call(
        INSTANCE_DELETE,
        InstanceDeleteCommand(
            instance_id=preview.instance_id,
            directories=preview.directories,
            volumes=preview.volumes,
        ),
    )
    assert isinstance(accepted, LifecycleOperationResult)
    assert accepted.instance_id == preview.instance_id
    return await finished_operation(client, accepted)


def volumes_of(hub: Hub, instance: LifecycleOperationResult) -> list[str]:
    return [item.source for item in storage_of(hub, instance) if item.kind is StorageKind.VOLUME]


def test_a_preview_lists_the_removed_instance_s_resolved_directory_and_named_volumes(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        created = await created_instance(hub_client(hub))
        await removed(hub_client(hub), created)
        instance_path = hub.instances_directory / str(created.instance_id)

        preview = await previewed(hub_client(hub, {Scope.HUB_READ}), created)

        assert preview.instance_id == created.instance_id
        assert preview.directories == [instance_path.resolve()]
        assert preview.volumes == [
            f"kinby-{created.instance_id}-workspace",
            f"kinby-{created.instance_id}-codex",
        ]
        assert instance_path.is_dir()
        assert runtime.deleted_volumes == []

    asyncio.run(scenario())


def test_a_deletion_deletes_exactly_the_previewed_targets_and_forgets_the_removed_record(
    tmp_path,
):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        client = hub_client(hub)
        created = await created_instance(client)
        instance_path = hub.instances_directory / str(created.instance_id)
        external = tmp_path / "notes"
        external.mkdir()
        (external / "plan.md").write_text("the user's own work\n", encoding="utf-8")
        with (instance_path / "kinby.toml").open("a", encoding="utf-8") as handle:
            handle.write(f'\n[workspace]\npath = "{external}"\n')
        volumes = volumes_of(hub, created)
        await removed(client, created)
        preview = await previewed(client, created)

        outcome = await deleted(client, preview)

        assert outcome.kind is OperationKind.DELETE
        assert outcome.state is OperationState.SUCCEEDED
        assert [step.name for step in outcome.steps] == [
            "validate",
            f"directory {instance_path.resolve()}",
            f"volume {volumes[0]}",
            f"volume {volumes[1]}",
        ]
        assert all(step.state is OperationState.SUCCEEDED for step in outcome.steps)
        assert not instance_path.exists()
        assert runtime.deleted_volumes == volumes
        assert (external / "plan.md").read_text(encoding="utf-8") == "the user's own work\n"
        assert hub.instances_directory.is_dir()
        assert await listed(client) == []
        assert await listed(client, removed=True) == []

    asyncio.run(scenario())


def test_only_a_removed_instance_previews_or_takes_a_deletion(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        client = hub_client(hub)
        created = await created_instance(client)
        instance_path = hub.instances_directory / str(created.instance_id)

        preview = await client.call(
            INSTANCE_DELETE_PREVIEW,
            InstanceDeletePreviewCommand(instance_id=created.instance_id),
        )
        deletion = await client.call(
            INSTANCE_DELETE,
            InstanceDeleteCommand(
                instance_id=created.instance_id,
                directories=[instance_path.resolve()],
                volumes=volumes_of(hub, created),
            ),
        )

        for refused in (preview, deletion):
            assert isinstance(refused, ErrorEnvelope)
            assert refused.code is ErrorCode.NOT_FOUND
        assert instance_path.is_dir()
        assert runtime.deleted_volumes == []
        assert [summary.instance_id for summary in await listed(client)] == [created.instance_id]

    asyncio.run(scenario())


def test_an_unauthorized_preview_or_deletion_has_no_effect(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        created = await created_instance(hub_client(hub))
        await removed(hub_client(hub), created)
        preview = await previewed(hub_client(hub), created)

        refused = [
            await hub.dispatcher.dispatch(
                INSTANCE_DELETE_PREVIEW.name,
                {"instance_id": str(created.instance_id)},
                set(),
            ),
            await hub.dispatcher.dispatch(
                INSTANCE_DELETE.name,
                preview.model_dump(mode="json"),
                {Scope.HUB_READ},
            ),
        ]

        for envelope in refused:
            assert isinstance(envelope, ErrorEnvelope)
            assert envelope.code is ErrorCode.PERMISSION_DENIED
        assert all(path.is_dir() for path in preview.directories)
        assert runtime.deleted_volumes == []
        assert await previewed(hub_client(hub), created) == preview

    asyncio.run(scenario())


def test_a_target_that_changed_since_the_preview_refuses_the_deletion(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        client = hub_client(hub)
        created = await created_instance(client)
        await removed(client, created)
        stale = await previewed(client, created)
        instance_path = hub.instances_directory / str(created.instance_id)
        elsewhere = tmp_path / "elsewhere"
        instance_path.rename(elsewhere)
        instance_path.symlink_to(elsewhere, target_is_directory=True)

        outcome = await deleted(client, stale)

        assert outcome.state is OperationState.FAILED
        assert "Preview the deletion again" in outcome.detail
        assert [step.name for step in outcome.steps] == ["validate"]
        assert (elsewhere / "kinby.toml").exists()
        assert runtime.deleted_volumes == []
        fresh = await previewed(client, created)
        assert fresh.directories == [elsewhere.resolve()]
        assert fresh.volumes == stale.volumes

    asyncio.run(scenario())


@pytest.mark.parametrize("owner_state", ["stopped", "removed"])
def test_a_volume_another_active_or_removed_instance_retains_refuses_the_deletion(
    tmp_path,
    owner_state,
):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        client = hub_client(hub)
        created = await created_instance(client)
        await removed(client, created)
        [workspace, _] = volumes_of(hub, created)
        owner = claim_storage(
            hub.directory / "registry.sqlite",
            workspace,
            intended_state=owner_state,
        )
        preview = await previewed(client, created)

        outcome = await deleted(client, preview)

        assert outcome.state is OperationState.FAILED
        assert workspace in outcome.detail
        assert str(owner) in outcome.detail
        assert [step.name for step in outcome.steps] == ["validate"]
        assert all(path.is_dir() for path in preview.directories)
        assert runtime.deleted_volumes == []
        assert await previewed(client, created) == preview

    asyncio.run(scenario())


@pytest.mark.parametrize("shape", ["alias", "inside", "around", "read-only"])
def test_a_directory_another_record_mounts_refuses_the_deletion(tmp_path, shape):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        client = hub_client(hub)
        created = await created_instance(client)
        await removed(client, created)
        instance_path = hub.instances_directory / str(created.instance_id)
        alias = tmp_path / "alias"
        alias.symlink_to(instance_path, target_is_directory=True)
        source = {
            "alias": alias,
            "inside": instance_path / "workspace-notes",
            "around": hub.instances_directory,
            "read-only": instance_path,
        }[shape]
        owner = claim_storage(
            hub.directory / "registry.sqlite",
            str(source),
            intended_state="removed",
            kind=StorageKind.BIND,
            writable=shape != "read-only",
        )
        preview = await previewed(client, created)

        outcome = await deleted(client, preview)

        assert outcome.state is OperationState.FAILED
        assert str(owner) in outcome.detail
        assert [step.name for step in outcome.steps] == ["validate"]
        assert (instance_path / "kinby.toml").exists()
        assert runtime.deleted_volumes == []

    asyncio.run(scenario())


class RefusingVolume(FakeRuntime):
    """Refuse to delete one named volume, the way Docker refuses a volume still in use."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.refused = name

    async def delete_volume(self, name: str) -> None:
        if name == self.refused:
            raise RuntimeError(f"volume {name} is in use")
        await super().delete_volume(name)


def test_a_partial_deletion_keeps_what_failed_and_a_retry_never_reaches_past_what_went(
    tmp_path,
):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        created = await created_instance(hub_client(hub))
        [workspace, codex] = volumes_of(hub, created)
        refusing = RefusingVolume(codex)
        hub.close()
        hub = hub_at(tmp_path / "hub", runtime=refusing)
        client = hub_client(hub)
        await removed(client, created)
        instance_path = hub.instances_directory / str(created.instance_id)
        first = await previewed(client, created)

        partial = await deleted(client, first)

        assert partial.state is OperationState.FAILED
        assert f"volume {codex} is in use" in partial.detail
        assert [(step.name, step.state) for step in partial.steps] == [
            ("validate", OperationState.SUCCEEDED),
            (f"directory {instance_path.resolve()}", OperationState.SUCCEEDED),
            (f"volume {workspace}", OperationState.SUCCEEDED),
            (f"volume {codex}", OperationState.FAILED),
        ]
        assert not instance_path.exists()
        assert [summary.instance_id for summary in await listed(client, removed=True)] == [
            created.instance_id
        ]

        # Unrelated storage appears under names the deleted targets had.
        instance_path.mkdir()
        (instance_path / "someone-else.txt").write_text("not the instance's\n", encoding="utf-8")
        refusing.missing.discard(workspace)
        refusing.refused = ""
        second = await previewed(client, created)
        stale = await deleted(client, first)
        retried = await deleted(client, second)

        assert second.directories == []
        assert second.volumes == [codex]
        assert stale.state is OperationState.FAILED
        assert "Preview the deletion again" in stale.detail
        assert retried.state is OperationState.SUCCEEDED
        assert refusing.deleted_volumes == [workspace, codex]
        assert (instance_path / "someone-else.txt").exists()
        assert await listed(client, removed=True) == []

    asyncio.run(scenario())


def test_a_directory_that_fails_to_go_keeps_every_target_for_a_retry(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        client = hub_client(hub)
        created = await created_instance(client)
        await removed(client, created)
        instance_path = hub.instances_directory / str(created.instance_id)
        preview = await previewed(client, created)
        shutil.rmtree(instance_path)
        instance_path.write_text("a file where the directory was\n", encoding="utf-8")

        failed = await deleted(client, preview)

        assert failed.state is OperationState.FAILED
        assert failed.steps[-1].name == f"directory {instance_path.resolve()}"
        assert failed.steps[-1].state is OperationState.FAILED
        assert runtime.deleted_volumes == []
        assert await previewed(client, created) == preview

        instance_path.unlink()
        retried = await deleted(client, preview)

        assert retried.state is OperationState.SUCCEEDED
        assert runtime.deleted_volumes == preview.volumes
        assert await listed(client, removed=True) == []

    asyncio.run(scenario())


class CrashingDeletion(FakeRuntime):
    """Die once the way a killed hub process does, right after a volume went."""

    def __init__(self) -> None:
        super().__init__()
        self.armed = False
        self.crashed = asyncio.Event()

    async def delete_volume(self, name: str) -> None:
        await super().delete_volume(name)
        if self.armed:
            self.armed = False
            self.crashed.set()
            raise asyncio.CancelledError


def test_a_deletion_interrupted_by_a_restart_is_inspectable_and_finishes_on_retry(tmp_path):
    async def scenario() -> None:
        runtime = CrashingDeletion()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        created = await created_instance(hub_client(hub))
        [workspace, codex] = volumes_of(hub, created)
        await removed(hub_client(hub), created)
        instance_path = hub.instances_directory / str(created.instance_id)
        preview = await previewed(hub_client(hub), created)
        runtime.armed = True
        interrupted = await hub_client(hub).call(
            INSTANCE_DELETE,
            InstanceDeleteCommand(
                instance_id=created.instance_id,
                directories=preview.directories,
                volumes=preview.volumes,
            ),
        )
        assert isinstance(interrupted, LifecycleOperationResult)
        await asyncio.wait_for(runtime.crashed.wait(), timeout=5)
        await asyncio.sleep(0)
        hub.close()

        reopened = hub_at(tmp_path / "hub", runtime=runtime)
        recovery = await reopened.recover()
        client = hub_client(reopened)
        failed = await finished_operation(client, interrupted)
        remaining = await previewed(client, created)

        assert [instance.state for instance in recovery.instances] == [RecoveredState.REMOVED]
        assert "Preview the deletion again" in recovery.instances[0].detail
        assert failed.state is OperationState.FAILED
        assert [(step.name, step.state) for step in failed.steps] == [
            ("validate", OperationState.SUCCEEDED),
            (f"directory {instance_path.resolve()}", OperationState.SUCCEEDED),
            (f"volume {workspace}", OperationState.FAILED),
        ]
        assert remaining.directories == []
        assert remaining.volumes == [workspace, codex]

        retried = await deleted(client, remaining)
        again = await reopened.recover()

        assert retried.state is OperationState.SUCCEEDED
        assert runtime.deleted_volumes == [workspace, workspace, codex]
        assert again.instances == ()
        assert await listed(client, removed=True) == []
        for refused in (
            await client.call(
                INSTANCE_DELETE_PREVIEW,
                InstanceDeletePreviewCommand(instance_id=created.instance_id),
            ),
            await client.call(
                INSTANCE_RESTORE,
                InstanceRestoreCommand(instance_id=created.instance_id),
            ),
        ):
            assert isinstance(refused, ErrorEnvelope)
            assert refused.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_an_adopted_instance_deletes_its_directory_where_the_hub_sees_it_and_no_read_only_mount(
    tmp_path,
):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=FakeControl())
        client = hub_client(hub)
        shared_login = tmp_path / "login"
        shared_login.mkdir()
        storage = (
            *coder_storage("/srv/kinby/instances/coder")[:3],
            StorageItem(
                kind=StorageKind.BIND,
                source=str(shared_login),
                destination="/anthropic-profile",
                writable=False,
            ),
        )
        directory = existing_coder(runtime, tmp_path / "view" / "coder", storage=storage)
        instance_id = await adopted(client, directory, CODER_CONTAINER)
        instance = LifecycleOperationResult(operation_id=uuid4(), instance_id=instance_id)
        await removed(client, instance)

        preview = await previewed(client, instance)
        outcome = await deleted(client, preview)

        assert preview.directories == [directory.resolve()]
        assert preview.volumes == ["kinby_coder-workspace", "kinby_coder-codex"]
        assert outcome.state is OperationState.SUCCEEDED
        assert not directory.exists()
        assert shared_login.is_dir()
        assert runtime.deleted_volumes == preview.volumes

    asyncio.run(scenario())


def test_a_container_under_the_removed_instance_s_name_refuses_the_deletion(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        client = hub_client(hub)
        created = await created_instance(client)
        await removed(client, created)
        preview = await previewed(client, created)
        runtime_id = str(created.instance_id)
        runtime.descriptions[runtime_id] = ContainerDescription(
            runtime_id=runtime_id,
            image="sha256:someone-else",
            owner=ContainerOwner.UNMANAGED,
            owner_name="",
            storage=(),
        )

        outcome = await deleted(client, preview)

        assert outcome.state is OperationState.FAILED
        assert f'container "{runtime_id}" still exists' in outcome.detail
        assert [step.name for step in outcome.steps] == ["validate"]
        assert all(path.is_dir() for path in preview.directories)
        assert runtime.deleted_volumes == []

    asyncio.run(scenario())


class HeldVolume(FakeRuntime):
    """Hold the next volume deletion until the test releases it, so a deletion stays in flight."""

    def __init__(self) -> None:
        super().__init__()
        self.holding = asyncio.Event()
        self.released = asyncio.Event()

    async def delete_volume(self, name: str) -> None:
        self.holding.set()
        await self.released.wait()
        await super().delete_volume(name)


def test_a_deletion_in_flight_refuses_a_restoration_and_a_second_deletion(tmp_path):
    async def scenario() -> None:
        runtime = HeldVolume()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        client = hub_client(hub)
        created = await created_instance(client)
        await removed(client, created)
        preview = await previewed(client, created)
        command = InstanceDeleteCommand(
            instance_id=created.instance_id,
            directories=preview.directories,
            volumes=preview.volumes,
        )
        deleting = await client.call(INSTANCE_DELETE, command)
        assert isinstance(deleting, LifecycleOperationResult)
        await asyncio.wait_for(runtime.holding.wait(), timeout=5)

        restoring = await client.call(
            INSTANCE_RESTORE,
            InstanceRestoreCommand(instance_id=created.instance_id),
        )
        again = await client.call(INSTANCE_DELETE, command)
        runtime.released.set()
        outcome = await finished_operation(client, deleting)

        for refused in (restoring, again):
            assert isinstance(refused, ErrorEnvelope)
            assert refused.code is ErrorCode.INSTANCE_BUSY
        assert outcome.state is OperationState.SUCCEEDED
        assert len(runtime.created) == 1

    asyncio.run(scenario())


def test_a_restoration_in_flight_refuses_a_deletion(tmp_path):
    async def scenario() -> None:
        runtime = HeldCreate()
        hub = hub_at(tmp_path / "hub", runtime=runtime)
        client = hub_client(hub)
        created = await created_instance(client)
        await removed(client, created)
        preview = await previewed(client, created)
        runtime.hold()
        restoring = await client.call(
            INSTANCE_RESTORE,
            InstanceRestoreCommand(instance_id=created.instance_id),
        )
        assert isinstance(restoring, LifecycleOperationResult)
        await asyncio.wait_for(runtime.holding.wait(), timeout=5)

        refused = await client.call(
            INSTANCE_DELETE,
            InstanceDeleteCommand(
                instance_id=created.instance_id,
                directories=preview.directories,
                volumes=preview.volumes,
            ),
        )
        runtime.released.set()
        restored = await finished_operation(client, restoring)

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INSTANCE_BUSY
        assert restored.state is OperationState.SUCCEEDED
        assert all(path.is_dir() for path in preview.directories)
        assert runtime.deleted_volumes == []

    asyncio.run(scenario())


def test_a_deleted_container_name_blocks_a_replacement_adoption_as_a_finding(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=FakeControl())
        client = hub_client(hub)
        original = existing_coder(runtime, tmp_path / "box" / "coder")
        instance_id = await adopted(client, original, CODER_CONTAINER)
        instance = LifecycleOperationResult(operation_id=uuid4(), instance_id=instance_id)
        await removed(client, instance)
        deletion = await deleted(client, await previewed(client, instance))
        replacement = existing_coder(runtime, tmp_path / "box" / "replacement")

        preview = await client.call(
            INSTANCE_ADOPT_PREVIEW,
            InstanceAdoptPreviewCommand(
                path=replacement,
                runtime_id=CODER_CONTAINER,
                relinquished=True,
            ),
        )
        refused = await client.call(
            INSTANCE_ADOPT,
            InstanceAdoptCommand(
                path=replacement,
                runtime_id=CODER_CONTAINER,
                relinquished=True,
            ),
        )
        still_there = await client.call(
            OPERATION_GET,
            OperationGetCommand(operation_id=deletion.operation_id),
        )

        assert not isinstance(preview, ErrorEnvelope)
        identity = next(
            finding
            for finding in preview.findings
            if finding.kind is AdoptionFindingKind.RETAINED_IDENTITY
        )
        assert CODER_CONTAINER in identity.detail
        assert str(instance_id) in identity.detail
        assert identity.blocking
        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert refused.code is not ErrorCode.INTERNAL
        assert isinstance(still_there, OperationGetResult)
        assert still_there.state is OperationState.SUCCEEDED

    asyncio.run(scenario())


def test_a_deleted_hub_path_blocks_a_replacement_with_a_new_id_as_a_finding(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=FakeControl())
        client = hub_client(hub)
        directory = existing_coder(runtime, tmp_path / "view" / "coder")
        instance_id = await adopted(client, directory, CODER_CONTAINER)
        instance = LifecycleOperationResult(operation_id=uuid4(), instance_id=instance_id)
        await removed(client, instance)
        await deleted(client, await previewed(client, instance))
        unseen_host = "/srv/docker/replacement-coder"
        existing_coder(
            runtime,
            directory,
            container="kinby-coder-2",
            storage=coder_storage(unseen_host),
        )

        preview = await client.call(
            INSTANCE_ADOPT_PREVIEW,
            InstanceAdoptPreviewCommand(
                path=directory,
                runtime_id="kinby-coder-2",
                relinquished=True,
            ),
        )
        refused = await client.call(
            INSTANCE_ADOPT,
            InstanceAdoptCommand(
                path=directory,
                runtime_id="kinby-coder-2",
                relinquished=True,
            ),
        )

        assert not isinstance(preview, ErrorEnvelope)
        assert preview.instance_id != instance_id
        assert AdoptionFindingKind.RETAINED_IDENTITY in {
            finding.kind for finding in preview.findings
        }
        identity = next(
            finding
            for finding in preview.findings
            if finding.kind is AdoptionFindingKind.RETAINED_IDENTITY
        )
        assert str(directory.resolve()) in identity.detail
        assert str(instance_id) in identity.detail
        assert identity.blocking
        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT

    asyncio.run(scenario())
