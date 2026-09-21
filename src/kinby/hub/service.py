"""Typed hub lifecycle service."""

from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Collection, Coroutine
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from dotenv import dotenv_values

from kinby.contracts import (
    INSTANCE_CREATE,
    INSTANCE_LIST,
    INSTANCE_LOGS,
    INSTANCE_START,
    INSTANCE_STATUS,
    INSTANCE_STOP,
    OPERATION_GET,
    Capability,
    ControlToken,
    DrainState,
    InstanceCreateCommand,
    InstanceListCommand,
    InstanceListResult,
    InstanceLogsCommand,
    InstanceLogsResult,
    InstanceStartCommand,
    InstanceStatusCommand,
    InstanceStatusResult,
    InstanceStopCommand,
    IntendedState,
    LifecycleOperationResult,
    OperationGetCommand,
    OperationGetResult,
    OperationKind,
    OperationState,
    ProcessState,
    Readiness,
    StorageItem,
    StorageKind,
)
from kinby.core.contract_server import CONTROL_TOKEN_VARIABLE
from kinby.core.dispatcher import Dispatcher
from kinby.core.errors import LifecycleOperationNotFound, ManagedInstanceNotFound
from kinby.hub.access import HubAccess, new_control_token
from kinby.hub.control import (
    ControlEndpoint,
    IncompatibleLifecycleEndpoint,
    InstanceControl,
    InstanceUnreachable,
)
from kinby.hub.models import (
    ContainerRuntime,
    ImagePreparation,
    ImageSelection,
    InstanceSpec,
    PreparedImage,
    RuntimeStatus,
)
from kinby.hub.registry import HubRegistry, ManagedInstance
from kinby.instance import init_instance, inspect_instance
from kinby.packages import InstalledPackage

_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INSTANCE_HOST = "0.0.0.0"
_INSTANCE_PORT = 8787
#: How long a forced container may take to exit before the runtime terminates it.
STOP_GRACE_SECONDS = 30
#: How long a forced instance may take to report its own drain before the container goes down.
FORCE_ANSWER_SECONDS = 30
#: How often the hub looks for the container to have actually stopped, and for how long.
_STOP_POLL_SECONDS = 0.2
_STOP_OBSERVE_SECONDS = 120
_RUNTIME_STOPPED = frozenset({"absent", "created", "stopped", "failed"})


@dataclass(frozen=True)
class PendingStop:
    """The stop running on one instance, and the way a later request escalates it."""

    operation_id: UUID
    force: asyncio.Event


class Hub:
    """Own instance management without booting instance runtimes in this process."""

    def __init__(
        self,
        directory: Path,
        *,
        runtime: ContainerRuntime,
        images: ImagePreparation,
        control: InstanceControl,
        docker_host_directory: Path | None = None,
    ) -> None:
        self.directory = Path(directory).resolve()
        self.instances_directory = self.directory / "instances"
        self.instances_directory.mkdir(parents=True, exist_ok=True)
        self._docker_host_directory = (
            Path(docker_host_directory).resolve()
            if docker_host_directory is not None
            else self.directory
        )
        self.registry = HubRegistry(self.directory)
        self.access = HubAccess(self.registry)
        self._runtime = runtime
        self._images = images
        self._control = control
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._stopping: dict[UUID, PendingStop] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self.dispatcher = Dispatcher()
        self.dispatcher.register(INSTANCE_CREATE, self.create)
        self.dispatcher.register(INSTANCE_START, self.start)
        self.dispatcher.register(INSTANCE_STOP, self.stop)
        self.dispatcher.register(INSTANCE_LIST, self.list)
        self.dispatcher.register(INSTANCE_STATUS, self.status)
        self.dispatcher.register(INSTANCE_LOGS, self.logs)
        self.dispatcher.register(OPERATION_GET, self.operation)

    def _schedule(self, work: Coroutine[object, object, None]) -> None:
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def create(self, command: InstanceCreateCommand) -> LifecycleOperationResult:
        instance_id = uuid4()
        operation_id = uuid4()
        instance_path = self.instances_directory / str(instance_id)
        record = ManagedInstance(
            instance_id=instance_id,
            path=instance_path,
            manifest_id=command.manifest_id,
            persona_name=command.persona_name,
            requested_revision=command.revision,
            source_revision=None,
            image_id=None,
            intended_state=IntendedState.STOPPED,
            runtime_id=str(instance_id),
            prepared=False,
            storage=(),
            package=command.package,
        )
        self.registry.begin_create(record, operation_id)
        secrets = {name: value.get_secret_value() for name, value in command.secrets.items()}
        self._schedule(self._create(operation_id, record, command.model, secrets))
        return LifecycleOperationResult(operation_id=operation_id, instance_id=instance_id)

    async def _create(
        self,
        operation_id: UUID,
        record: ManagedInstance,
        model: str,
        secrets: dict[str, str],
    ) -> None:
        staging = record.path.with_name(f"{record.path.name}.creating")
        self.registry.update_operation(
            operation_id,
            OperationState.RUNNING,
            "Preparing instance configuration.",
        )
        try:
            self._validate_secret_names(secrets)
            secrets[CONTROL_TOKEN_VARIABLE] = new_control_token()
            selection = ImageSelection(
                revision=record.requested_revision,
                package=record.package,
            )
            if selection.package is None:
                init_instance(staging, model=model)
                self._write_configuration(staging, record.manifest_id, record.persona_name)
                self._write_secrets(staging, secrets)
                inspect_instance(staging)
            self.registry.update_operation(
                operation_id,
                OperationState.RUNNING,
                "Preparing selected image.",
            )
            prepared = await self._images.prepare(selection)
            package = self._package(prepared, selection, secrets)
            if package is not None:
                init_instance(staging, model=model, package=package)
                self._write_configuration(staging, record.manifest_id, record.persona_name)
                self._write_secrets(staging, secrets)
                inspect_instance(staging)
            artifact = prepared.artifact
            if record.path.exists():
                raise FileExistsError(f"Instance directory already exists: {record.path}")
            staging.replace(record.path)
            storage = self._storage(record.instance_id, record.path)
            self.registry.record_preparation(record.instance_id, artifact, storage)
            environment = self._environment(record.path)
            await self._runtime.create(
                InstanceSpec(
                    instance_id=record.runtime_id,
                    image=artifact.image_id,
                    env=environment,
                    port=_INSTANCE_PORT,
                )
            )
            self.registry.mark_prepared(record.instance_id)
            self.registry.update_operation(
                operation_id,
                OperationState.SUCCEEDED,
                "Instance prepared and stopped.",
            )
        except Exception as exc:
            if staging.exists():
                shutil.rmtree(staging)
            self.registry.update_operation(
                operation_id,
                OperationState.FAILED,
                self._redact(str(exc) or type(exc).__name__, secrets.values()),
            )

    async def start(self, command: InstanceStartCommand) -> LifecycleOperationResult:
        record = self._prepared_instance(command.instance_id)
        operation_id = uuid4()
        self.registry.begin_operation(
            operation_id,
            record.instance_id,
            OperationKind.START,
            "Start queued.",
        )
        self._schedule(self._start(operation_id, record.instance_id))
        return LifecycleOperationResult(
            operation_id=operation_id,
            instance_id=record.instance_id,
        )

    async def _start(self, operation_id: UUID, instance_id: UUID) -> None:
        lock = self._locks.setdefault(instance_id, asyncio.Lock())
        async with lock:
            record = self._prepared_instance(instance_id)
            secrets = self._environment(record.path).values()
            self.registry.update_operation(
                operation_id,
                OperationState.RUNNING,
                "Starting selected image.",
            )
            self.registry.set_intended_state(instance_id, IntendedState.RUNNING)
            try:
                await self._runtime.start(record.runtime_id)
            except Exception as exc:
                self.registry.update_operation(
                    operation_id,
                    OperationState.FAILED,
                    self._redact(str(exc) or type(exc).__name__, secrets),
                )
                return
            self.registry.update_operation(
                operation_id,
                OperationState.SUCCEEDED,
                "Instance started.",
            )

    async def stop(self, command: InstanceStopCommand) -> LifecycleOperationResult:
        """Stop the instance gracefully, or escalate the stop already running to a force stop."""
        record = self._prepared_instance(command.instance_id)
        pending = self._stopping.get(record.instance_id)
        if pending is None:
            pending = PendingStop(uuid4(), asyncio.Event())
            self._stopping[record.instance_id] = pending
            self.registry.begin_operation(
                pending.operation_id,
                record.instance_id,
                OperationKind.STOP,
                "Stop queued.",
            )
            self._schedule(self._stop(record.instance_id, pending))
        if command.force:
            pending.force.set()
        return LifecycleOperationResult(
            operation_id=pending.operation_id,
            instance_id=record.instance_id,
        )

    async def _stop(self, instance_id: UUID, pending: PendingStop) -> None:
        operation_id = pending.operation_id
        try:
            # The lock serializes stops against other mutations; escalation never takes it.
            async with self._locks.setdefault(instance_id, asyncio.Lock()):
                record = self._prepared_instance(instance_id)
                self.registry.set_intended_state(instance_id, IntendedState.STOPPED)
                try:
                    await self._stop_running(record, pending)
                except Exception as exc:
                    self._fail(operation_id, record, str(exc) or type(exc).__name__)
                    return
                self.registry.update_operation(
                    operation_id,
                    OperationState.SUCCEEDED,
                    "Instance stopped.",
                )
        finally:
            self._stopping.pop(instance_id, None)

    async def _stop_running(self, record: ManagedInstance, pending: PendingStop) -> None:
        """Drain the instance through its own runtime, then take the container down."""
        operation_id = pending.operation_id
        if (await self._runtime.status(record.runtime_id)).state in _RUNTIME_STOPPED:
            self._record(operation_id, "Instance was already stopped.")
            return
        self._record(operation_id, "Checking the instance's lifecycle endpoint.")
        endpoint = self._endpoint(record)
        probed = await self._control.probe(endpoint)
        if Capability.DRAIN not in probed.capabilities:
            raise IncompatibleLifecycleEndpoint(
                "The instance's lifecycle endpoint cannot drain, so it was left running. "
                "Update the instance before stopping it."
            )
        state = await self._drain(operation_id, endpoint, pending)
        self._record(
            operation_id,
            f"Instance {state.value}. Stopping the container."
            if state is not None
            else "The instance did not report its drain. Terminating the container.",
        )
        await self._runtime.stop(record.runtime_id, grace_seconds=STOP_GRACE_SECONDS)
        await self._observe_stop(record.runtime_id)

    async def _drain(
        self,
        operation_id: UUID,
        endpoint: ControlEndpoint,
        pending: PendingStop,
    ) -> DrainState | None:
        """Wait for the drain, escalating on request. None when a forced instance never answered."""
        forced = pending.force.is_set()
        self._record(
            operation_id,
            "Interrupting active work." if forced else "Draining accepted work.",
        )
        draining = asyncio.create_task(self._control.drain(endpoint, force=forced))
        escalation = asyncio.create_task(pending.force.wait())
        interrupting: asyncio.Task[DrainState] | None = None
        try:
            if not forced:
                await asyncio.wait((draining, escalation), return_when=asyncio.FIRST_COMPLETED)
                if draining.done():
                    return draining.result()
                self._record(operation_id, "Force stop requested. Interrupting active work.")
                interrupting = asyncio.create_task(self._control.drain(endpoint, force=True))
            try:
                return await asyncio.wait_for(
                    asyncio.shield(draining),
                    timeout=FORCE_ANSWER_SECONDS,
                )
            except TimeoutError:
                return None
        finally:
            for task in (escalation, draining, interrupting):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(task for task in (escalation, draining, interrupting) if task is not None),
                return_exceptions=True,
            )

    async def _observe_stop(self, runtime_id: str) -> None:
        """Report stopped only once the container itself is: an interrupt alone is not enough."""
        try:
            async with asyncio.timeout(_STOP_OBSERVE_SECONDS):
                while (await self._runtime.status(runtime_id)).state not in _RUNTIME_STOPPED:
                    await asyncio.sleep(_STOP_POLL_SECONDS)
        except TimeoutError as exc:
            raise TimeoutError(
                f"The container was still running {_STOP_OBSERVE_SECONDS} seconds "
                "after it was told to stop."
            ) from exc

    def _endpoint(self, record: ManagedInstance) -> ControlEndpoint:
        token = self._environment(record.path).get(CONTROL_TOKEN_VARIABLE)
        if token is None:
            raise InstanceUnreachable(
                "The instance has no control token, so its lifecycle endpoint cannot be reached."
            )
        return ControlEndpoint(
            address=self._runtime.address(record.runtime_id, _INSTANCE_PORT),
            token=ControlToken(token),
        )

    def _record(self, operation_id: UUID, detail: str) -> None:
        self.registry.update_operation(operation_id, OperationState.RUNNING, detail)

    def _fail(self, operation_id: UUID, record: ManagedInstance, detail: str) -> None:
        self.registry.update_operation(
            operation_id,
            OperationState.FAILED,
            self._redact(detail, self._environment(record.path).values()),
        )

    async def list(self, command: InstanceListCommand) -> InstanceListResult:
        return InstanceListResult(instances=self.registry.list_instances())

    async def status(self, command: InstanceStatusCommand) -> InstanceStatusResult:
        record = self._prepared_instance(command.instance_id)
        active = self.registry.active_operation(record.instance_id)
        try:
            status = await self._runtime.status(record.runtime_id)
        except Exception as exc:
            return InstanceStatusResult(
                instance_id=record.instance_id,
                process=ProcessState.UNAVAILABLE,
                readiness=Readiness.UNKNOWN,
                detail=self._redact(
                    str(exc) or type(exc).__name__,
                    self._environment(record.path).values(),
                ),
                active_operation_id=active,
            )
        process, readiness = self._status(status)
        return InstanceStatusResult(
            instance_id=record.instance_id,
            process=process,
            readiness=readiness,
            detail=self._redact(status.detail, self._environment(record.path).values()),
            active_operation_id=active,
        )

    async def logs(self, command: InstanceLogsCommand) -> InstanceLogsResult:
        record = self._prepared_instance(command.instance_id)
        chunks = [
            chunk
            async for chunk in self._runtime.logs(
                record.runtime_id,
                tail=command.tail,
                follow=False,
            )
        ]
        text = b"".join(chunks).decode(errors="replace")
        return InstanceLogsResult(
            instance_id=record.instance_id,
            text=self._redact(text, self._environment(record.path).values()),
        )

    async def operation(self, command: OperationGetCommand) -> OperationGetResult:
        result = self.registry.operation(command.operation_id)
        if result is None:
            raise LifecycleOperationNotFound(
                f'Lifecycle operation "{command.operation_id}" was not found.'
            )
        return result

    def _prepared_instance(self, instance_id: UUID) -> ManagedInstance:
        record = self.registry.instance(instance_id)
        if record is None or not record.prepared:
            raise ManagedInstanceNotFound(f'Instance "{instance_id}" was not found.')
        return record

    @staticmethod
    def _package(
        prepared: PreparedImage,
        selection: ImageSelection,
        secrets: dict[str, str],
    ) -> InstalledPackage | None:
        selected = selection.package
        installed = prepared.package
        if selected is None:
            if installed is not None:
                raise ValueError("A vanilla image unexpectedly exported a package.")
            return None
        if installed is None:
            raise ValueError(f'Package "{selected.id}" was not found in the prepared image.')
        descriptor = installed.descriptor
        expected = (selected.id, selected.distribution, selected.version)
        actual = (descriptor.id, descriptor.distribution, descriptor.version)
        if actual != expected:
            raise ValueError(
                f"Prepared package was {descriptor.id} from "
                f"{descriptor.distribution} {descriptor.version}; expected "
                f"{selected.id} from {selected.distribution} {selected.version}."
            )
        missing = [
            secret.name for secret in descriptor.required_secrets if secret.name not in secrets
        ]
        if missing:
            raise ValueError(f'Missing required secret: "{missing[0]}".')
        return installed

    def _storage(self, instance_id: UUID, path: Path) -> tuple[StorageItem, ...]:
        relative = path.relative_to(self.directory)
        host_path = self._docker_host_directory / relative
        runtime_id = str(instance_id)
        return (
            StorageItem(
                kind=StorageKind.BIND,
                source=str(host_path),
                destination="/instance",
                writable=True,
            ),
            StorageItem(
                kind=StorageKind.VOLUME,
                source=f"kinby-{runtime_id}-workspace",
                destination="/instance/workspace",
                writable=True,
            ),
            StorageItem(
                kind=StorageKind.VOLUME,
                source=f"kinby-{runtime_id}-codex",
                destination="/root/.codex",
                writable=True,
            ),
        )

    @staticmethod
    def _validate_secret_names(secrets: dict[str, str]) -> None:
        invalid = [name for name in secrets if _ENVIRONMENT_NAME.fullmatch(name) is None]
        if invalid:
            raise ValueError(f'Invalid environment variable name: "{invalid[0]}".')

    @staticmethod
    def _dotenv_value(value: str) -> str:
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

    @classmethod
    def _write_secrets(cls, directory: Path, secrets: dict[str, str]) -> None:
        path = directory / ".env"
        body = "".join(f"{name}={cls._dotenv_value(value)}\n" for name, value in secrets.items())
        path.write_text(body, encoding="utf-8")
        path.chmod(0o600)

    @staticmethod
    def _toml_string(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    @classmethod
    def _write_configuration(
        cls,
        directory: Path,
        manifest_id: str,
        persona_name: str | None,
    ) -> None:
        path = directory / "kinby.toml"
        lines = path.read_text(encoding="utf-8").splitlines()
        identity = [f"id = {cls._toml_string(manifest_id)}"]
        if persona_name is not None:
            identity.append(f"persona_name = {cls._toml_string(persona_name)}")
        for index, line in enumerate(lines):
            if line.startswith("id = "):
                lines[index : index + 1] = identity
                break
        lines.extend(("", "[serve]", f'listen = "{_INSTANCE_HOST}:{_INSTANCE_PORT}"'))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _environment(path: Path) -> dict[str, str]:
        return {
            name: value
            for name, value in dotenv_values(path / ".env", interpolate=False).items()
            if value is not None
        }

    @staticmethod
    def _redact(message: str, secrets: Collection[str]) -> str:
        redacted = message
        for secret in sorted(secrets, key=len, reverse=True):
            if secret:
                redacted = redacted.replace(secret, "[REDACTED]")
        return redacted

    @staticmethod
    def _status(status: RuntimeStatus) -> tuple[ProcessState, Readiness]:
        if status.state == "absent":
            return ProcessState.MISSING, Readiness.NOT_RUNNING
        if status.state == "created":
            return ProcessState.CREATED, Readiness.NOT_RUNNING
        if status.state == "starting":
            return ProcessState.STARTING, Readiness.STARTING
        if status.state == "stopped":
            return ProcessState.STOPPED, Readiness.NOT_RUNNING
        if status.state == "failed":
            return ProcessState.FAILED, Readiness.NOT_RUNNING
        if status.healthy is True:
            return ProcessState.RUNNING, Readiness.READY
        if status.healthy is False:
            return ProcessState.RUNNING, Readiness.UNHEALTHY
        return ProcessState.RUNNING, Readiness.UNKNOWN
