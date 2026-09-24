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
    INSTANCE_ADOPT,
    INSTANCE_ADOPT_PREVIEW,
    INSTANCE_CREATE,
    INSTANCE_DELETE,
    INSTANCE_DELETE_PREVIEW,
    INSTANCE_LIST,
    INSTANCE_LOGS,
    INSTANCE_RECREATE,
    INSTANCE_REMOVE,
    INSTANCE_RESTORE,
    INSTANCE_SECRETS_SET,
    INSTANCE_START,
    INSTANCE_STATUS,
    INSTANCE_STOP,
    INSTANCE_UPDATE,
    OPERATION_GET,
    Capability,
    ContainerOwner,
    ControlToken,
    DrainState,
    InstanceAdoptCommand,
    InstanceAdoptPreviewCommand,
    InstanceAdoptPreviewResult,
    InstanceCreateCommand,
    InstanceDeleteCommand,
    InstanceDeletePreviewCommand,
    InstanceDeletePreviewResult,
    InstanceListCommand,
    InstanceListResult,
    InstanceLogsCommand,
    InstanceLogsResult,
    InstanceRecreateCommand,
    InstanceRemoveCommand,
    InstanceRestoreCommand,
    InstanceSecretsSetCommand,
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
    PackagePin,
    PackageSelection,
    ProcessState,
    Readiness,
    StorageItem,
    StorageKind,
)
from kinby.core.contract_server import CONTROL_TOKEN_VARIABLE
from kinby.core.dispatcher import Dispatcher
from kinby.core.errors import (
    AdoptionBlocked,
    LifecycleOperationInFlight,
    LifecycleOperationNotFound,
    ManagedInstanceNotFound,
    PackagePinRefused,
)
from kinby.hub.access import HubAccess, new_control_token
from kinby.hub.adoption import INSTANCE_MOUNT, blocker, preflight, previous_manager
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
from kinby.instance import Instance, init_instance, inspect_instance
from kinby.packages import PACKAGE_CONFIG_NAME, InstalledPackage

_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INSTANCE_HOST = "0.0.0.0"
_INSTANCE_PORT = 8787
#: How long a forced container may take to exit before the runtime terminates it.
STOP_GRACE_SECONDS = 30
#: How long a forced instance may take to report its own drain before the container goes down.
FORCE_ANSWER_SECONDS = 30
#: How often the hub looks at a container, and how long it waits for a stop or for a boot.
_POLL_SECONDS = 0.2
_STOP_OBSERVE_SECONDS = 120
READY_OBSERVE_SECONDS = 120
#: Pause before calling a drain again, so a socket that closes immediately does not spin.
_DRAIN_RETRY_SECONDS = 0.2
_RUNTIME_STOPPED = frozenset({"absent", "created", "stopped", "failed"})
_INTERRUPTED_OPERATION = "The hub stopped before this operation finished."


class HubAlreadyRunning(RuntimeError):
    """Another process holds this hub directory."""


class ReplacementNotReady(RuntimeError):
    """The replacement container never reported a booted instance behind it."""


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


def _pinned_package(record: ManagedInstance, pin: PackagePin | None) -> PackageSelection | None:
    """The package an update carries: the instance's own, moved to the pinned commit if any.

    A pin never switches packages (ADR 0040), and it moves only a package that comes from git.
    """
    current = record.package
    if pin is None:
        return current
    if current is None or current.id != pin.id:
        running = f'package "{current.id}"' if current is not None else "no package"
        raise PackagePinRefused(
            f'Instance "{record.instance_id}" runs {running}, not "{pin.id}". '
            "An update never switches packages."
        )
    match current.version:
        case PackageCommit(url=url):
            return current.model_copy(update={"version": PackageCommit(url=url, sha=pin.sha)})
        case version:
            raise PackagePinRefused(
                f'Package "{pin.id}" is installed from the package index at version {version}, '
                "so there is no git repository to move to another commit."
            )


def _delete_directory(directory: Path) -> None:
    """A retry finds a directory an interrupted attempt already deleted, and that is done."""
    try:
        shutil.rmtree(directory)
    except FileNotFoundError:
        return


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
            self.dispatcher.register(INSTANCE_REMOVE, self.remove)
            self.dispatcher.register(INSTANCE_RESTORE, self.restore)
            self.dispatcher.register(INSTANCE_DELETE_PREVIEW, self.delete_preview)
            self.dispatcher.register(INSTANCE_DELETE, self.delete)
            self.dispatcher.register(INSTANCE_UPDATE, self.update)
            self.dispatcher.register(INSTANCE_SECRETS_SET, self.set_secrets)
            self.dispatcher.register(INSTANCE_ADOPT_PREVIEW, self.adopt_preview)
            self.dispatcher.register(INSTANCE_ADOPT, self.adopt)
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
                    storage=storage,
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
        record = self._active_instance(command.instance_id)
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
            try:
                record = self._active_instance(instance_id)
            except ManagedInstanceNotFound as exc:
                self.registry.finish_operation(operation_id, OperationState.FAILED, str(exc))
                return
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
        record = self._active_instance(command.instance_id)
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
                try:
                    record = self._active_instance(instance_id)
                except ManagedInstanceNotFound as exc:
                    self.registry.finish_operation(operation_id, OperationState.FAILED, str(exc))
                    return
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
        record = self._active_instance(command.instance_id)
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

    async def adopt_preview(
        self,
        command: InstanceAdoptPreviewCommand,
    ) -> InstanceAdoptPreviewResult:
        """Preview a handoff: what the hub would take over, and what stands in the way."""
        return await preflight(command, self.registry, self._runtime, self._control)

    async def adopt(self, command: InstanceAdoptCommand) -> LifecycleOperationResult:
        """Take an existing instance over, preserving its identity, storage and webhook URL."""
        preview = await preflight(command, self.registry, self._runtime, self._control)
        stopped = blocker(preview)
        if stopped is not None:
            raise AdoptionBlocked(f"This instance was not adopted. {stopped.detail}")
        record = ManagedInstance(
            instance_id=preview.instance_id,
            path=preview.path,
            manifest_id=preview.manifest_id,
            persona_name=preview.persona_name,
            requested_revision="",
            source_revision=None,
            image_id=preview.image_id,
            intended_state=await self._observed_state(preview.runtime_id),
            runtime_id=preview.runtime_id,
            prepared=False,
            storage=tuple(preview.storage),
        )
        operation_id = uuid4()
        try:
            opened = self.registry.begin_adoption(record, operation_id)
        except ValueError as exc:
            raise AdoptionBlocked(f"This instance was not adopted. {exc}") from exc
        if opened != operation_id:
            raise LifecycleOperationInFlight(
                f'Lifecycle operation "{opened}" is still running for this instance.'
            )
        self._schedule(self._adopt(operation_id, record, command))
        return LifecycleOperationResult(
            operation_id=operation_id,
            instance_id=record.instance_id,
        )

    async def _observed_state(self, runtime_id: str) -> IntendedState:
        """The state the instance is in now becomes the state the hub keeps it in."""
        running = (await self._runtime.status(runtime_id)).state not in _RUNTIME_STOPPED
        return IntendedState.RUNNING if running else IntendedState.STOPPED

    async def _adopt(
        self,
        operation_id: UUID,
        record: ManagedInstance,
        command: InstanceAdoptCommand,
    ) -> None:
        try:
            async with self._locks.setdefault(record.instance_id, asyncio.Lock()):
                try:
                    await self._hand_over(operation_id, record, command)
                except Exception as exc:
                    self._fail(operation_id, record, str(exc) or type(exc).__name__)
                    return
                self.registry.finish_operation(
                    operation_id,
                    OperationState.SUCCEEDED,
                    "The hub owns this instance.",
                )
        except asyncio.CancelledError:
            self._fail_if_unfinished(operation_id)
            raise

    async def _hand_over(
        self,
        operation_id: UUID,
        record: ManagedInstance,
        command: InstanceAdoptCommand,
    ) -> None:
        """Take the previous runtime down, then bring the same storage up under this hub.

        Ownership moves in one recorded step, once the previous runtime is down and
        before its container goes. From there on the data is this hub's, so an
        interruption leaves a missing container rather than an unclaimed instance.
        """
        await self._relinquish(operation_id, record, command)
        self._record(operation_id, "configure", "Writing this hub's control token beside it.")
        self._replace_secrets(record.path, {CONTROL_TOKEN_VARIABLE: new_control_token()})
        self._record(operation_id, "claim", "The previous runtime is down. The hub owns this now.")
        self.registry.mark_prepared(record.instance_id)
        self._record(operation_id, "remove", "Removing the previous container, keeping storage.")
        await self._runtime.remove(record.runtime_id)
        self._record(operation_id, "create", "Creating the container under this hub.")
        await self._runtime.create(
            InstanceSpec(
                instance_id=record.runtime_id,
                image=record.image_id or "",
                storage=record.storage,
                env=self._environment(record.path),
                port=_INSTANCE_PORT,
            )
        )
        if record.intended_state is IntendedState.RUNNING:
            self._record(operation_id, "start", "Starting the instance the hub now owns.")
            await self._runtime.start(record.runtime_id)
        if command.claim_signals:
            self._record(operation_id, "signals", "Keeping the established webhook URL here.")
            self.registry.set_signal_alias(record.instance_id)

    async def _relinquish(
        self,
        operation_id: UUID,
        record: ManagedInstance,
        command: InstanceAdoptCommand,
    ) -> None:
        """Stop the previous runtime. It drains when it can, and is interrupted when it cannot.

        The preflight already settled which of those the operator gets, so a graceful
        handoff never quietly becomes the interrupting one.
        """
        if (await self._runtime.status(record.runtime_id)).state in _RUNTIME_STOPPED:
            self._record(operation_id, "stopped", "The previous runtime was already stopped.")
            return
        self._record(operation_id, "probe", "Checking the previous runtime's endpoint.")
        endpoint = await self._probe_endpoint(record)
        probed = await self._control.probe(endpoint)
        if Capability.DRAIN in probed.capabilities:
            await self._take_down(record, PendingStop(operation_id, asyncio.Event()))
            return
        if not command.acknowledge_interrupting_stop:
            raise IncompatibleLifecycleEndpoint(
                "The previous runtime can no longer drain, and the interrupting stop was not "
                "acknowledged, so it was left running."
            )
        self._record(operation_id, "interrupt", "The previous runtime cannot drain. Stopping it.")
        await self._halt_container(record.runtime_id)

    async def recreate(self, command: InstanceRecreateCommand) -> LifecycleOperationResult:
        """Replace this instance's container from the image and secrets already recorded for it."""
        record = self._claimed(self._active_instance(command.instance_id))
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

    def _claimed(self, record: ManagedInstance) -> ManagedInstance:
        """One container change at a time: a client asks again once the running one finishes."""
        active = self.registry.active_operation(record.instance_id)
        if active is not None:
            raise LifecycleOperationInFlight(
                f'Lifecycle operation "{active}" is still running for this instance.'
            )
        return record

    async def _recreate(self, operation_id: UUID, instance_id: UUID) -> None:
        try:
            async with self._locks.setdefault(instance_id, asyncio.Lock()):
                record = self._active_instance(instance_id)
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
        self._revalidate(operation_id, record)
        if not record.image_id:
            raise ValueError("This instance has no recorded image to recreate its container from.")
        await self._remove_container(record, PendingStop(operation_id, asyncio.Event()))
        await self._create_container(operation_id, record, record.image_id)
        if record.intended_state is IntendedState.RUNNING:
            self._record(operation_id, "start", "Restoring the intended running state.")
            await self._runtime.start(record.runtime_id)

    def _revalidate(self, operation_id: UUID, record: ManagedInstance) -> Instance:
        """Revalidate what a container is built on. No revision is resolved here."""
        self._record(
            operation_id,
            "validate",
            f"Revalidating the retained configuration of instance {record.instance_id}.",
        )
        instance = inspect_instance(record.path)
        conflict = self.registry.conflicting_storage(record.instance_id, record.storage)
        if conflict is not None:
            raise ValueError(
                f'Storage source "{conflict.item.source}" is owned by instance {conflict.owner}, '
                "so nothing was changed."
            )
        return instance

    async def _remove_container(self, record: ManagedInstance, pending: PendingStop) -> None:
        """Drain and remove the container that is there, keeping every storage it owns."""
        if (await self._runtime.status(record.runtime_id)).state == "absent":
            return
        await self._stop_running(record, pending)
        self._record(pending.operation_id, "remove", "Removing the container, keeping its storage.")
        await self._runtime.remove(record.runtime_id)

    async def _create_container(
        self,
        operation_id: UUID,
        record: ManagedInstance,
        image: str,
    ) -> None:
        self._record(operation_id, "create", f"Creating the container from image {image}.")
        await self._runtime.create(
            InstanceSpec(
                instance_id=record.runtime_id,
                image=image,
                storage=record.storage,
                env=self._environment(record.path),
                port=_INSTANCE_PORT,
            )
        )

    async def update(self, command: InstanceUpdateCommand) -> LifecycleOperationResult:
        """Move this instance onto the image a selected revision prepares."""
        record = self._claimed(self._active_instance(command.instance_id))
        selection = ImageSelection(command.revision, _pinned_package(record, command.package))
        operation_id = uuid4()
        self.registry.record_operation(
            operation_id,
            record.instance_id,
            OperationKind.UPDATE,
            "Update queued.",
        )
        self._schedule(self._update(operation_id, record.instance_id, selection))
        return LifecycleOperationResult(
            operation_id=operation_id,
            instance_id=record.instance_id,
        )

    async def _update(
        self,
        operation_id: UUID,
        instance_id: UUID,
        selection: ImageSelection,
    ) -> None:
        try:
            # The lock serializes the replacement against every other lifecycle mutation.
            async with self._locks.setdefault(instance_id, asyncio.Lock()):
                record = self._active_instance(instance_id)
                try:
                    detail = await self._replace_image(operation_id, record, selection)
                except Exception as exc:
                    self._fail(operation_id, record, str(exc) or type(exc).__name__)
                    return
                self.registry.finish_operation(operation_id, OperationState.SUCCEEDED, detail)
        except asyncio.CancelledError:
            self._fail_if_unfinished(operation_id)
            raise

    async def _replace_image(
        self,
        operation_id: UUID,
        record: ManagedInstance,
        selection: ImageSelection,
    ) -> str:
        """Prepare the candidate before anything moves, then replace the container with it.

        A failure before the replacement leaves the instance running on its selected
        image. A failure after it stays a failure: the new image may already have
        changed this instance's data, so nothing here starts an older one against it.
        """
        revision = selection.revision
        self._revalidate(operation_id, record)
        self._record(operation_id, "image", f"Preparing the image {revision} selects.")
        # The candidate validates this instance's own package.yaml before anything moves.
        prepared = await self._images.prepare(selection, self._candidate_config(record))
        artifact = prepared.artifact
        # The candidate must carry this instance's package selection. The configuration
        # that package once copied is the instance's own and is never seeded again,
        # so nothing of the instance directory is rewritten here. See ADR 0039.
        self._package(prepared, selection, self._environment(record.path))
        self._record(
            operation_id,
            "replace",
            f"Replacing image {record.image_id} with {artifact.image_id}. "
            "The previous image stays recorded for an explicit recovery.",
        )
        self.registry.stage_candidate(record.instance_id, revision, artifact)
        await self._remove_container(record, PendingStop(operation_id, asyncio.Event()))
        await self._create_container(operation_id, record, artifact.image_id)
        self.registry.record_selection(record.instance_id, revision, artifact)
        running = record.intended_state is IntendedState.RUNNING
        if running:
            self._record(operation_id, "start", "Starting the replacement.")
            await self._runtime.start(record.runtime_id)
            self._record(
                operation_id, "ready", "Waiting for the replacement to report itself ready."
            )
            await self._observe_ready(record)
        # A failed update keeps the previous pin: the package moves once the replacement is up.
        self.registry.record_package(record.instance_id, selection.package)
        return "Instance updated." if running else "Instance updated and left stopped."

    async def remove(self, command: InstanceRemoveCommand) -> LifecycleOperationResult:
        """Drain and remove the container. The data and the record stay for a restoration."""
        record = self._claimed(self._active_instance(command.instance_id))
        operation_id = uuid4()
        self.registry.record_operation(
            operation_id,
            record.instance_id,
            OperationKind.REMOVE,
            "Removal queued.",
        )
        # A force stop escalates this drain the way it escalates a stop's.
        pending = PendingStop(operation_id, asyncio.Event())
        self._stopping[record.instance_id] = pending
        self._schedule(self._remove(record.instance_id, pending))
        return LifecycleOperationResult(
            operation_id=operation_id,
            instance_id=record.instance_id,
        )

    async def _remove(self, instance_id: UUID, pending: PendingStop) -> None:
        operation_id = pending.operation_id
        try:
            try:
                async with self._locks.setdefault(instance_id, asyncio.Lock()):
                    record = self._active_instance(instance_id)
                    try:
                        await self._take_away(record, pending)
                    except Exception as exc:
                        self._fail(operation_id, record, str(exc) or type(exc).__name__)
                        return
                    self.registry.finish_operation(
                        operation_id,
                        OperationState.SUCCEEDED,
                        "Instance removed. Its data and record are retained.",
                    )
            except asyncio.CancelledError:
                self._fail_if_unfinished(operation_id)
                raise
        finally:
            self._stopping.pop(instance_id, None)

    async def _take_away(self, record: ManagedInstance, pending: PendingStop) -> None:
        """Remove only a container this hub labeled, and record the removal once it is gone.

        The record says removed only after the container is, so an interruption before
        that leaves an instance the user can still see and remove again.
        """
        described = await self._runtime.describe(record.runtime_id)
        if described is not None and described.owner is not ContainerOwner.HUB:
            raise ValueError(
                f"{previous_manager(described)} The hub removes only a container it labeled, "
                "so the instance was left as it is."
            )
        self.registry.set_intended_state(record.instance_id, IntendedState.STOPPED)
        await self._remove_container(record, pending)
        self.registry.set_intended_state(record.instance_id, IntendedState.REMOVED)

    async def restore(self, command: InstanceRestoreCommand) -> LifecycleOperationResult:
        """Bring a removed instance back from its retained record, and leave it stopped."""
        record = self._claimed(self._removed_instance(command.instance_id))
        operation_id = uuid4()
        self.registry.record_operation(
            operation_id,
            record.instance_id,
            OperationKind.RESTORE,
            "Restoration queued.",
        )
        self._schedule(self._restore(operation_id, record.instance_id))
        return LifecycleOperationResult(
            operation_id=operation_id,
            instance_id=record.instance_id,
        )

    async def _restore(self, operation_id: UUID, instance_id: UUID) -> None:
        try:
            async with self._locks.setdefault(instance_id, asyncio.Lock()):
                record = self._removed_instance(instance_id)
                try:
                    await self._bring_back(operation_id, record)
                except Exception as exc:
                    self._fail(operation_id, record, str(exc) or type(exc).__name__)
                    return
                self.registry.finish_operation(
                    operation_id,
                    OperationState.SUCCEEDED,
                    "Instance restored and left stopped. Start it to run it.",
                )
        except asyncio.CancelledError:
            self._fail_if_unfinished(operation_id)
            raise

    async def _bring_back(self, operation_id: UUID, record: ManagedInstance) -> None:
        """Check that everything the record retains is still there, then create the container.

        Nothing missing is replaced. Docker would mount a new, empty volume under a
        retained volume's name, and another image would be an update.
        """
        manifest_id = self._revalidate(operation_id, record).manifest.id
        if manifest_id != record.manifest_id:
            raise ValueError(
                f'{record.path} now holds instance "{manifest_id}", not "{record.manifest_id}".'
            )
        image = record.image_id
        if not image or not await self._runtime.has_image(image):
            raise ValueError(
                f"The selected image {image} is no longer available, and restoration selects "
                "no other."
            )
        for item in record.storage:
            if item.kind is StorageKind.VOLUME and not await self._runtime.has_volume(item.source):
                raise ValueError(
                    f'Named volume "{item.source}" is missing, and restoration does not replace '
                    "it with an empty one."
                )
        if await self._runtime.describe(record.runtime_id) is not None:
            raise ValueError(
                f'A container "{record.runtime_id}" already exists, so the instance was not '
                "restored over it."
            )
        await self._create_container(operation_id, record, image)
        self.registry.set_intended_state(record.instance_id, IntendedState.STOPPED)

    async def delete_preview(
        self,
        command: InstanceDeletePreviewCommand,
    ) -> InstanceDeletePreviewResult:
        """Name exactly what a permanent deletion would take. Nothing is touched here."""
        return self._deletion_targets(self._removed_instance(command.instance_id))

    async def delete(self, command: InstanceDeleteCommand) -> LifecycleOperationResult:
        """Permanently delete the previewed directories and named volumes of a removed instance."""
        record = self._claimed(self._removed_instance(command.instance_id))
        operation_id = uuid4()
        self.registry.record_operation(
            operation_id,
            record.instance_id,
            OperationKind.DELETE,
            "Deletion queued.",
        )
        self._schedule(self._delete(operation_id, command))
        return LifecycleOperationResult(
            operation_id=operation_id,
            instance_id=record.instance_id,
        )

    async def _delete(self, operation_id: UUID, command: InstanceDeleteCommand) -> None:
        try:
            # The lock serializes the deletion against restoration and every other mutation.
            async with self._locks.setdefault(command.instance_id, asyncio.Lock()):
                try:
                    record = self._removed_instance(command.instance_id)
                except ManagedInstanceNotFound as exc:
                    self.registry.finish_operation(operation_id, OperationState.FAILED, str(exc))
                    return
                try:
                    await self._erase(operation_id, record, command)
                except Exception as exc:
                    self._fail(operation_id, record, str(exc) or type(exc).__name__)
                    return
                self.registry.finish_operation(
                    operation_id,
                    OperationState.SUCCEEDED,
                    "Instance deleted. Its directories and named volumes are gone.",
                )
        except asyncio.CancelledError:
            self._fail_if_unfinished(operation_id)
            raise

    async def _erase(
        self,
        operation_id: UUID,
        record: ManagedInstance,
        command: InstanceDeleteCommand,
    ) -> None:
        """Revalidate the preview, then delete each target and release it from the inventory.

        A target leaves the inventory only once it is gone. A retry therefore picks up
        a target that failed, and never reaches for one that was already deleted, even
        when something unrelated appears in its place later.
        """
        self._record(
            operation_id,
            "validate",
            "Revalidating the retained inventory against the preview.",
        )
        targets = self._deletion_targets(record)
        if (targets.directories, targets.volumes) != (command.directories, command.volumes):
            raise ValueError(
                "The deletion targets changed since the preview, so nothing was deleted. "
                "Preview the deletion again."
            )
        shared = self.registry.shared_storage(record.instance_id, record.storage)
        if shared is not None:
            raise ValueError(
                f'Storage "{shared.item.source}" is shared with instance {shared.owner}, '
                "so nothing was deleted."
            )
        if await self._runtime.describe(record.runtime_id) is not None:
            raise ValueError(
                f'A container "{record.runtime_id}" still exists and may mount this storage, '
                "so nothing was deleted."
            )
        owned = [item for item in record.storage if item.writable]
        for directory in targets.directories:
            entries = [
                item
                for item in owned
                if item.kind is StorageKind.BIND and self._directory(record, item) == directory
            ]
            self._record(operation_id, f"directory {directory}", f"Deleting {directory}.")
            await asyncio.to_thread(_delete_directory, directory)
            self.registry.release_storage(record.instance_id, entries)
        for volume in targets.volumes:
            entries = [
                item for item in owned if item.kind is StorageKind.VOLUME and item.source == volume
            ]
            self._record(operation_id, f"volume {volume}", f'Deleting named volume "{volume}".')
            await self._runtime.delete_volume(volume)
            self.registry.release_storage(record.instance_id, entries)
        self.registry.mark_deleted(record.instance_id)

    def _deletion_targets(self, record: ManagedInstance) -> InstanceDeletePreviewResult:
        """The writable storage the retained inventory records, and nothing it does not.

        A read-only mount was never reserved against other instances, so it is not
        this instance's to delete. Neither is anything the manifest merely references.
        """
        owned = [item for item in record.storage if item.writable]
        return InstanceDeletePreviewResult(
            instance_id=record.instance_id,
            directories=list(
                dict.fromkeys(
                    self._directory(record, item) for item in owned if item.kind is StorageKind.BIND
                )
            ),
            volumes=list(
                dict.fromkeys(item.source for item in owned if item.kind is StorageKind.VOLUME)
            ),
        )

    def _candidate_config(self, record: ManagedInstance) -> StorageItem | None:
        """The instance file the candidate check may see, on the Docker host.

        The check reads package.yaml and nothing else. When the file exists, the
        mount source is that file's host path, so the hub does not hand the
        candidate the directory that holds .env. A missing file stays a directory
        mount with nothing attached, and the check reports the absence.
        """
        mount = next(
            (
                item
                for item in record.storage
                if item.kind is StorageKind.BIND and item.destination == INSTANCE_MOUNT
            ),
            None,
        )
        if mount is None or not (record.path / PACKAGE_CONFIG_NAME).is_file():
            return mount
        return StorageItem(
            kind=StorageKind.BIND,
            source=str(Path(mount.source) / PACKAGE_CONFIG_NAME),
            destination=f"{mount.destination.rstrip('/')}/{PACKAGE_CONFIG_NAME}",
            writable=False,
        )

    def _directory(self, record: ManagedInstance, item: StorageItem) -> Path:
        """Where this hub sees a bind source.

        A bind source names a path on the Docker host. The instance directory is
        where the record keeps it as the hub sees it, wherever the host mounts it from.
        """
        if item.destination == INSTANCE_MOUNT:
            return record.path.resolve()
        return Path(item.source).resolve()

    async def _observe_ready(self, record: ManagedInstance) -> None:
        """A container that runs is not an instance that booted.

        The replacement answers its own lifecycle endpoint before the update reports
        success, so an image that fails against the data it now owns fails the update.
        """
        try:
            async with asyncio.timeout(READY_OBSERVE_SECONDS):
                while not await self._ready(record):
                    await asyncio.sleep(_POLL_SECONDS)
        except TimeoutError as exc:
            raise ReplacementNotReady(
                "The replacement did not answer its lifecycle endpoint within "
                f"{READY_OBSERVE_SECONDS} seconds."
            ) from exc

    async def _ready(self, record: ManagedInstance) -> bool:
        """An instance still booting has no endpoint yet, and that is not a failure."""
        status = await self._runtime.status(record.runtime_id)
        if status.state in _RUNTIME_STOPPED:
            raise ReplacementNotReady(
                "The replacement container stopped before the instance answered: "
                f"{status.detail or status.state}."
            )
        if status.healthy is False and status.state == "running":
            raise ReplacementNotReady("The replacement container reports itself unhealthy.")
        if status.state != "running":
            return False
        try:
            await self._control.probe(await self._endpoint(record))
        except ControlUnreachable:
            return False
        return True

    async def _stop(self, instance_id: UUID, pending: PendingStop) -> None:
        operation_id = pending.operation_id
        try:
            try:
                # The lock serializes stops against other mutations; escalation never takes it.
                async with self._locks.setdefault(instance_id, asyncio.Lock()):
                    record = self._active_instance(instance_id)
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
        probed = await self._control.probe(await self._endpoint(record))
        if Capability.DRAIN not in probed.capabilities:
            raise IncompatibleLifecycleEndpoint(
                "The instance's lifecycle endpoint cannot drain, so it was left running. "
                "Update the instance before stopping it."
            )
        await self._take_down(record, pending)
        return "Instance stopped."

    async def _take_down(self, record: ManagedInstance, pending: PendingStop) -> None:
        """Wait for the instance's own drain, then take its container down and watch it go."""
        operation_id = pending.operation_id
        state = await self._drain(operation_id, await self._endpoint(record), pending)
        self._record(
            operation_id,
            "result",
            f"Instance {state.value}. Stopping the container."
            if state is not None
            else "The instance did not report its drain. Terminating the container.",
        )
        await self._halt_container(record.runtime_id)
        self._record(operation_id, "container", "Stopping the container.")

    async def _halt_container(self, runtime_id: str) -> None:
        """A recorded interruption is not evidence that the process exited. Watch it stop."""
        await self._runtime.stop(runtime_id, grace_seconds=STOP_GRACE_SECONDS)
        await self._observe_stop(runtime_id)

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
                    await asyncio.sleep(_POLL_SECONDS)
        except TimeoutError as exc:
            raise TimeoutError(
                f"The container was still running {_STOP_OBSERVE_SECONDS} seconds "
                "after it was told to stop."
            ) from exc

    async def _probe_endpoint(self, record: ManagedInstance) -> ControlEndpoint:
        """Where the instance answers, with whatever token it holds: a health probe needs none."""
        address = await self._runtime.address(record.runtime_id)
        if address is None:
            raise ControlUnreachable("The instance's lifecycle endpoint cannot be reached.")
        token = self._environment(record.path).get(CONTROL_TOKEN_VARIABLE, "")
        return ControlEndpoint(address=address, token=ControlToken(token))

    async def _endpoint(self, record: ManagedInstance) -> ControlEndpoint:
        """The same endpoint, for the calls a token opens."""
        endpoint = await self._probe_endpoint(record)
        if not endpoint.token:
            raise ControlUnreachable("The instance's lifecycle endpoint cannot be reached.")
        return endpoint

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
        return InstanceListResult(instances=self.registry.list_instances(removed=command.removed))

    async def status(self, command: InstanceStatusCommand) -> InstanceStatusResult:
        record = self._active_instance(command.instance_id)
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
        record = self._active_instance(command.instance_id)
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
        if record is None or not record.active:
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

    def _active_instance(self, instance_id: UUID) -> ManagedInstance:
        record = self.registry.instance(instance_id)
        if record is None or not record.active:
            raise ManagedInstanceNotFound(f'Instance "{instance_id}" was not found.')
        return record

    def _removed_instance(self, instance_id: UUID) -> ManagedInstance:
        record = self.registry.instance(instance_id)
        if record is None or record.intended_state is not IntendedState.REMOVED:
            raise ManagedInstanceNotFound(f'Removed instance "{instance_id}" was not found.')
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
        match selected.version:
            case PackageCommit(url=url, sha=sha):
                # A commit names no version before it is built; its own metadata reports one.
                version, source = descriptor.version, f"git+{url}@{sha}"
            case index_version:
                version, source = index_version, index_version
        expected = (selected.id, selected.distribution, version)
        actual = (descriptor.id, descriptor.distribution, descriptor.version)
        if actual != expected:
            raise ValueError(
                f"Prepared package was {descriptor.id} from "
                f"{descriptor.distribution} {descriptor.version}; expected "
                f"{selected.id} from {selected.distribution} {source}."
            )
        missing = [
            secret.name for secret in descriptor.required_secrets if secret.name not in secrets
        ]
        if missing:
            raise ValueError(f'Missing required secret: "{missing[0]}".')
        return installed

    def _storage(self, instance_id: UUID, path: Path) -> tuple[StorageItem, ...]:
        """What a created instance owns: its directory on the Docker host, and two volumes."""
        relative = path.relative_to(self.directory)
        host_path = self._docker_host_directory / relative
        volume = f"kinby-{instance_id}"
        return (
            StorageItem(
                kind=StorageKind.BIND,
                source=str(host_path),
                destination="/instance",
                writable=True,
            ),
            StorageItem(
                kind=StorageKind.VOLUME,
                source=f"{volume}-workspace",
                destination="/instance/workspace",
                writable=True,
            ),
            StorageItem(
                kind=StorageKind.VOLUME,
                source=f"{volume}-codex",
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
