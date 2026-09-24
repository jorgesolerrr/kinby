"""Adoption: the preflight that previews an existing instance, and the ownership handoff."""

import asyncio
from pathlib import Path
from uuid import UUID

import aiohttp
import pytest
from aiohttp import web

from kinby.cli.client import ContractClient
from kinby.contracts import (
    INSTANCE_ADOPT,
    INSTANCE_ADOPT_PREVIEW,
    INSTANCE_LIST,
    INSTANCE_RECREATE,
    INSTANCE_UPDATE,
    OPERATION_GET,
    AdoptionFindingKind,
    Capability,
    ContainerOwner,
    ErrorCode,
    ErrorEnvelope,
    InstanceAdoptCommand,
    InstanceAdoptPreviewCommand,
    InstanceListCommand,
    InstanceRecreateCommand,
    InstanceUpdateCommand,
    IntendedState,
    LifecycleOperationResult,
    OperationGetCommand,
    OperationKind,
    OperationState,
    PackageCommit,
    PackagePin,
    PackageSelection,
    StorageItem,
    StorageKind,
)
from kinby.core.contract_server import CONTROL_TOKEN_VARIABLE
from kinby.hub import ContainerDescription, InstanceSpec, RecoveredState, RuntimeStatus
from kinby.hub.service import STOP_GRACE_SECONDS
from kinby.instance import init_instance
from tests.test_hub import (
    FakeControl,
    FakeImages,
    FakeRuntime,
    finished_operation,
    hub_at,
    hub_client,
)
from tests.test_hub_recovery import claim_storage
from tests.test_hub_routing import SIGNATURE, recording_instance
from tests.test_hub_server import served, url

CODER_CONTAINER = "kinby-coder-1"
CODER_IMAGE = "sha256:coder-image"
COMPOSE_PROJECT = "kinby"
GITHUB_TOKEN = "ghp-existing"
OLD_CONTROL_TOKEN = "control-token-compose-gave-it"


def coder_storage(
    host_directory: Path | str,
    *,
    volumes: str = "kinby_coder",
) -> tuple[StorageItem, ...]:
    """The coder's layout: its directory, a workspace volume, a login volume, a read-only bind."""
    return (
        StorageItem(
            kind=StorageKind.BIND,
            source=str(host_directory),
            destination="/instance",
            writable=True,
        ),
        StorageItem(
            kind=StorageKind.VOLUME,
            source=f"{volumes}-workspace",
            destination="/instance/workspace",
            writable=True,
        ),
        StorageItem(
            kind=StorageKind.VOLUME,
            source=f"{volumes}-codex",
            destination="/root/.codex",
            writable=True,
        ),
        # The same host login, read only, in every coder: shared storage nobody writes.
        StorageItem(
            kind=StorageKind.BIND,
            source="/home/jorge/.config/Anthropic",
            destination="/anthropic-profile",
            writable=False,
        ),
    )


def coder_directory(
    path: Path,
    *,
    manifest_id: str = "coder",
    persona_name: str = "Ada",
    control_token: str = "",
) -> Path:
    """An instance directory as Compose left it: a manifest, and the secrets it was given."""
    init_instance(path, model="openai:gpt-5")
    manifest = path / "kinby.toml"
    lines = manifest.read_text(encoding="utf-8").splitlines()
    identity = [f'id = "{manifest_id}"', f'persona_name = "{persona_name}"']
    for index, line in enumerate(lines):
        if line.startswith("id = "):
            lines[index : index + 1] = identity
            break
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    secrets = f"GITHUB_TOKEN='{GITHUB_TOKEN}'\n"
    if control_token:
        secrets += f"{CONTROL_TOKEN_VARIABLE}='{control_token}'\n"
    (path / ".env").write_text(secrets, encoding="utf-8")
    return path


def existing_coder(
    runtime: FakeRuntime,
    path: Path,
    *,
    owner: ContainerOwner = ContainerOwner.COMPOSE,
    container: str = CODER_CONTAINER,
    running: bool = True,
    control_token: str = OLD_CONTROL_TOKEN,
    storage: tuple[StorageItem, ...] | None = None,
) -> Path:
    """Plant a coder the hub does not manage: its directory, its container and its storage."""
    directory = coder_directory(path, control_token=control_token)
    runtime.states[container] = (
        RuntimeStatus("running", True) if running else RuntimeStatus("stopped", None)
    )
    runtime.addresses[container] = f"http://{container}:8787"
    runtime.descriptions[container] = ContainerDescription(
        runtime_id=container,
        image=CODER_IMAGE,
        owner=owner,
        owner_name=COMPOSE_PROJECT if owner is ContainerOwner.COMPOSE else "",
        storage=storage if storage is not None else coder_storage(directory),
    )
    return directory


def test_a_preview_shows_the_identity_the_storage_and_the_owner_of_a_running_coder(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        control = FakeControl()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=control)
        directory = existing_coder(runtime, tmp_path / "box" / "coder")
        client = hub_client(hub)
        secrets = (directory / ".env").read_text(encoding="utf-8")

        previewed = await client.call(
            INSTANCE_ADOPT_PREVIEW,
            InstanceAdoptPreviewCommand(path=directory, runtime_id=CODER_CONTAINER),
        )
        again = await client.call(
            INSTANCE_ADOPT_PREVIEW,
            InstanceAdoptPreviewCommand(path=directory, runtime_id=CODER_CONTAINER),
        )

        assert not isinstance(previewed, ErrorEnvelope)
        assert not isinstance(again, ErrorEnvelope)
        assert previewed.manifest_id == "coder"
        assert previewed.persona_name == "Ada"
        assert previewed.path == directory
        assert previewed.runtime_id == CODER_CONTAINER
        assert previewed.image_id == CODER_IMAGE
        assert previewed.owner is ContainerOwner.COMPOSE
        assert previewed.owner_name == COMPOSE_PROJECT
        assert previewed.capabilities == [Capability.WS, Capability.DRAIN]
        assert previewed.storage == list(coder_storage(directory))
        # The hub instance id is its own, and the same instance previews as the same id.
        assert str(previewed.instance_id) not in {"coder", "Ada"}
        assert again.instance_id == previewed.instance_id
        assert [finding.kind for finding in previewed.findings] == [
            AdoptionFindingKind.PREVIOUS_MANAGER
        ]
        assert COMPOSE_PROJECT in previewed.findings[0].detail
        assert previewed.findings[0].blocking
        assert "drain" in previewed.handoff.downtime
        assert any("compose" in step.lower() for step in previewed.handoff.steps)
        assert any("update" in step.lower() for step in previewed.handoff.steps)
        assert "/signals/" in previewed.handoff.signals
        # A preflight reads. It starts no container, stops none, and registers nothing.
        assert runtime.created == []
        assert runtime.removed == []
        assert runtime.stopped == []
        assert hub.registry.managed_instances() == []
        assert (directory / ".env").read_text(encoding="utf-8") == secrets

    asyncio.run(scenario())


def test_the_handoff_drains_the_previous_runtime_before_it_starts_the_replacement(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        control = FakeControl()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=control)
        directory = existing_coder(runtime, tmp_path / "box" / "coder")
        client = hub_client(hub)

        accepted = await client.call(
            INSTANCE_ADOPT,
            InstanceAdoptCommand(
                path=directory,
                runtime_id=CODER_CONTAINER,
                relinquished=True,
                claim_signals=True,
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)

        assert outcome.kind is OperationKind.ADOPT
        assert outcome.state is OperationState.SUCCEEDED
        assert outcome.detail == "The hub owns this instance."
        # The previous runtime goes down before its replacement is built, never in parallel.
        assert [step.name for step in outcome.steps] == [
            "probe",
            "drain",
            "result",
            "container",
            "configure",
            "claim",
            "remove",
            "create",
            "start",
            "signals",
        ]
        assert control.forces == [False]
        # The drain went through the instance's own control token, over its own endpoint.
        assert control.endpoints[-1].token == OLD_CONTROL_TOKEN
        assert runtime.stopped == [STOP_GRACE_SECONDS]
        assert runtime.removed == [(CODER_CONTAINER, False)]
        # One replacement, never a second runtime beside the instance it took over.
        assert len(runtime.created) == 1
        spec = runtime.created[0]
        assert spec.instance_id == CODER_CONTAINER
        assert spec.image == CODER_IMAGE
        assert spec.storage == coder_storage(directory)
        assert spec.env["GITHUB_TOKEN"] == GITHUB_TOKEN
        assert spec.env[CONTROL_TOKEN_VARIABLE] not in {"", OLD_CONTROL_TOKEN}
        assert runtime.started == [CODER_CONTAINER]

        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert not isinstance(listed, ErrorEnvelope)
        summary = listed.instances[0]
        assert summary.instance_id == accepted.instance_id
        assert (summary.manifest_id, summary.persona_name) == ("coder", "Ada")
        assert summary.intended_state is IntendedState.RUNNING
        # The registry keeps every bind location and named volume, ordered by destination.
        assert summary.storage == sorted(
            coder_storage(directory), key=lambda item: item.destination
        )
        assert hub.registry.signal_alias() == accepted.instance_id
        secrets = (directory / ".env").read_text(encoding="utf-8")
        assert GITHUB_TOKEN in secrets
        assert OLD_CONTROL_TOKEN not in secrets

    asyncio.run(scenario())


def test_the_handoff_is_refused_while_the_previous_manager_still_owns_the_container(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=FakeControl())
        directory = existing_coder(runtime, tmp_path / "box" / "coder")

        refused = await hub_client(hub).call(
            INSTANCE_ADOPT,
            InstanceAdoptCommand(path=directory, runtime_id=CODER_CONTAINER),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert COMPOSE_PROJECT in refused.message
        assert "not adopted" in refused.message
        assert hub.registry.managed_instances() == []
        assert runtime.created == []
        assert runtime.removed == []
        assert runtime.stopped == []

    asyncio.run(scenario())


def test_a_legacy_runtime_is_interrupted_only_when_the_operator_acknowledges_it(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        # A receiver from before the contract server: health and signals, and no control token.
        control = FakeControl(capabilities=[Capability.WS])
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=control)
        directory = existing_coder(runtime, tmp_path / "box" / "coder", control_token="")
        client = hub_client(hub)

        previewed = await client.call(
            INSTANCE_ADOPT_PREVIEW,
            InstanceAdoptPreviewCommand(
                path=directory,
                runtime_id=CODER_CONTAINER,
                relinquished=True,
            ),
        )
        refused = await client.call(
            INSTANCE_ADOPT,
            InstanceAdoptCommand(
                path=directory,
                runtime_id=CODER_CONTAINER,
                relinquished=True,
            ),
        )
        accepted = await client.call(
            INSTANCE_ADOPT,
            InstanceAdoptCommand(
                path=directory,
                runtime_id=CODER_CONTAINER,
                relinquished=True,
                acknowledge_interrupting_stop=True,
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)

        assert not isinstance(previewed, ErrorEnvelope)
        # The relinquished manager is still reported; only the legacy runtime blocks.
        assert [finding.kind for finding in previewed.findings] == [
            AdoptionFindingKind.PREVIOUS_MANAGER,
            AdoptionFindingKind.LEGACY_RUNTIME,
        ]
        assert [finding.blocking for finding in previewed.findings] == [False, True]
        assert "cannot drain" in previewed.findings[1].detail
        assert "interrupted at once" in previewed.handoff.downtime
        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert runtime.stopped == [STOP_GRACE_SECONDS]
        assert outcome.state is OperationState.SUCCEEDED
        # The legacy stop is named for what it is, and no drain was ever claimed.
        assert [step.name for step in outcome.steps] == [
            "probe",
            "interrupt",
            "configure",
            "claim",
            "remove",
            "create",
            "start",
        ]
        assert control.forces == []
        assert len(runtime.created) == 1
        assert runtime.created[0].storage == coder_storage(directory)

    asyncio.run(scenario())


async def adopted(client: ContractClient, directory: Path, container: str) -> UUID:
    """Hand one existing instance over, the way the operator does after the preflight."""
    accepted = await client.call(
        INSTANCE_ADOPT,
        InstanceAdoptCommand(path=directory, runtime_id=container, relinquished=True),
    )
    assert isinstance(accepted, LifecycleOperationResult)
    assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED
    return accepted.instance_id


def test_the_same_instance_cannot_be_adopted_twice_under_another_spelling_of_its_path(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=FakeControl())
        directory = existing_coder(runtime, tmp_path / "box" / "coder")
        client = hub_client(hub)
        instance_id = await adopted(client, directory, CODER_CONTAINER)
        # The same directory, mounted by a second container under a path that walks through it.
        alias = f"{directory.parent}/./{directory.name}"
        existing_coder(
            runtime,
            tmp_path / "second",
            container="kinby-coder-2",
            storage=coder_storage(alias),
        )

        previewed = await client.call(
            INSTANCE_ADOPT_PREVIEW,
            InstanceAdoptPreviewCommand(path=directory, runtime_id="kinby-coder-2"),
        )
        refused = await client.call(
            INSTANCE_ADOPT,
            InstanceAdoptCommand(
                path=directory,
                runtime_id="kinby-coder-2",
                relinquished=True,
            ),
        )

        assert not isinstance(previewed, ErrorEnvelope)
        assert previewed.instance_id == instance_id
        assert AdoptionFindingKind.DUPLICATE_ADOPTION in {
            finding.kind for finding in previewed.findings
        }
        assert isinstance(refused, ErrorEnvelope)
        assert "already manages" in refused.message
        assert len(hub.registry.managed_instances()) == 1
        assert len(runtime.created) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("prepared", "kind"),
    [
        (True, AdoptionFindingKind.STORAGE_OWNED),
        (False, AdoptionFindingKind.RETAINED_STORAGE),
    ],
)
def test_writable_storage_another_record_owns_stops_the_handoff(tmp_path, prepared, kind):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=FakeControl())
        directory = existing_coder(runtime, tmp_path / "box" / "coder")
        claim_storage(hub.directory / "registry.sqlite", "kinby_coder-workspace", prepared=prepared)

        previewed = await hub_client(hub).call(
            INSTANCE_ADOPT_PREVIEW,
            InstanceAdoptPreviewCommand(
                path=directory,
                runtime_id=CODER_CONTAINER,
                relinquished=True,
            ),
        )

        assert not isinstance(previewed, ErrorEnvelope)
        assert [finding.kind for finding in previewed.findings] == [
            AdoptionFindingKind.PREVIOUS_MANAGER,
            kind,
        ]
        assert "kinby_coder-workspace" in previewed.findings[1].detail
        assert previewed.findings[1].blocking

    asyncio.run(scenario())


def test_two_instances_with_the_same_manifest_id_are_both_adopted_under_their_own_identity(
    tmp_path,
):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=FakeControl())
        client = hub_client(hub)
        first = existing_coder(runtime, tmp_path / "one" / "coder")
        await adopted(client, first, CODER_CONTAINER)
        second = tmp_path / "two" / "coder"
        existing_coder(
            runtime,
            second,
            container="kinby-spare-1",
            storage=coder_storage(second, volumes="spare_coder"),
        )

        previewed = await client.call(
            INSTANCE_ADOPT_PREVIEW,
            InstanceAdoptPreviewCommand(
                path=second,
                runtime_id="kinby-spare-1",
                relinquished=True,
            ),
        )
        assert not isinstance(previewed, ErrorEnvelope)
        await adopted(client, second, "kinby-spare-1")

        assert [finding.kind for finding in previewed.findings] == [
            AdoptionFindingKind.PREVIOUS_MANAGER,
            AdoptionFindingKind.MANIFEST_ID_TAKEN,
        ]
        assert previewed.findings[1].blocking is False
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert not isinstance(listed, ErrorEnvelope)
        assert [instance.manifest_id for instance in listed.instances] == ["coder", "coder"]
        assert len({instance.instance_id for instance in listed.instances}) == 2

    asyncio.run(scenario())


class CrashOnReplacement(FakeRuntime):
    """Die once between removing the previous container and creating its replacement."""

    def __init__(self) -> None:
        super().__init__()
        self.crashed = asyncio.Event()

    async def create(self, spec: InstanceSpec) -> None:
        if self.removed and not self.crashed.is_set():
            self.crashed.set()
            raise asyncio.CancelledError
        await super().create(spec)


class CrashOnStop(FakeRuntime):
    """Die while taking the previous runtime down, before ownership has moved."""

    def __init__(self) -> None:
        super().__init__()
        self.crashed = asyncio.Event()

    async def stop(self, instance_id: str, *, grace_seconds: int) -> None:
        if not self.crashed.is_set():
            self.crashed.set()
            raise asyncio.CancelledError
        await super().stop(instance_id, grace_seconds=grace_seconds)


class CrashBeforeHandoffStep(FakeRuntime):
    """Die on the status read that opens the handoff, before any step is recorded.

    ``adopt`` reads status once to choose the intended state. The handoff reads it
    again, and that second read is the first thing the handoff does.
    """

    def __init__(self) -> None:
        super().__init__()
        self.crashed = asyncio.Event()
        self._seen = 0

    async def status(self, instance_id: str) -> RuntimeStatus:
        self._seen += 1
        if self._seen == 2:
            self.crashed.set()
            raise asyncio.CancelledError
        return await super().status(instance_id)


def test_a_handoff_interrupted_after_ownership_moved_leaves_a_container_to_recreate(tmp_path):
    async def scenario() -> None:
        runtime = CrashOnReplacement()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=FakeControl())
        directory = existing_coder(runtime, tmp_path / "box" / "coder")
        interrupted = await hub_client(hub).call(
            INSTANCE_ADOPT,
            InstanceAdoptCommand(
                path=directory,
                runtime_id=CODER_CONTAINER,
                relinquished=True,
            ),
        )
        assert isinstance(interrupted, LifecycleOperationResult)
        await asyncio.wait_for(runtime.crashed.wait(), timeout=5)
        await asyncio.sleep(0)
        hub.close()

        reopened = hub_at(
            tmp_path / "hub",
            runtime=runtime,
            images=FakeImages(),
            control=FakeControl(),
        )
        recovery = await reopened.recover()
        client = hub_client(reopened)
        failed = await client.call(
            OPERATION_GET,
            OperationGetCommand(operation_id=interrupted.operation_id),
        )
        again = await client.call(
            INSTANCE_RECREATE,
            InstanceRecreateCommand(instance_id=interrupted.instance_id),
        )
        assert isinstance(again, LifecycleOperationResult)
        outcome = await finished_operation(client, again)

        assert not isinstance(failed, ErrorEnvelope)
        assert failed.state is OperationState.FAILED
        assert failed.steps[-1].name == "create"
        # The hub had already taken ownership, so only the container is missing.
        assert [instance.state for instance in recovery.instances] == [RecoveredState.MISSING]
        assert outcome.state is OperationState.SUCCEEDED
        assert sorted(runtime.created[-1].storage, key=lambda item: item.destination) == sorted(
            coder_storage(directory), key=lambda item: item.destination
        )
        assert runtime.created[-1].image == CODER_IMAGE

    asyncio.run(scenario())


def test_a_handoff_interrupted_before_ownership_moved_names_the_owner_of_the_data(tmp_path):
    async def scenario() -> None:
        runtime = CrashOnStop()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=FakeControl())
        directory = existing_coder(runtime, tmp_path / "box" / "coder")
        interrupted = await hub_client(hub).call(
            INSTANCE_ADOPT,
            InstanceAdoptCommand(
                path=directory,
                runtime_id=CODER_CONTAINER,
                relinquished=True,
            ),
        )
        assert isinstance(interrupted, LifecycleOperationResult)
        await asyncio.wait_for(runtime.crashed.wait(), timeout=5)
        await asyncio.sleep(0)
        hub.close()

        reopened = hub_at(
            tmp_path / "hub",
            runtime=runtime,
            images=FakeImages(),
            control=FakeControl(),
        )
        recovery = await reopened.recover()
        client = hub_client(reopened)
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        adopted_again = await adopted(client, directory, CODER_CONTAINER)

        assert [instance.state for instance in recovery.instances] == [RecoveredState.INCOMPLETE]
        assert COMPOSE_PROJECT in recovery.instances[0].detail
        assert not isinstance(listed, ErrorEnvelope)
        assert listed.instances == []
        assert adopted_again == interrupted.instance_id
        assert runtime.removed == [(CODER_CONTAINER, False)]

    asyncio.run(scenario())


def test_a_handoff_that_dies_before_its_first_step_does_not_claim_the_container(tmp_path):
    async def scenario() -> None:
        runtime = CrashBeforeHandoffStep()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=FakeControl())
        directory = existing_coder(runtime, tmp_path / "box" / "coder")
        interrupted = await hub_client(hub).call(
            INSTANCE_ADOPT,
            InstanceAdoptCommand(
                path=directory,
                runtime_id=CODER_CONTAINER,
                relinquished=True,
            ),
        )
        assert isinstance(interrupted, LifecycleOperationResult)
        await asyncio.wait_for(runtime.crashed.wait(), timeout=5)
        await asyncio.sleep(0)
        hub.close()

        reopened = hub_at(
            tmp_path / "hub",
            runtime=runtime,
            images=FakeImages(),
            control=FakeControl(),
        )
        recovery = await reopened.recover()
        client = hub_client(reopened)
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        failed = await client.call(
            OPERATION_GET,
            OperationGetCommand(operation_id=interrupted.operation_id),
        )

        assert not isinstance(failed, ErrorEnvelope)
        assert failed.state is OperationState.FAILED
        assert failed.steps == []
        assert [instance.state for instance in recovery.instances] == [RecoveredState.INCOMPLETE]
        assert COMPOSE_PROJECT in recovery.instances[0].detail
        assert not isinstance(listed, ErrorEnvelope)
        assert listed.instances == []
        assert runtime.descriptions[CODER_CONTAINER].owner is ContainerOwner.COMPOSE
        assert runtime.removed == []
        assert runtime.created == []

    asyncio.run(scenario())


def test_the_webhook_url_registered_before_the_hub_reaches_the_adopted_instance(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=FakeControl())
        directory = existing_coder(runtime, tmp_path / "box" / "coder")
        client = hub_client(hub)
        received: list[web.Request] = []
        async with served(hub) as address, recording_instance(received) as private:
            accepted = await client.call(
                INSTANCE_ADOPT,
                InstanceAdoptCommand(
                    path=directory,
                    runtime_id=CODER_CONTAINER,
                    relinquished=True,
                    claim_signals=True,
                ),
            )
            assert isinstance(accepted, LifecycleOperationResult)
            assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED
            runtime.addresses[CODER_CONTAINER] = f"http://{private.host}:{private.port}"
            async with aiohttp.ClientSession() as sender:
                answered = await sender.post(
                    url(address, "/signals/news"),
                    data=b"\x00binary\xffbody",
                    headers={"X-Hub-Signature-256": SIGNATURE},
                )

        assert answered.status == 202
        assert len(received) == 1
        # The instance verifies the signature itself, so the bytes and the header arrive whole.
        assert received[0].path == "/signals/news"
        assert await received[0].read() == b"\x00binary\xffbody"
        assert received[0].headers["X-Hub-Signature-256"] == SIGNATURE

    asyncio.run(scenario())


def test_a_container_the_hub_cannot_see_is_previewed_as_unreachable(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=FakeControl())
        directory = coder_directory(tmp_path / "box" / "coder")

        previewed = await hub_client(hub).call(
            INSTANCE_ADOPT_PREVIEW,
            InstanceAdoptPreviewCommand(
                path=directory, runtime_id="kinby-gone-1", relinquished=True
            ),
        )

        assert not isinstance(previewed, ErrorEnvelope)
        assert previewed.manifest_id == "coder"
        assert previewed.storage == []
        assert [finding.kind for finding in previewed.findings] == [AdoptionFindingKind.UNREACHABLE]
        assert previewed.findings[0].blocking

    asyncio.run(scenario())


def test_a_directory_that_is_not_the_container_s_instance_is_never_adopted(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=FakeControl())
        existing_coder(runtime, tmp_path / "box" / "coder")
        other = coder_directory(tmp_path / "box" / "other", manifest_id="other", persona_name="Bea")

        previewed = await hub_client(hub).call(
            INSTANCE_ADOPT_PREVIEW,
            InstanceAdoptPreviewCommand(
                path=other,
                runtime_id=CODER_CONTAINER,
                relinquished=True,
            ),
        )
        refused = await hub_client(hub).call(
            INSTANCE_ADOPT,
            InstanceAdoptCommand(path=other, runtime_id=CODER_CONTAINER, relinquished=True),
        )

        assert not isinstance(previewed, ErrorEnvelope)
        assert previewed.findings[0].kind is AdoptionFindingKind.INVALID_INSTANCE
        assert previewed.findings[0].blocking
        assert str(other) in previewed.findings[0].detail
        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert hub.registry.managed_instances() == []
        assert runtime.stopped == []
        assert runtime.removed == []

    asyncio.run(scenario())


def test_a_directory_that_holds_no_instance_is_never_adopted(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=FakeControl())
        existing_coder(runtime, tmp_path / "box" / "coder")
        empty = tmp_path / "empty"
        empty.mkdir()

        refused = await hub_client(hub).call(
            INSTANCE_ADOPT,
            InstanceAdoptCommand(path=empty, runtime_id=CODER_CONTAINER, relinquished=True),
        )

        assert isinstance(refused, ErrorEnvelope)
        assert str(empty) in refused.message
        assert hub.registry.managed_instances() == []
        assert runtime.stopped == []

    asyncio.run(scenario())


def test_an_instance_that_speaks_an_older_contract_is_a_legacy_runtime(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        control = FakeControl(contract_version="0")
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=control)
        directory = existing_coder(runtime, tmp_path / "box" / "coder")

        previewed = await hub_client(hub).call(
            INSTANCE_ADOPT_PREVIEW,
            InstanceAdoptPreviewCommand(
                path=directory,
                runtime_id=CODER_CONTAINER,
                relinquished=True,
            ),
        )

        assert not isinstance(previewed, ErrorEnvelope)
        assert [finding.kind for finding in previewed.findings] == [
            AdoptionFindingKind.PREVIOUS_MANAGER,
            AdoptionFindingKind.LEGACY_RUNTIME,
        ]
        assert "contract version 0" in previewed.findings[1].detail
        assert previewed.findings[1].blocking

    asyncio.run(scenario())


def test_an_instance_stopped_before_the_handoff_is_adopted_without_interrupting_anything(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        control = FakeControl()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=control)
        directory = existing_coder(runtime, tmp_path / "box" / "coder", running=False)
        client = hub_client(hub)

        previewed = await client.call(
            INSTANCE_ADOPT_PREVIEW,
            InstanceAdoptPreviewCommand(
                path=directory,
                runtime_id=CODER_CONTAINER,
                relinquished=True,
            ),
        )
        accepted = await client.call(
            INSTANCE_ADOPT,
            InstanceAdoptCommand(
                path=directory,
                runtime_id=CODER_CONTAINER,
                relinquished=True,
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await finished_operation(client, accepted)

        assert not isinstance(previewed, ErrorEnvelope)
        # Nothing is running, so nothing has to be drained and nothing is acknowledged.
        assert [finding.blocking for finding in previewed.findings] == [False]
        assert "already stopped" in previewed.handoff.downtime
        assert outcome.state is OperationState.SUCCEEDED
        assert [step.name for step in outcome.steps] == [
            "stopped",
            "configure",
            "claim",
            "remove",
            "create",
        ]
        assert control.forces == []
        assert runtime.stopped == []
        assert runtime.started == []
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert not isinstance(listed, ErrorEnvelope)
        assert listed.instances[0].intended_state is IntendedState.STOPPED

    asyncio.run(scenario())


class HoldStatus(FakeRuntime):
    """Hold status so two adoptions can both finish preflight before either is recorded."""

    def __init__(self) -> None:
        super().__init__()
        self.waiting = 0
        self.ready = asyncio.Event()
        self.release = asyncio.Event()

    async def status(self, instance_id: str) -> RuntimeStatus:
        self.waiting += 1
        if self.waiting >= 2:
            self.ready.set()
        await self.release.wait()
        return await super().status(instance_id)


def test_a_second_adoption_is_refused_while_the_handoff_is_being_recorded(tmp_path):
    async def scenario() -> None:
        runtime = HoldStatus()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=FakeControl())
        directory = existing_coder(runtime, tmp_path / "box" / "coder")
        client = hub_client(hub)
        command = InstanceAdoptCommand(
            path=directory, runtime_id=CODER_CONTAINER, relinquished=True
        )
        first = asyncio.create_task(client.call(INSTANCE_ADOPT, command))
        second = asyncio.create_task(client.call(INSTANCE_ADOPT, command))
        await asyncio.wait_for(runtime.ready.wait(), timeout=5)
        runtime.release.set()
        outcomes = await asyncio.gather(first, second)

        accepted = next(
            result for result in outcomes if isinstance(result, LifecycleOperationResult)
        )
        refused = next(result for result in outcomes if isinstance(result, ErrorEnvelope))
        finished = await finished_operation(client, accepted)

        assert refused.code is ErrorCode.INSTANCE_BUSY
        assert finished.state is OperationState.SUCCEEDED
        assert len(runtime.created) == 1
        assert len(hub.registry.managed_instances()) == 1
        assert hub.registry.active_operation(accepted.instance_id) is None

    asyncio.run(scenario())


def test_concurrent_adoptions_do_not_claim_the_same_writable_storage(tmp_path):
    async def scenario() -> None:
        runtime = HoldStatus()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=FakeControl())
        first = existing_coder(runtime, tmp_path / "one" / "coder")
        second = tmp_path / "two" / "coder"
        existing_coder(
            runtime,
            second,
            container="kinby-spare-1",
            storage=coder_storage(second, volumes="kinby_coder"),
        )
        client = hub_client(hub)
        first_task = asyncio.create_task(
            client.call(
                INSTANCE_ADOPT,
                InstanceAdoptCommand(path=first, runtime_id=CODER_CONTAINER, relinquished=True),
            )
        )
        second_task = asyncio.create_task(
            client.call(
                INSTANCE_ADOPT,
                InstanceAdoptCommand(path=second, runtime_id="kinby-spare-1", relinquished=True),
            )
        )
        await asyncio.wait_for(runtime.ready.wait(), timeout=5)
        runtime.release.set()
        outcomes = await asyncio.gather(first_task, second_task)

        accepted = next(
            result for result in outcomes if isinstance(result, LifecycleOperationResult)
        )
        refused = next(result for result in outcomes if isinstance(result, ErrorEnvelope))
        finished = await finished_operation(client, accepted)
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())

        assert refused.code is ErrorCode.INVALID_ARGUMENT
        assert "kinby_coder" in refused.message
        assert finished.state is OperationState.SUCCEEDED
        assert len(runtime.created) == 1
        assert not isinstance(listed, ErrorEnvelope)
        assert len(listed.instances) == 1

    asyncio.run(scenario())


def test_the_preview_reports_the_bind_source_the_docker_host_knows(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=FakeControl())
        # The hub reads the directory where it is mounted; the daemon mounts it from elsewhere.
        host_source = "/srv/kinby/instances/coder"
        directory = existing_coder(
            runtime,
            tmp_path / "view" / "coder",
            storage=coder_storage(host_source),
        )

        previewed = await hub_client(hub).call(
            INSTANCE_ADOPT_PREVIEW,
            InstanceAdoptPreviewCommand(
                path=directory,
                runtime_id=CODER_CONTAINER,
                relinquished=True,
            ),
        )

        assert not isinstance(previewed, ErrorEnvelope)
        assert previewed.path == directory
        assert previewed.storage[0].source == host_source
        assert str(directory) not in {item.source for item in previewed.storage}

    asyncio.run(scenario())


FACTORY_URL = "https://github.com/jorgesolerrr/kinby-code-factory"
FACTORY = PackageSelection(
    id="coder",
    distribution="kinby-code-factory",
    version=PackageCommit(url=FACTORY_URL, sha="a" * 40),
    image_recipe="RUN echo coding clients\n",
)


def declare_package(directory: Path, package_id: str = "coder") -> None:
    """Migrate the manifest by hand, as the coder's migration does before adoption."""
    with (directory / "kinby.toml").open("a", encoding="utf-8") as manifest:
        manifest.write(
            f'\n[package]\nid = "{package_id}"\n'
            'distribution = "kinby-code-factory"\nversion = "0.1.0"\n'
        )


def test_an_adopted_instance_keeps_its_package_so_a_later_update_can_pin_it(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        images = FakeImages()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=images, control=FakeControl())
        directory = existing_coder(runtime, tmp_path / "box" / "coder")
        declare_package(directory)
        client = hub_client(hub)

        previewed = await client.call(
            INSTANCE_ADOPT_PREVIEW,
            InstanceAdoptPreviewCommand(
                path=directory, runtime_id=CODER_CONTAINER, relinquished=True, package=FACTORY
            ),
        )
        accepted = await client.call(
            INSTANCE_ADOPT,
            InstanceAdoptCommand(
                path=directory, runtime_id=CODER_CONTAINER, relinquished=True, package=FACTORY
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        assert (await finished_operation(client, accepted)).state is OperationState.SUCCEEDED
        # Adoption keeps the running image. The package image is the next update's.
        assert runtime.created[0].image == CODER_IMAGE
        assert images.selections == []
        pinned = await client.call(
            INSTANCE_UPDATE,
            InstanceUpdateCommand(
                instance_id=accepted.instance_id,
                revision="main",
                package=PackagePin(id="coder", sha="b" * 40),
            ),
        )

        assert not isinstance(previewed, ErrorEnvelope)
        assert all(not finding.blocking for finding in previewed.findings)
        assert isinstance(pinned, LifecycleOperationResult)
        await finished_operation(client, pinned)
        assert images.selections[0].package == FACTORY.model_copy(
            update={"version": PackageCommit(url=FACTORY_URL, sha="b" * 40)}
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("declared", "selection", "expected"),
    [
        (None, FACTORY, 'declares no package, not "coder"'),
        ("coder", None, 'declares package "coder"'),
        ("writer", FACTORY, 'declares package "writer"'),
    ],
)
def test_a_package_that_disagrees_with_the_manifest_blocks_the_adoption(
    tmp_path, declared, selection, expected
):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=FakeControl())
        directory = existing_coder(runtime, tmp_path / "box" / "coder")
        if declared is not None:
            declare_package(directory, declared)
        client = hub_client(hub)

        previewed = await client.call(
            INSTANCE_ADOPT_PREVIEW,
            InstanceAdoptPreviewCommand(
                path=directory, runtime_id=CODER_CONTAINER, relinquished=True, package=selection
            ),
        )
        refused = await client.call(
            INSTANCE_ADOPT,
            InstanceAdoptCommand(
                path=directory, runtime_id=CODER_CONTAINER, relinquished=True, package=selection
            ),
        )

        assert not isinstance(previewed, ErrorEnvelope)
        mismatch = [
            finding
            for finding in previewed.findings
            if finding.kind is AdoptionFindingKind.PACKAGE_MISMATCH
        ]
        assert len(mismatch) == 1
        assert mismatch[0].blocking
        assert expected in mismatch[0].detail
        assert isinstance(refused, ErrorEnvelope)
        assert "not adopted" in refused.message
        assert hub.registry.managed_instances() == []
        assert runtime.removed == []

    asyncio.run(scenario())
