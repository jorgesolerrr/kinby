"""Domain values shared by hub lifecycle components."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from kinby.contracts import ContainerOwner, ControlToken, PackageSelection, StorageItem
from kinby.packages import InstalledPackage


@dataclass(frozen=True)
class InstanceSpec:
    """One stopped instance the container runtime should own."""

    instance_id: str
    image: str
    #: Exactly what the container mounts. The hub owns this inventory; the runtime obeys it.
    storage: tuple[StorageItem, ...] = ()
    command: Sequence[str] = ("serve",)
    env: Mapping[str, str] = field(default_factory=dict)
    port: int = 8787


@dataclass(frozen=True)
class InstanceEndpoint:
    """Where the hub reaches one running instance, and the secret it presents there."""

    url: str
    control_token: ControlToken


class InstanceUnreachable(StrEnum):
    """Why the hub cannot reach an instance: the two answers a public caller gets."""

    MISSING = "missing"
    UNAVAILABLE = "unavailable"


class InstanceRouting(Protocol):
    """How a public route finds the instance it carries traffic to."""

    async def endpoint(self, instance_id: UUID) -> InstanceEndpoint | InstanceUnreachable: ...

    async def signal_endpoint(self) -> InstanceEndpoint | InstanceUnreachable: ...


type RuntimeState = Literal["absent", "created", "starting", "running", "stopped", "failed"]


@dataclass(frozen=True)
class RuntimeStatus:
    state: RuntimeState
    healthy: bool | None
    detail: str = ""


@dataclass(frozen=True)
class ContainerDescription:
    """What one existing container carries, read without changing it.

    Adoption reads its storage here rather than deriving it from the instance's
    manifest: only the container knows which host directories and named volumes
    it actually mounts, and which of them it may write.
    """

    runtime_id: str
    image: str
    owner: ContainerOwner
    owner_name: str
    storage: tuple[StorageItem, ...]


class ContainerRuntime(Protocol):
    async def create(self, spec: InstanceSpec) -> None: ...

    async def describe(self, instance_id: str) -> ContainerDescription | None: ...

    async def start(self, instance_id: str) -> None: ...

    async def stop(self, instance_id: str, *, grace_seconds: int) -> None: ...

    async def remove(self, instance_id: str, *, delete_data: bool = False) -> None: ...

    async def status(self, instance_id: str) -> RuntimeStatus: ...

    async def address(self, instance_id: str) -> str | None: ...

    def logs(
        self,
        instance_id: str,
        *,
        tail: int | None = None,
        follow: bool = False,
    ) -> AsyncIterator[bytes]: ...

    def exec(self, instance_id: str, command: Sequence[str]) -> AsyncIterator[bytes]: ...

    async def list(self) -> Sequence[str]: ...

    async def has_image(self, image: str) -> bool: ...

    async def has_volume(self, name: str) -> bool: ...

    async def delete_volume(self, name: str) -> None: ...


class RecoveredState(StrEnum):
    """What a hub found for one managed instance when it opened its directory."""

    RUNNING = "running"
    STARTED = "started"
    STOPPED = "stopped"
    UNSTOPPED = "unstopped"
    UNHEALTHY = "unhealthy"
    MISSING = "missing"
    REMOVED = "removed"
    INCOMPLETE = "incomplete"
    CONFLICTED = "conflicted"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True)
class RecoveredInstance:
    instance_id: UUID
    state: RecoveredState
    detail: str


@dataclass(frozen=True)
class LifecycleRecovery:
    """One pass of lifecycle recovery: what each instance came back as, and what is not ours."""

    instances: tuple[RecoveredInstance, ...]
    unknown_containers: tuple[str, ...]


@dataclass(frozen=True)
class ImageArtifact:
    image_id: str
    revision: str
    dependency_id: str
    base_images: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    package: PackageSelection | None = None


@dataclass(frozen=True)
class ImageSelection:
    revision: str
    package: PackageSelection | None = None


@dataclass(frozen=True)
class PreparedImage:
    artifact: ImageArtifact
    package: InstalledPackage | None = None


class ImagePreparation(Protocol):
    async def prepare(
        self,
        selection: ImageSelection,
        instance: StorageItem | None = None,
    ) -> PreparedImage:
        """Prepare the image, then run the candidate check inside it.

        *instance* is an existing instance's directory, or that directory's
        package.yaml. The check validates the file and mounts nothing else.
        """
        ...


@dataclass(frozen=True)
class BuildResult:
    image_id: str
    dependencies: tuple[str, ...] = ()


class ImageBackend(Protocol):
    async def resolve_base_images(self, dockerfile: Path) -> tuple[str, ...]: ...

    async def build(self, context: Path, base_images: tuple[str, ...]) -> BuildResult: ...

    async def exists(self, image_id: str) -> bool: ...

    async def inspect_package(
        self,
        image_id: str,
        package_id: str,
        instance: StorageItem | None = None,
    ) -> InstalledPackage: ...
