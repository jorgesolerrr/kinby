"""Domain values shared by hub lifecycle components."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, NewType, Protocol

from kinby.contracts import PackageSelection
from kinby.packages import InstalledPackage

#: Where an instance's own server answers on the hub's private network.
InstanceAddress = NewType("InstanceAddress", str)


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

    def address(self, instance_id: str, port: int) -> InstanceAddress: ...

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
