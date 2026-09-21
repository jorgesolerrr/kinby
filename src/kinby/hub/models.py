"""Domain values shared by hub lifecycle components."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from kinby.contracts import ControlToken, PackageSelection
from kinby.packages import InstalledPackage


@dataclass(frozen=True)
class InstanceSpec:
    """One stopped instance the container runtime should own."""

    instance_id: str
    image: str
    command: Sequence[str] = ("serve",)
    env: Mapping[str, str] = field(default_factory=dict)
    port: int = 8787
    files: Mapping[str, str] = field(default_factory=dict)
    public_host: str | None = None


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


class ContainerRuntime(Protocol):
    async def create(self, spec: InstanceSpec) -> None: ...

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
    async def prepare(self, selection: ImageSelection) -> PreparedImage: ...


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
    ) -> InstalledPackage: ...
