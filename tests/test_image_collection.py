"""Collecting the recorded images that no instance, update candidate or container refers to."""

import asyncio
from pathlib import Path

from kinby.contracts import (
    IMAGE_PREPARE,
    INSTANCE_UPDATE,
    DeclaredSetup,
    ImagePrepareCommand,
    ImagePrepareResult,
    InstanceUpdateCommand,
    LifecycleOperationResult,
    OperationState,
)
from kinby.hub import Hub, HubRegistry, ImagePreparer
from tests.test_hub import (
    FakeControl,
    FakeRuntime,
    created_instance,
    finished_operation,
    hub_client,
    prepared,
)
from tests.test_hub_deletion import deleted, previewed
from tests.test_hub_recreate import CrashOnReplacement
from tests.test_hub_removal import removed
from tests.test_image_preparation import FakeImageBackend, _git, _source_repo


def _hub(tmp_path: Path, backend: FakeImageBackend, runtime: FakeRuntime | None = None) -> Hub:
    """A hub that builds its images from a checkout at tmp_path/source, made once."""
    source = tmp_path / "source"
    if not source.exists():
        source.mkdir()
        _source_repo(source)
    directory = tmp_path / "hub"
    return Hub(
        directory,
        runtime=runtime or FakeRuntime(),
        images=ImagePreparer(source, HubRegistry(directory), backend),
        control=FakeControl(),
    )


def _commit(tmp_path: Path, text: str) -> None:
    """Move the hub's checkout to a new commit, so the next build makes a new image."""
    source = tmp_path / "source"
    (source / "src" / "app.py").write_text(f"print({text!r})\n", encoding="utf-8")
    _git(source, "commit", "-am", text)


async def _settled(hub: Hub) -> None:
    """Wait out the collection that follows each create, update and delete."""
    await asyncio.gather(*hub._tasks)


async def _updated(hub: Hub, created: LifecycleOperationResult) -> None:
    client = hub_client(hub)
    accepted = await client.call(
        INSTANCE_UPDATE,
        InstanceUpdateCommand(instance_id=created.instance_id, revision="HEAD"),
    )
    assert isinstance(accepted, LifecycleOperationResult)
    assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED
    await _settled(hub)


def test_an_accepted_update_removes_the_previous_image_and_every_record_naming_it(tmp_path):
    async def scenario() -> None:
        backend = FakeImageBackend()
        hub = _hub(tmp_path, backend)
        created = await created_instance(hub_client(hub))
        previous = hub.registry.image_artifacts()[0]
        # Docker gives one image id to two builds whose inputs differ but whose layers do not.
        hub.registry.record_image_artifact("another-input-key", previous)
        _commit(tmp_path, "updated")

        await _updated(hub, created)

        assert backend.removed == ["sha256:image-1"]
        assert [artifact.image_id for artifact in hub.registry.image_artifacts()] == [
            "sha256:image-2"
        ]
        assert hub.registry.description("sha256:image-1") is None

    asyncio.run(scenario())


def test_an_image_another_instance_uses_stays_after_an_update(tmp_path):
    async def scenario() -> None:
        backend = FakeImageBackend()
        hub = _hub(tmp_path, backend)
        created = await created_instance(hub_client(hub))
        await created_instance(hub_client(hub), name="bob")
        _commit(tmp_path, "updated")

        await _updated(hub, created)

        assert backend.removed == []
        assert backend.images == {"sha256:image-1", "sha256:image-2"}

    asyncio.run(scenario())


def test_a_removed_instance_keeps_its_image_until_it_is_deleted(tmp_path):
    async def scenario() -> None:
        backend = FakeImageBackend()
        hub = _hub(tmp_path, backend)
        client = hub_client(hub)
        created = await created_instance(client)
        assert (await removed(client, created)).state is OperationState.SUCCEEDED
        _commit(tmp_path, "another")
        await created_instance(client)
        await _settled(hub)

        assert backend.removed == []

        assert (await deleted(client, await previewed(client, created))).state is (
            OperationState.SUCCEEDED
        )
        await _settled(hub)

        assert backend.removed == ["sha256:image-1"]

    asyncio.run(scenario())


def test_deleting_an_instance_frees_the_candidate_its_failed_update_staged(tmp_path):
    async def scenario() -> None:
        backend = FakeImageBackend()
        runtime = FakeRuntime()
        hub = _hub(tmp_path, backend, runtime)
        client = hub_client(hub)
        created = await created_instance(client)
        _commit(tmp_path, "candidate")
        runtime.create_failure = RuntimeError("The replacement could not be created.")
        update = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(instance_id=created.instance_id, revision="HEAD"),
        )
        assert isinstance(update, LifecycleOperationResult)
        assert (await finished_operation(client, update)).state is OperationState.FAILED
        assert hub.registry.candidate_image(created.instance_id) == "sha256:image-2"
        runtime.create_failure = None

        assert (await removed(client, created)).state is OperationState.SUCCEEDED
        assert (await deleted(client, await previewed(client, created))).state is (
            OperationState.SUCCEEDED
        )
        await _settled(hub)

        assert sorted(backend.removed) == ["sha256:image-1", "sha256:image-2"]

    asyncio.run(scenario())


def test_recovery_keeps_a_staged_candidate_and_removes_an_unused_prepared_image(tmp_path):
    async def scenario() -> None:
        backend = FakeImageBackend()
        runtime = CrashOnReplacement()
        hub = _hub(tmp_path, backend, runtime)
        client = hub_client(hub)
        created = await created_instance(client)
        _commit(tmp_path, "prepared")
        await prepared(client)
        _commit(tmp_path, "candidate")
        interrupted = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(instance_id=created.instance_id, revision="HEAD"),
        )
        assert isinstance(interrupted, LifecycleOperationResult)
        await asyncio.wait_for(runtime.crashed.wait(), timeout=5)
        await asyncio.sleep(0)
        hub.close()

        reopened = _hub(tmp_path, backend, runtime)
        await reopened.recover()
        await _settled(reopened)

        assert reopened.registry.candidate_image(created.instance_id) == "sha256:image-3"
        assert backend.removed == ["sha256:image-2"]

    asyncio.run(scenario())


def test_recovery_removes_the_inspection_containers_a_previous_hub_left(tmp_path):
    async def scenario() -> None:
        backend = FakeImageBackend()
        backend.inspection_containers = 2
        hub = _hub(tmp_path, backend)

        await hub.recover()
        await _settled(hub)

        assert backend.inspection_containers == 0

    asyncio.run(scenario())


def test_an_image_docker_refuses_to_remove_keeps_its_records_and_fails_no_update(tmp_path):
    async def scenario() -> None:
        backend = FakeImageBackend()
        backend.refused.add("sha256:image-1")
        hub = _hub(tmp_path, backend)
        created = await created_instance(hub_client(hub))
        _commit(tmp_path, "updated")

        await _updated(hub, created)

        assert backend.removed == []
        assert [artifact.image_id for artifact in hub.registry.image_artifacts()] == [
            "sha256:image-1",
            "sha256:image-2",
        ]
        assert hub.registry.description("sha256:image-1") is not None

    asyncio.run(scenario())


class HeldDescription(FakeImageBackend):
    """Hold the next description until the test releases it, so a preparation stays unfinished."""

    def __init__(self) -> None:
        super().__init__()
        self.holding = asyncio.Event()
        self.released = asyncio.Event()

    async def describe(self, image_id: str) -> DeclaredSetup:
        if self.described:
            self.holding.set()
            await self.released.wait()
        return await super().describe(image_id)


def test_nothing_is_collected_while_a_preparation_holds_an_image_no_record_names(tmp_path):
    async def scenario() -> None:
        backend = HeldDescription()
        hub = _hub(tmp_path, backend)
        client = hub_client(hub)
        created = await created_instance(client)
        assert (await removed(client, created)).state is OperationState.SUCCEEDED
        _commit(tmp_path, "prepared")
        preparing = await client.call(IMAGE_PREPARE, ImagePrepareCommand())
        assert isinstance(preparing, ImagePrepareResult)
        await asyncio.wait_for(backend.holding.wait(), timeout=5)

        assert (await deleted(client, await previewed(client, created))).state is (
            OperationState.SUCCEEDED
        )
        await asyncio.sleep(0.05)

        assert backend.removed == []

        backend.released.set()
        assert (await finished_operation(client, preparing)).state is OperationState.SUCCEEDED

    asyncio.run(scenario())
