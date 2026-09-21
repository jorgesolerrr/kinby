"""Typed hub lifecycle service."""

from __future__ import annotations

import asyncio
import fcntl
import os
import re
import shutil
from collections.abc import Collection, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import IO
from uuid import UUID, uuid4

from dotenv import dotenv_values

from kinby.contracts import (
    INSTANCE_CREATE,
    INSTANCE_LIST,
    INSTANCE_LOGS,
    INSTANCE_RECREATE,
    INSTANCE_SECRETS_SET,
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
    InstanceRecreateCommand,
    InstanceSecretsSetCommand,
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
from kinby.core.errors import (
    LifecycleOperationInFlight,
    LifecycleOperationNotFound,
    ManagedInstanceNotFound,
)
from kinby.hub.access import HubAccess, new_control_token
from kinby.hub.control import (
    ControlConnectionLost,
    ControlEndpoint,
    ControlUnreachable,
    HttpInstanceControl,
    IncompatibleLifecycleEndpoint,
    InstanceControl,
)
from kinby.hub.models import (
    ContainerRuntime,
    ImagePreparation,
    ImageSelection,
    InstanceEndpoint,
    InstanceSpec,
    InstanceUnreachable,
    LifecycleRecovery,
    PreparedImage,
    RuntimeStatus,
)
from kinby.hub.recovery import recover_lifecycle
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
#: Pause before calling a drain again, so a socket that closes immediately does not spin.
_DRAIN_RETRY_SECONDS = 0.2
_RUNTIME_STOPPED = frozenset({"absent", "created", "stopped", "failed"})
_INTERRUPTED_OPERATION = "The hub stopped before this operation finished."


class HubAlreadyRunning(RuntimeError):
    """Another process holds this hub directory."""


def _acquire_directory(directory: Path) -> IO[str]:
    """Hold this directory until the process exits, or until close.

    The kernel releases the lock when the process dies, so a later hub can tell
    that unfinished operations belong to a process that is gone.
    """
    handle = (directory / "hub.lock").open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise HubAlreadyRunning(f"Another hub is already running for {directory}.") from None
    return handle


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
        control: InstanceControl | None = None,
        docker_host_directory: Path | None = None,
    ) -> None:
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._directory_lock = _acquire_directory(self.directory)
        try:
            self.instances_directory = self.directory / "instances"
            self.instances_directory.mkdir(parents=True, exist_ok=True)
            self._docker_host_directory = (
                Path(docker_host_directory).resolve()
                if docker_host_directory is not None
                else self.directory
            )
            self.registry = HubRegistry(self.directory)
            self.registry.fail_interrupted_operations(_INTERRUPTED_OPERATION)
            self.access = HubAccess(self.registry)
            self._runtime = runtime
            self._images = images
            self._control = control if control is not None else HttpInstanceControl()
            self._locks: dict[UUID, asyncio.Lock] = {}
            self._stopping: dict[UUID, PendingStop] = {}
            self._tasks: set[asyncio.Task[None]] = set()
            self.dispatcher = Dispatcher()
            self.dispatcher.register(INSTANCE_CREATE, self.create)
            self.dispatcher.register(INSTANCE_START, self.start)
            self.dispatcher.register(INSTANCE_STOP, self.stop)
            self.dispatcher.register(INSTANCE_RECREATE, self.recreate)
            self.dispatcher.register(INSTANCE_SECRETS_SET, self.set_secrets)
            self.dispatcher.register(INSTANCE_LIST, self.list)
            self.dispatcher.register(INSTANCE_STATUS, self.status)
            self.dispatcher.register(INSTANCE_LOGS, self.logs)
            self.dispatcher.register(OPERATION_GET, self.operation)
        except BaseException:
            self._directory_lock.close()
            raise

    def close(self) -> None:
        """Release this directory so another process can own it."""
        self._directory_lock.close()

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
        self.registry.advance_operation(
            operation_id,
            "configure",
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
                self._write_secrets(staging / ".env", secrets)
                inspect_instance(staging)
            self.registry.advance_operation(operation_id, "image", "Preparing selected image.")
            prepared = await self._images.prepare(selection)
            package = self._package(prepared, selection, secrets)
            if package is not None:
                init_instance(staging, model=model, package=package)
                self._write_configuration(staging, record.manifest_id, record.persona_name)
                self._write_secrets(staging / ".env", secrets)
                inspect_instance(staging)
            artifact = prepared.artifact
            if record.path.exists():
                raise FileExistsError(f"Instance directory already exists: {record.path}")
            staging.replace(record.path)
            storage = self._storage(record.instance_id, record.path)
            self.registry.record_preparation(record.instance_id, artifact, storage)
            self.registry.advance_operation(
                operation_id,
                "container",
                "Creating the instance container.",
            )
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
            self.registry.finish_operation(
                operation_id,
                OperationState.SUCCEEDED,
                "Instance prepared and stopped.",
            )
        except Exception as exc:
            if staging.exists():
                shutil.rmtree(staging)
            self.registry.finish_operation(
                operation_id,
                OperationState.FAILED,
                self._redact(str(exc) or type(exc).__name__, secrets.values()),
            )

    async def start(self, command: InstanceStartCommand) -> LifecycleOperationResult:
        record = self._prepared_instance(command.instance_id)
        operation_id = uuid4()
        opened = self.registry.begin_operation(
            operation_id,
            record.instance_id,
            OperationKind.START,
            "Start queued.",
        )
        if opened == operation_id:
            self._schedule(self._start(operation_id, record.instance_id))
        return LifecycleOperationResult(
            operation_id=opened,
            instance_id=record.instance_id,
        )

    async def _start(self, operation_id: UUID, instance_id: UUID) -> None:
        try:
            await self._run_start(operation_id, instance_id)
        except asyncio.CancelledError:
            self._fail_if_unfinished(operation_id)
            raise

    async def _run_start(self, operation_id: UUID, instance_id: UUID) -> None:
        lock = self._locks.setdefault(instance_id, asyncio.Lock())
        async with lock:
            record = self._prepared_instance(instance_id)
            secrets = self._environment(record.path).values()
            self.registry.advance_operation(operation_id, "start", "Starting selected image.")
            self.registry.set_intended_state(instance_id, IntendedState.RUNNING)
            try:
                await self._runtime.start(record.runtime_id)
            except Exception as exc:
                self.registry.finish_operation(
                    operation_id,
                    OperationState.FAILED,
                    self._redact(str(exc) or type(exc).__name__, secrets),
                )
                return
            self.registry.finish_operation(
                operation_id,
                OperationState.SUCCEEDED,
                "Instance started.",
            )

    async def set_secrets(self, command: InstanceSecretsSetCommand) -> LifecycleOperationResult:
        """Replace the named values in this instance's secrets. No value travels back out."""
        record = self._prepared_instance(command.instance_id)
        secrets = {name: value.get_secret_value() for name, value in command.secrets.items()}
        operation_id = uuid4()
        self.registry.record_operation(
            operation_id,
            record.instance_id,
            OperationKind.SECRETS,
            "Secret replacement queued.",
        )
        self._schedule(self._set_secrets(operation_id, record.instance_id, secrets))
        return LifecycleOperationResult(
            operation_id=operation_id,
            instance_id=record.instance_id,
        )

    async def _set_secrets(
        self,
        operation_id: UUID,
        instance_id: UUID,
        secrets: dict[str, str],
    ) -> None:
        try:
            # The lock serializes this write against every other lifecycle mutation.
            async with self._locks.setdefault(instance_id, asyncio.Lock()):
                record = self._prepared_instance(instance_id)
                self.registry.advance_operation(
                    operation_id,
                    "secrets",
                    "Replacing the instance's secrets.",
                )
                try:
                    self._validate_secret_names(secrets)
                    self._replace_secrets(record.path, secrets)
                except Exception as exc:
                    self._fail(
                        operation_id,
                        record,
                        str(exc) or type(exc).__name__,
                        secrets.values(),
                    )
                    return
                self.registry.finish_operation(
                    operation_id,
                    OperationState.SUCCEEDED,
                    "Secrets replaced. Recreate the container to apply them.",
                )
        except asyncio.CancelledError:
            self._fail_if_unfinished(operation_id)
            raise

    async def stop(self, command: InstanceStopCommand) -> LifecycleOperationResult:
        """Stop the instance gracefully, or escalate the stop already running to a force stop."""
        record = self._prepared_instance(command.instance_id)
        pending = self._stopping.get(record.instance_id)
        if pending is None:
            operation_id = uuid4()
            opened = self.registry.begin_operation(
                operation_id,
                record.instance_id,
                OperationKind.STOP,
                "Stop queued.",
            )
            pending = PendingStop(opened, asyncio.Event())
            self._stopping[record.instance_id] = pending
            if opened == operation_id:
                self._schedule(self._stop(record.instance_id, pending))
        if command.force:
            pending.force.set()
        return LifecycleOperationResult(
            operation_id=pending.operation_id,
            instance_id=record.instance_id,
        )

    async def recreate(self, command: InstanceRecreateCommand) -> LifecycleOperationResult:
        """Replace this instance's container from the image and secrets already recorded for it."""
        record = self._prepared_instance(command.instance_id)
        active = self.registry.active_operation(record.instance_id)
        if active is not None:
            raise LifecycleOperationInFlight(
                f'Lifecycle operation "{active}" is still running for this instance.'
            )
        operation_id = uuid4()
        self.registry.record_operation(
            operation_id,
            record.instance_id,
            OperationKind.RECREATE,
            "Recreation queued.",
        )
        self._schedule(self._recreate(operation_id, record.instance_id))
        return LifecycleOperationResult(
            operation_id=operation_id,
            instance_id=record.instance_id,
        )

    async def _recreate(self, operation_id: UUID, instance_id: UUID) -> None:
        try:
            async with self._locks.setdefault(instance_id, asyncio.Lock()):
                record = self._prepared_instance(instance_id)
                try:
                    await self._replace_container(operation_id, record)
                except Exception as exc:
                    self._fail(operation_id, record, str(exc) or type(exc).__name__)
                    return
                self.registry.finish_operation(
                    operation_id,
                    OperationState.SUCCEEDED,
                    "Container recreated.",
                )
        except asyncio.CancelledError:
            self._fail_if_unfinished(operation_id)
            raise

    async def _replace_container(self, operation_id: UUID, record: ManagedInstance) -> None:
        """Take the existing container down the established way, then build its replacement."""
        image = self._revalidated_image(operation_id, record)
        if (await self._runtime.status(record.runtime_id)).state != "absent":
            await self._stop_running(record, PendingStop(operation_id, asyncio.Event()))
            self._record(operation_id, "remove", "Removing the container, keeping its storage.")
            await self._runtime.remove(record.runtime_id)
        self._record(operation_id, "create", f"Creating the container from image {image}.")
        await self._runtime.create(
            InstanceSpec(
                instance_id=record.runtime_id,
                image=image,
                env=self._environment(record.path),
                port=_INSTANCE_PORT,
            )
        )
        if record.intended_state is IntendedState.RUNNING:
            self._record(operation_id, "start", "Restoring the intended running state.")
            await self._runtime.start(record.runtime_id)

    def _revalidated_image(self, operation_id: UUID, record: ManagedInstance) -> str:
        """Revalidate what a replacement is built from. No revision is resolved here."""
        self._record(
            operation_id,
            "validate",
            f"Revalidating the retained configuration of instance {record.instance_id}.",
        )
        inspect_instance(record.path)
        conflict = self.registry.conflicting_storage(record.instance_id, record.storage)
        if conflict is not None:
            raise ValueError(
                f'Storage source "{conflict.source}" is owned by another instance, '
                "so this container was not replaced."
            )
        if not record.image_id:
            raise ValueError("This instance has no recorded image to recreate its container from.")
        return record.image_id

    async def _stop(self, instance_id: UUID, pending: PendingStop) -> None:
        operation_id = pending.operation_id
        try:
            try:
                # The lock serializes stops against other mutations; escalation never takes it.
                async with self._locks.setdefault(instance_id, asyncio.Lock()):
                    record = self._prepared_instance(instance_id)
                    self.registry.set_intended_state(instance_id, IntendedState.STOPPED)
                    try:
                        detail = await self._stop_running(record, pending)
                    except Exception as exc:
                        self._fail(operation_id, record, str(exc) or type(exc).__name__)
                        return
                    self.registry.finish_operation(operation_id, OperationState.SUCCEEDED, detail)
            except asyncio.CancelledError:
                self._fail_if_unfinished(operation_id)
                raise
        finally:
            self._stopping.pop(instance_id, None)

    async def _stop_running(self, record: ManagedInstance, pending: PendingStop) -> str:
        """Drain the instance through its own runtime, then take the container down."""
        operation_id = pending.operation_id
        if (await self._runtime.status(record.runtime_id)).state in _RUNTIME_STOPPED:
            return "Instance was already stopped."
        self._record(operation_id, "probe", "Checking the instance's lifecycle endpoint.")
        endpoint = await self._endpoint(record)
        probed = await self._control.probe(endpoint)
        if Capability.DRAIN not in probed.capabilities:
            raise IncompatibleLifecycleEndpoint(
                "The instance's lifecycle endpoint cannot drain, so it was left running. "
                "Update the instance before stopping it."
            )
        state = await self._drain(operation_id, endpoint, pending)
        self._record(
            operation_id,
            "result",
            f"Instance {state.value}. Stopping the container."
            if state is not None
            else "The instance did not report its drain. Terminating the container.",
        )
        await self._runtime.stop(record.runtime_id, grace_seconds=STOP_GRACE_SECONDS)
        await self._observe_stop(record.runtime_id)
        self._record(operation_id, "container", "Stopping the container.")
        return "Instance stopped."

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
            "drain",
            "Interrupting active work." if forced else "Draining accepted work.",
        )
        draining = asyncio.create_task(self._call_drain(operation_id, endpoint, pending))
        escalation = asyncio.create_task(pending.force.wait())
        interrupting: asyncio.Task[DrainState] | None = None
        try:
            if not forced:
                await asyncio.wait((draining, escalation), return_when=asyncio.FIRST_COMPLETED)
                if draining.done():
                    return draining.result()
                self._record(
                    operation_id,
                    "force",
                    "Force stop requested. Interrupting active work.",
                )
                interrupting = asyncio.create_task(
                    self._call_drain(operation_id, endpoint, pending)
                )
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

    async def _call_drain(
        self,
        operation_id: UUID,
        endpoint: ControlEndpoint,
        pending: PendingStop,
    ) -> DrainState:
        """Call the drain again when the socket drops. The instance keeps draining either way."""
        reported = False
        while True:
            try:
                return await self._control.drain(endpoint, force=pending.force.is_set())
            except ControlConnectionLost:
                if not reported:
                    self._record(
                        operation_id,
                        "reconnect",
                        "The control socket closed. Waiting for the drain again.",
                    )
                    reported = True
                await asyncio.sleep(_DRAIN_RETRY_SECONDS)

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

    async def _endpoint(self, record: ManagedInstance) -> ControlEndpoint:
        token = self._environment(record.path).get(CONTROL_TOKEN_VARIABLE)
        address = await self._runtime.address(record.runtime_id)
        if token is None or address is None:
            raise ControlUnreachable("The instance's lifecycle endpoint cannot be reached.")
        return ControlEndpoint(address=address, token=ControlToken(token))

    def _record(self, operation_id: UUID, step: str, detail: str) -> None:
        self.registry.advance_operation(operation_id, step, detail)

    def _fail(
        self,
        operation_id: UUID,
        record: ManagedInstance,
        detail: str,
        submitted: Collection[str] = (),
    ) -> None:
        """Fail with every secret cut from the detail: the stored ones and the submitted ones."""
        self.registry.finish_operation(
            operation_id,
            OperationState.FAILED,
            self._redact(detail, [*submitted, *self._environment(record.path).values()]),
        )

    def _fail_if_unfinished(self, operation_id: UUID) -> None:
        current = self.registry.operation(operation_id)
        unfinished = {OperationState.PENDING, OperationState.RUNNING}
        if current is not None and current.state in unfinished:
            self.registry.finish_operation(
                operation_id,
                OperationState.FAILED,
                _INTERRUPTED_OPERATION,
            )

    async def recover(self) -> LifecycleRecovery:
        """Reconcile every managed instance against what the container runtime still has."""
        return await recover_lifecycle(self.registry, self._runtime, self._restore_start)

    async def _restore_start(self, instance_id: UUID) -> OperationState:
        """Start one instance again under its own lifecycle operation, so the attempt is visible."""
        operation_id = self.registry.begin_operation(
            uuid4(),
            instance_id,
            OperationKind.START,
            "Restoring the intended running state.",
        )
        await self._run_start(operation_id, instance_id)
        finished = self.registry.operation(operation_id)
        return finished.state if finished is not None else OperationState.FAILED

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

    async def endpoint(self, instance_id: UUID) -> InstanceEndpoint | InstanceUnreachable:
        """Answer where a public route may reach one instance, and with which control token."""
        record = self.registry.instance(instance_id)
        if record is None or not record.prepared:
            return InstanceUnreachable.MISSING
        try:
            address = await self._runtime.address(record.runtime_id)
        except Exception:
            return InstanceUnreachable.UNAVAILABLE
        token = self._environment(record.path).get(CONTROL_TOKEN_VARIABLE)
        if address is None or not token:
            return InstanceUnreachable.UNAVAILABLE
        return InstanceEndpoint(url=address, control_token=ControlToken(token))

    async def signal_endpoint(self) -> InstanceEndpoint | InstanceUnreachable:
        """Reach the instance that kept the public webhook URL it was registered with."""
        alias = self.registry.signal_alias()
        if alias is None:
            return InstanceUnreachable.MISSING
        return await self.endpoint(alias)

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
    def _write_secrets(cls, path: Path, secrets: dict[str, str]) -> None:
        """Close the file to everyone else before the first secret byte lands in it."""
        body = "".join(f"{name}={cls._dotenv_value(value)}\n" for name, value in secrets.items())
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)
        path.chmod(0o600)

    @classmethod
    def _replace_secrets(cls, directory: Path, secrets: dict[str, str]) -> None:
        """Merge the submitted values in through one rename.

        A failure leaves the previous secrets in place and removes the staging file,
        so the values are not left in a second copy.
        """
        staging = directory / ".env.replacing"
        try:
            cls._write_secrets(staging, cls._environment(directory) | secrets)
            staging.replace(directory / ".env")
        except OSError:
            staging.unlink(missing_ok=True)
            raise

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
