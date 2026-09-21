import asyncio
import os
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

from kinby.cli.client import ContractClient
from kinby.contracts import (
    CONTROL_SCOPES,
    INSTANCE_CREATE,
    INSTANCE_LIST,
    INSTANCE_LOGS,
    INSTANCE_SCOPES,
    INSTANCE_START,
    INSTANCE_STATUS,
    OPERATION_GET,
    ErrorCode,
    ErrorEnvelope,
    InstanceCreateCommand,
    InstanceListCommand,
    InstanceLogsCommand,
    InstanceStartCommand,
    InstanceStatusCommand,
    LifecycleOperationResult,
    OperationGetCommand,
    OperationKind,
    OperationState,
    PackageSelection,
    Readiness,
    Scope,
    StorageItem,
    StorageKind,
)
from kinby.hub import (
    Hub,
    ImageArtifact,
    ImageSelection,
    InstanceSpec,
    PreparedImage,
    RuntimeStatus,
)
from kinby.instance import inspect_instance
from kinby.packages import InstalledPackage, PackageDescriptor, RequiredSecret


class FakeImages:
    def __init__(
        self,
        *,
        failure: str | None = None,
        package: InstalledPackage | None = None,
    ) -> None:
        self.revisions: list[str] = []
        self.failure = failure
        self.package = package

    async def prepare(self, selection: ImageSelection) -> PreparedImage:
        self.revisions.append(selection.revision)
        if self.failure is not None:
            raise RuntimeError(self.failure)
        return PreparedImage(
            artifact=ImageArtifact(
                image_id="sha256:selected-image",
                revision="a" * 40,
                dependency_id="sha256:dependencies",
                base_images=("python@sha256:base",),
                package=selection.package,
            ),
            package=self.package,
        )


class FakeRuntime:
    def __init__(self) -> None:
        self.created: list[InstanceSpec] = []
        self.started: list[str] = []
        self.states: dict[str, RuntimeStatus] = {}
        self.addresses: dict[str, str] = {}
        self.log_output = b"booted\n"

    async def create(self, spec: InstanceSpec) -> None:
        self.created.append(spec)
        self.states[spec.instance_id] = RuntimeStatus("created", None)

    async def start(self, instance_id: str) -> None:
        self.started.append(instance_id)
        self.states[instance_id] = RuntimeStatus("running", True)

    async def stop(self, instance_id: str) -> None:
        self.states[instance_id] = RuntimeStatus("stopped", None)

    async def remove(self, instance_id: str, *, delete_data: bool = False) -> None:
        self.states.pop(instance_id, None)

    async def status(self, instance_id: str) -> RuntimeStatus:
        return self.states.get(instance_id, RuntimeStatus("absent", None))

    async def address(self, instance_id: str) -> str | None:
        if self.states.get(instance_id, RuntimeStatus("absent", None)).state != "running":
            return None
        return self.addresses.get(instance_id)

    async def logs(
        self,
        instance_id: str,
        *,
        tail: int | None = None,
        follow: bool = False,
    ) -> AsyncIterator[bytes]:
        yield self.log_output

    async def exec(
        self,
        instance_id: str,
        command: Sequence[str],
    ) -> AsyncIterator[bytes]:
        if False:
            yield b""

    async def list(self) -> Sequence[str]:
        return tuple(self.states)


class SerialRuntime(FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.active_starts = 0
        self.maximum_active_starts = 0

    async def start(self, instance_id: str) -> None:
        self.active_starts += 1
        self.maximum_active_starts = max(self.maximum_active_starts, self.active_starts)
        await asyncio.sleep(0.02)
        await super().start(instance_id)
        self.active_starts -= 1


class UnavailableRuntime(FakeRuntime):
    async def status(self, instance_id: str) -> RuntimeStatus:
        raise ConnectionError("Docker daemon unavailable")


class HeldRuntime(FakeRuntime):
    """Hold a start until the test releases it, so an operation stays in flight."""

    def __init__(self) -> None:
        super().__init__()
        self.holding = asyncio.Event()
        self.released = asyncio.Event()

    async def start(self, instance_id: str) -> None:
        self.holding.set()
        await self.released.wait()
        await super().start(instance_id)


def _client(hub: Hub, scopes: set[Scope] | None = None) -> ContractClient:
    return ContractClient(
        hub.dispatcher.dispatch,
        hub.dispatcher.subscribe,
        scopes if scopes is not None else {Scope.HUB_READ, Scope.HUB_ADMIN},
    )


async def _operation(client: ContractClient, accepted: LifecycleOperationResult):
    for _ in range(100):
        result = await client.call(
            OPERATION_GET,
            OperationGetCommand(operation_id=accepted.operation_id),
        )
        assert not isinstance(result, ErrorEnvelope)
        if result.state in {OperationState.SUCCEEDED, OperationState.FAILED}:
            return result
        await asyncio.sleep(0.01)
    raise AssertionError("lifecycle operation did not finish")


def test_create_prepares_a_stopped_vanilla_instance_and_survives_reopening(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        images = FakeImages()
        hub = Hub(tmp_path / "hub", runtime=runtime, images=images)
        hub_id = hub.registry.hub_id()
        client = _client(hub)

        accepted = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="alice",
                persona_name="Ada",
                model="openai:gpt-5",
                revision="main",
                secrets={"PROVIDER_TOKEN": "private-value"},
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await _operation(client, accepted)

        assert outcome.state is OperationState.SUCCEEDED
        assert outcome.detail == "Instance prepared and stopped."
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert not isinstance(listed, ErrorEnvelope)
        assert len(listed.instances) == 1
        summary = listed.instances[0]
        assert summary.instance_id == accepted.instance_id
        assert str(summary.instance_id) not in {"alice", "Ada"}
        assert summary.manifest_id == "alice"
        assert summary.persona_name == "Ada"
        assert summary.image_id == "sha256:selected-image"
        assert summary.intended_state == "stopped"
        assert {item.destination for item in summary.storage} == {
            "/instance",
            "/instance/workspace",
            "/root/.codex",
        }
        assert len(runtime.created) == 1
        spec = runtime.created[0]
        assert spec.image == "sha256:selected-image"
        assert spec.env["PROVIDER_TOKEN"] == "private-value"
        assert runtime.started == []
        instance_path = tmp_path / "hub" / "instances" / str(accepted.instance_id)
        assert instance_path.is_dir()
        serve = inspect_instance(instance_path).manifest.serve
        assert serve is not None
        assert (serve.host, serve.port) == ("0.0.0.0", 8787)
        assert (instance_path / ".env").stat().st_mode & 0o777 == 0o600
        assert not any((instance_path / ".state").iterdir())
        assert "private-value" not in (tmp_path / "hub" / "registry.sqlite").read_bytes().decode(
            "utf-8", errors="ignore"
        )

        reopened = Hub(tmp_path / "hub", runtime=runtime, images=images)
        assert reopened.registry.hub_id() == hub_id
        reopened_result = await _client(reopened).call(
            OPERATION_GET,
            OperationGetCommand(operation_id=accepted.operation_id),
        )
        assert not isinstance(reopened_result, ErrorEnvelope)
        assert reopened_result.state is OperationState.SUCCEEDED

    asyncio.run(scenario())


def test_create_from_a_pinned_package_seeds_owned_configuration_and_provenance(tmp_path):
    async def scenario() -> None:
        package = InstalledPackage(
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
            files={
                "kinby.toml": '[feedback]\nask = "off"\n',
                "SYSTEM.md": "You are an exacting editor.\n",
                "factory.toml": 'style = "plain"\n',
                "routines/draft/ROUTINE.md": "---\nname: draft\n---\nDraft an article.\n",
                "routines/draft/run.py": "from kinby_writer import draft\n",
                "skills/voice/SKILL.md": "Packaged skill.\n",
                "tools/editor.py": "def edit(): ...\n",
            },
        )
        runtime = FakeRuntime()
        images = FakeImages(package=package)
        hub = Hub(tmp_path / "hub", runtime=runtime, images=images)
        client = _client(hub)
        selection = PackageSelection(
            id="writer",
            distribution="kinby-writer",
            version="1.4.2",
        )

        accepted = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="editor",
                persona_name="Quill",
                model="openai:gpt-5",
                revision="v0.1.0",
                package=selection,
                secrets={"EDITOR_TOKEN": "private-editor-token"},
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await _operation(client, accepted)

        assert outcome.state is OperationState.SUCCEEDED
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert not isinstance(listed, ErrorEnvelope)
        assert listed.instances[0].package is not None
        assert listed.instances[0].package.id == "writer"
        assert listed.instances[0].package.version == "1.4.2"
        instance_path = tmp_path / "hub" / "instances" / str(accepted.instance_id)
        instance = inspect_instance(instance_path)
        assert instance.manifest.package is not None
        assert instance.manifest.package.id == "writer"
        assert instance.manifest.package.distribution == "kinby-writer"
        assert instance.manifest.package.version == "1.4.2"
        assert instance.manifest.feedback.ask == "off"
        assert (instance_path / "SYSTEM.md").read_text() == "You are an exacting editor.\n"
        assert (instance_path / "factory.toml").read_text() == 'style = "plain"\n'
        assert (instance_path / "routines" / "draft" / "run.py").is_file()
        assert not (instance_path / "skills" / "voice").exists()
        assert not (instance_path / "tools" / "editor.py").exists()
        assert runtime.created[0].env["EDITOR_TOKEN"] == "private-editor-token"
        assert runtime.started == []

    asyncio.run(scenario())


def test_package_creation_requires_declared_secrets_before_publishing_an_instance(tmp_path):
    async def scenario() -> None:
        package = InstalledPackage(
            descriptor=PackageDescriptor(
                id="writer",
                display_name="Writing teammate",
                description="Drafts articles.",
                icon="pen",
                distribution="kinby-writer",
                version="1.4.2",
                required_secrets=(
                    RequiredSecret("EDITOR_TOKEN", "Editor token", "Authenticates editing."),
                ),
            ),
            files={"SYSTEM.md": "Write clearly.\n"},
        )
        runtime = FakeRuntime()
        hub = Hub(tmp_path / "hub", runtime=runtime, images=FakeImages(package=package))
        client = _client(hub)

        accepted = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="editor",
                model="openai:gpt-5",
                package=PackageSelection(
                    id="writer",
                    distribution="kinby-writer",
                    version="1.4.2",
                ),
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await _operation(client, accepted)

        assert outcome.state is OperationState.FAILED
        assert outcome.detail == 'Missing required secret: "EDITOR_TOKEN".'
        assert runtime.created == []
        assert not (tmp_path / "hub" / "instances" / str(accepted.instance_id)).exists()
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert not isinstance(listed, ErrorEnvelope)
        assert listed.instances == []

    asyncio.run(scenario())


def test_invalid_package_configuration_is_not_published_or_sent_to_the_runtime(tmp_path):
    async def scenario() -> None:
        package = InstalledPackage(
            descriptor=PackageDescriptor(
                id="writer",
                display_name="Writing teammate",
                description="Drafts articles.",
                icon="pen",
                distribution="kinby-writer",
                version="1.4.2",
            ),
            files={"kinby.toml": '[workspace]\nsnapshots = "invalid"\n'},
        )
        secret = "recognizable-package-secret"
        runtime = FakeRuntime()
        hub = Hub(tmp_path / "hub", runtime=runtime, images=FakeImages(package=package))
        client = _client(hub)

        accepted = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="editor",
                model="openai:gpt-5",
                package=PackageSelection(
                    id="writer",
                    distribution="kinby-writer",
                    version="1.4.2",
                ),
                secrets={"TOKEN": secret},
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await _operation(client, accepted)

        assert outcome.state is OperationState.FAILED
        assert "workspace.snapshots" in outcome.detail
        assert secret not in outcome.detail
        assert runtime.created == []
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert not isinstance(listed, ErrorEnvelope)
        assert listed.instances == []

    asyncio.run(scenario())


def test_authorization_precedes_secret_validation_and_effects(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        images = FakeImages()
        hub = Hub(tmp_path / "hub", runtime=runtime, images=images)

        result = await hub.dispatcher.dispatch(
            INSTANCE_CREATE.name,
            {
                "manifest_id": "alice",
                "model": "not-a-model",
                "secrets": {"BAD KEY": "do-not-disclose"},
            },
            {Scope.HUB_READ},
        )

        assert isinstance(result, ErrorEnvelope)
        assert result.code is ErrorCode.PERMISSION_DENIED
        assert "do-not-disclose" not in result.message
        assert images.revisions == []
        assert runtime.created == []

    asyncio.run(scenario())


def test_secret_command_serializes_the_value_for_transport_without_exposing_its_repr():
    command = InstanceCreateCommand(
        manifest_id="alice",
        model="openai:gpt-5",
        secrets={"TOKEN": "transport-secret"},
    )

    assert "transport-secret" not in repr(command)
    assert '"TOKEN":"transport-secret"' in command.model_dump_json()


def test_malformed_secret_is_not_exposed_by_contract_validation(tmp_path):
    async def scenario() -> None:
        secret = "malformed-submitted-secret"
        hub = Hub(tmp_path / "hub", runtime=FakeRuntime(), images=FakeImages())

        result = await hub.dispatcher.dispatch(
            INSTANCE_CREATE.name,
            {
                "manifest_id": "alice",
                "model": "openai:gpt-5",
                "secrets": {"TOKEN": [secret]},
            },
            {Scope.HUB_ADMIN},
        )

        assert isinstance(result, ErrorEnvelope)
        assert result.code is ErrorCode.INVALID_ARGUMENT
        assert secret not in result.message
        assert "secrets.TOKEN" in result.message

    asyncio.run(scenario())


def test_invalid_manifest_metadata_is_an_operation_failure_before_runtime_effects(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        images = FakeImages()
        hub = Hub(tmp_path / "hub", runtime=runtime, images=images)
        client = _client(hub)

        accepted = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="alice",
                model="not-a-provider-model",
                secrets={"TOKEN": "not-in-the-error"},
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await _operation(client, accepted)

        assert outcome.state is OperationState.FAILED
        assert "models.main" in outcome.detail
        assert "not-in-the-error" not in outcome.detail
        assert images.revisions == []
        assert runtime.created == []

    asyncio.run(scenario())


def test_failed_build_is_inspectable_and_does_not_touch_the_runtime(tmp_path):
    async def scenario() -> None:
        secret = "submitted-secret"
        runtime = FakeRuntime()
        images = FakeImages(failure=f"builder rejected {secret}")
        hub = Hub(tmp_path / "hub", runtime=runtime, images=images)
        client = _client(hub)

        accepted = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="alice",
                model="openai:gpt-5",
                secrets={"TOKEN": secret},
            ),
        )
        assert isinstance(accepted, LifecycleOperationResult)
        outcome = await _operation(client, accepted)

        assert outcome.state is OperationState.FAILED
        assert secret not in outcome.detail
        assert runtime.created == []
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert not isinstance(listed, ErrorEnvelope)
        assert listed.instances == []

    asyncio.run(scenario())


def test_start_status_and_logs_use_the_selected_image_and_redact_secrets(tmp_path):
    async def scenario() -> None:
        secret = "secret-in-runtime-log"
        runtime = FakeRuntime()
        images = FakeImages()
        hub = Hub(tmp_path / "hub", runtime=runtime, images=images)
        client = _client(hub)
        created = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="alice",
                model="openai:gpt-5",
                secrets={"TOKEN": secret},
            ),
        )
        assert isinstance(created, LifecycleOperationResult)
        assert (await _operation(client, created)).state is OperationState.SUCCEEDED
        runtime.log_output = f"ready token={secret}\n".encode()

        started = await client.call(
            INSTANCE_START,
            InstanceStartCommand(instance_id=created.instance_id),
        )
        assert isinstance(started, LifecycleOperationResult)
        assert (await _operation(client, started)).state is OperationState.SUCCEEDED
        status = await client.call(
            INSTANCE_STATUS,
            InstanceStatusCommand(instance_id=created.instance_id),
        )
        assert not isinstance(status, ErrorEnvelope)
        assert status.process == "running"
        assert status.readiness is Readiness.READY
        logs = await client.call(
            INSTANCE_LOGS,
            InstanceLogsCommand(instance_id=created.instance_id),
        )
        assert not isinstance(logs, ErrorEnvelope)
        assert secret not in logs.text
        assert "[REDACTED]" in logs.text

        started_again = await client.call(
            INSTANCE_START,
            InstanceStartCommand(instance_id=created.instance_id),
        )
        assert isinstance(started_again, LifecycleOperationResult)
        assert (await _operation(client, started_again)).state is OperationState.SUCCEEDED
        assert images.revisions == ["HEAD"]
        assert runtime.created[0].image == "sha256:selected-image"
        assert runtime.started == [str(created.instance_id), str(created.instance_id)]
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert not isinstance(listed, ErrorEnvelope)
        assert listed.instances[0].intended_state == "running"

    asyncio.run(scenario())


def test_status_distinguishes_missing_starting_unhealthy_and_unavailable(tmp_path):
    @dataclass(frozen=True)
    class ExpectedStatus:
        runtime: RuntimeStatus
        process: str
        readiness: Readiness

    async def create(hub: Hub, runtime: FakeRuntime):
        client = _client(hub)
        created = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(manifest_id="alice", model="openai:gpt-5"),
        )
        assert isinstance(created, LifecycleOperationResult)
        assert (await _operation(client, created)).state is OperationState.SUCCEEDED
        return client, created

    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = Hub(tmp_path / "hub", runtime=runtime, images=FakeImages())
        client, created = await create(hub, runtime)
        cases = (
            ExpectedStatus(RuntimeStatus("absent", None), "missing", Readiness.NOT_RUNNING),
            ExpectedStatus(RuntimeStatus("starting", False), "starting", Readiness.STARTING),
            ExpectedStatus(RuntimeStatus("running", False), "running", Readiness.UNHEALTHY),
        )
        for case in cases:
            runtime.states[str(created.instance_id)] = case.runtime
            result = await client.call(
                INSTANCE_STATUS,
                InstanceStatusCommand(instance_id=created.instance_id),
            )
            assert not isinstance(result, ErrorEnvelope)
            assert result.process == case.process
            assert result.readiness is case.readiness

        unavailable = UnavailableRuntime()
        unavailable.states = runtime.states
        reopened = Hub(tmp_path / "hub", runtime=unavailable, images=FakeImages())
        result = await _client(reopened).call(
            INSTANCE_STATUS,
            InstanceStatusCommand(instance_id=created.instance_id),
        )
        assert not isinstance(result, ErrorEnvelope)
        assert result.process == "unavailable"
        assert result.readiness is Readiness.UNKNOWN

    asyncio.run(scenario())


def test_metadata_for_two_created_instances_never_changes_process_environment(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = Hub(tmp_path / "hub", runtime=runtime, images=FakeImages())
        client = _client(hub)
        before = dict(os.environ)
        for manifest_id, value in (("first", "one"), ("second", "two")):
            created = await client.call(
                INSTANCE_CREATE,
                InstanceCreateCommand(
                    manifest_id=manifest_id,
                    model="openai:gpt-5",
                    secrets={"SHARED": value},
                ),
            )
            assert isinstance(created, LifecycleOperationResult)
            assert (await _operation(client, created)).state is OperationState.SUCCEEDED

        assert dict(os.environ) == before
        assert runtime.created[0].env["SHARED"] == "one"
        assert runtime.created[1].env["SHARED"] == "two"

    asyncio.run(scenario())


def test_each_instance_gets_its_own_control_token_and_never_the_access_token(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = Hub(tmp_path / "hub", runtime=runtime, images=FakeImages())
        access_token = hub.access.issue()
        assert access_token is not None
        client = _client(hub)
        paths = []
        for manifest_id in ("first", "second"):
            created = await client.call(
                INSTANCE_CREATE,
                InstanceCreateCommand(manifest_id=manifest_id, model="openai:gpt-5"),
            )
            assert isinstance(created, LifecycleOperationResult)
            assert (await _operation(client, created)).state is OperationState.SUCCEEDED
            paths.append(tmp_path / "hub" / "instances" / str(created.instance_id))

        tokens = [spec.env["KINBY_CONTROL_TOKEN"] for spec in runtime.created]
        assert len(set(tokens)) == 2
        for path, token in zip(paths, tokens, strict=True):
            environment = (path / ".env").read_text(encoding="utf-8")
            assert f"KINBY_CONTROL_TOKEN='{token}'" in environment
            assert access_token not in environment
        assert all(access_token not in spec.env.values() for spec in runtime.created)

    asyncio.run(scenario())


def test_secret_values_are_passed_without_environment_interpolation(tmp_path):
    async def scenario() -> None:
        runtime = FakeRuntime()
        hub = Hub(tmp_path / "hub", runtime=runtime, images=FakeImages())
        client = _client(hub)
        created = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(
                manifest_id="alice",
                model="openai:gpt-5",
                secrets={"TOKEN": "${HOME}:a'b\\c\n"},
            ),
        )
        assert isinstance(created, LifecycleOperationResult)
        assert (await _operation(client, created)).state is OperationState.SUCCEEDED
        assert runtime.created[0].env["TOKEN"] == "${HOME}:a'b\\c\n"

    asyncio.run(scenario())


def test_concurrent_starts_are_serialized_per_instance(tmp_path):
    async def scenario() -> None:
        runtime = SerialRuntime()
        hub = Hub(tmp_path / "hub", runtime=runtime, images=FakeImages())
        client = _client(hub)
        created = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(manifest_id="alice", model="openai:gpt-5"),
        )
        assert isinstance(created, LifecycleOperationResult)
        assert (await _operation(client, created)).state is OperationState.SUCCEEDED

        first, second = await asyncio.gather(
            client.call(
                INSTANCE_START,
                InstanceStartCommand(instance_id=created.instance_id),
            ),
            client.call(
                INSTANCE_START,
                InstanceStartCommand(instance_id=created.instance_id),
            ),
        )
        assert isinstance(first, LifecycleOperationResult)
        assert isinstance(second, LifecycleOperationResult)
        await asyncio.gather(_operation(client, first), _operation(client, second))

        assert first.operation_id == second.operation_id
        assert runtime.maximum_active_starts == 1
        assert runtime.started == [str(created.instance_id)]

    asyncio.run(scenario())


def test_retained_writable_bind_rejects_an_overlapping_path_alias(tmp_path):
    async def scenario() -> None:
        hub = Hub(tmp_path / "hub", runtime=FakeRuntime(), images=FakeImages())
        client = _client(hub)
        created = []
        for manifest_id in ("first", "second"):
            result = await client.call(
                INSTANCE_CREATE,
                InstanceCreateCommand(manifest_id=manifest_id, model="openai:gpt-5"),
            )
            assert isinstance(result, LifecycleOperationResult)
            assert (await _operation(client, result)).state is OperationState.SUCCEEDED
            created.append(result)

        first = hub.registry.instance(created[0].instance_id)
        second = hub.registry.instance(created[1].instance_id)
        assert first is not None
        assert second is not None
        first_bind = next(item for item in first.storage if item.kind is StorageKind.BIND)
        alias = str(Path(first_bind.source) / ".." / Path(first_bind.source).name / "nested")

        with pytest.raises(ValueError, match="already owned"):
            hub.registry.record_preparation(
                second.instance_id,
                ImageArtifact(
                    image_id=second.image_id or "",
                    revision=second.source_revision or "",
                    dependency_id="sha256:dependencies",
                    base_images=("python@sha256:base",),
                ),
                (
                    StorageItem(
                        kind=StorageKind.BIND,
                        source=alias,
                        destination="/conflict",
                        writable=True,
                    ),
                ),
            )

    asyncio.run(scenario())


def test_an_operation_reports_every_step_it_ran(tmp_path):
    async def scenario() -> None:
        hub = Hub(tmp_path / "hub", runtime=FakeRuntime(), images=FakeImages())
        client = _client(hub)
        created = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(manifest_id="alice", model="openai:gpt-5"),
        )
        assert isinstance(created, LifecycleOperationResult)

        outcome = await _operation(client, created)

        assert outcome.state is OperationState.SUCCEEDED
        assert [(step.name, step.state) for step in outcome.steps] == [
            ("configure", OperationState.SUCCEEDED),
            ("image", OperationState.SUCCEEDED),
            ("container", OperationState.SUCCEEDED),
        ]
        assert outcome.steps[-1].detail == "Instance prepared and stopped."

    asyncio.run(scenario())


def test_a_failed_operation_marks_the_step_that_failed(tmp_path):
    async def scenario() -> None:
        hub = Hub(
            tmp_path / "hub",
            runtime=FakeRuntime(),
            images=FakeImages(failure="no such revision"),
        )
        client = _client(hub)
        created = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(manifest_id="alice", model="openai:gpt-5"),
        )
        assert isinstance(created, LifecycleOperationResult)

        outcome = await _operation(client, created)

        assert outcome.state is OperationState.FAILED
        assert [(step.name, step.state) for step in outcome.steps] == [
            ("configure", OperationState.SUCCEEDED),
            ("image", OperationState.FAILED),
        ]
        assert outcome.steps[-1].detail == "no such revision"

    asyncio.run(scenario())


def test_status_carries_the_operation_a_client_lost_the_response_to(tmp_path):
    async def scenario() -> None:
        runtime = HeldRuntime()
        hub = Hub(tmp_path / "hub", runtime=runtime, images=FakeImages())
        client = _client(hub)
        created = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(manifest_id="alice", model="openai:gpt-5"),
        )
        assert isinstance(created, LifecycleOperationResult)
        assert (await _operation(client, created)).state is OperationState.SUCCEEDED

        started = await client.call(
            INSTANCE_START,
            InstanceStartCommand(instance_id=created.instance_id),
        )
        assert isinstance(started, LifecycleOperationResult)
        await asyncio.wait_for(runtime.holding.wait(), timeout=5)
        during = await client.call(
            INSTANCE_STATUS,
            InstanceStatusCommand(instance_id=created.instance_id),
        )
        runtime.released.set()
        assert (await _operation(client, started)).state is OperationState.SUCCEEDED
        after = await client.call(
            INSTANCE_STATUS,
            InstanceStatusCommand(instance_id=created.instance_id),
        )

        assert not isinstance(during, ErrorEnvelope)
        assert during.active_operation_id == started.operation_id
        assert not isinstance(after, ErrorEnvelope)
        assert after.active_operation_id is None

    asyncio.run(scenario())


def test_a_second_start_keeps_the_operation_a_client_can_still_find(tmp_path):
    async def scenario() -> None:
        runtime = HeldRuntime()
        hub = Hub(tmp_path / "hub", runtime=runtime, images=FakeImages())
        client = _client(hub)
        created = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(manifest_id="alice", model="openai:gpt-5"),
        )
        assert isinstance(created, LifecycleOperationResult)
        assert (await _operation(client, created)).state is OperationState.SUCCEEDED

        started = await client.call(
            INSTANCE_START,
            InstanceStartCommand(instance_id=created.instance_id),
        )
        assert isinstance(started, LifecycleOperationResult)
        await asyncio.wait_for(runtime.holding.wait(), timeout=5)
        again = await client.call(
            INSTANCE_START,
            InstanceStartCommand(instance_id=created.instance_id),
        )
        during = await client.call(
            INSTANCE_STATUS,
            InstanceStatusCommand(instance_id=created.instance_id),
        )
        runtime.released.set()
        assert (await _operation(client, started)).state is OperationState.SUCCEEDED

        assert isinstance(again, LifecycleOperationResult)
        assert again.operation_id == started.operation_id
        assert not isinstance(during, ErrorEnvelope)
        assert during.active_operation_id == started.operation_id
        assert runtime.started == [str(created.instance_id)]

    asyncio.run(scenario())


def test_a_restarted_hub_can_start_an_instance_whose_start_was_interrupted(tmp_path):
    async def scenario() -> None:
        directory = tmp_path / "hub"
        hub = Hub(directory, runtime=FakeRuntime(), images=FakeImages())
        client = _client(hub)
        created = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(manifest_id="alice", model="openai:gpt-5"),
        )
        assert isinstance(created, LifecycleOperationResult)
        assert (await _operation(client, created)).state is OperationState.SUCCEEDED
        stale = uuid4()
        assert (
            hub.registry.begin_operation(
                stale,
                created.instance_id,
                OperationKind.START,
                "Start queued.",
            )
            == stale
        )

        runtime = FakeRuntime()
        restarted = Hub(directory, runtime=runtime, images=FakeImages())
        restarted_client = _client(restarted)
        failed = await restarted_client.call(
            OPERATION_GET,
            OperationGetCommand(operation_id=stale),
        )
        started = await restarted_client.call(
            INSTANCE_START,
            InstanceStartCommand(instance_id=created.instance_id),
        )
        assert isinstance(started, LifecycleOperationResult)
        outcome = await _operation(restarted_client, started)

        assert not isinstance(failed, ErrorEnvelope)
        assert failed.state is OperationState.FAILED
        assert failed.detail == "The hub stopped before this operation finished."
        assert started.operation_id != stale
        assert outcome.state is OperationState.SUCCEEDED
        assert runtime.started == [str(created.instance_id)]

    asyncio.run(scenario())


def test_a_cancelled_start_can_be_started_again(tmp_path):
    async def scenario() -> None:
        runtime = HeldRuntime()
        hub = Hub(tmp_path / "hub", runtime=runtime, images=FakeImages())
        client = _client(hub)
        created = await client.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(manifest_id="alice", model="openai:gpt-5"),
        )
        assert isinstance(created, LifecycleOperationResult)
        assert (await _operation(client, created)).state is OperationState.SUCCEEDED
        started = await client.call(
            INSTANCE_START,
            InstanceStartCommand(instance_id=created.instance_id),
        )
        assert isinstance(started, LifecycleOperationResult)
        await asyncio.wait_for(runtime.holding.wait(), timeout=5)
        assert len(hub._tasks) == 1
        task = next(iter(hub._tasks))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        failed = await client.call(
            OPERATION_GET,
            OperationGetCommand(operation_id=started.operation_id),
        )
        runtime.released.set()
        again = await client.call(
            INSTANCE_START,
            InstanceStartCommand(instance_id=created.instance_id),
        )
        assert isinstance(again, LifecycleOperationResult)
        outcome = await _operation(client, again)

        assert not isinstance(failed, ErrorEnvelope)
        assert failed.state is OperationState.FAILED
        assert again.operation_id != started.operation_id
        assert outcome.state is OperationState.SUCCEEDED
        assert runtime.started == [str(created.instance_id)]

    asyncio.run(scenario())


def test_hub_reads_and_mutations_ask_for_different_scopes(tmp_path):
    async def scenario() -> None:
        hub = Hub(tmp_path / "hub", runtime=FakeRuntime(), images=FakeImages())
        reader = _client(hub, {Scope.HUB_READ})
        agent = _client(hub, set(INSTANCE_SCOPES) | set(CONTROL_SCOPES))

        listed = await reader.call(INSTANCE_LIST, InstanceListCommand())
        refused = await reader.call(
            INSTANCE_CREATE,
            InstanceCreateCommand(manifest_id="alice", model="openai:gpt-5"),
        )
        unreachable = await agent.call(INSTANCE_LIST, InstanceListCommand())

        assert not isinstance(listed, ErrorEnvelope)
        assert isinstance(refused, ErrorEnvelope)
        assert refused.code is ErrorCode.PERMISSION_DENIED
        assert isinstance(unreachable, ErrorEnvelope)
        assert unreachable.code is ErrorCode.PERMISSION_DENIED

    asyncio.run(scenario())


def test_no_scope_an_instance_grants_carries_hub_authority():
    hub_scopes = {Scope.HUB_READ, Scope.HUB_ADMIN}

    assert INSTANCE_SCOPES & hub_scopes == set()
    assert CONTROL_SCOPES & hub_scopes == set()
    assert {INSTANCE_LIST.scope, INSTANCE_STATUS.scope, INSTANCE_LOGS.scope} == {Scope.HUB_READ}
    assert {INSTANCE_CREATE.scope, INSTANCE_START.scope} == {Scope.HUB_ADMIN}
