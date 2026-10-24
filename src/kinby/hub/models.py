"""Domain values shared by hub lifecycle components."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NewType, Protocol
from uuid import UUID

from kinby.contracts import (
    ContainerOwner,
    ControlToken,
    DeclaredSetup,
    LifecycleOperationResult,
    RoutineName,
    Scope,
    StorageItem,
)

if TYPE_CHECKING:
    from kinby.core.dispatcher import Dispatcher
    from kinby.hub.approvals import HomeApprovals


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
class SetupSpec:
    """One setup container: a login's command on an instance's image, with that login's volume.

    It gets none of the instance's secrets, and its command runs in place of kinby.
    """

    #: The instance it signs in, by runtime id. The container is labeled with it.
    instance_id: str
    image: str
    command: Sequence[str]
    volume: StorageItem


@dataclass(frozen=True)
class InstanceEndpoint:
    """Where the hub reaches one running instance, and the secret it presents there."""

    instance_id: UUID
    url: str
    control_token: ControlToken


class InstanceUnreachable(StrEnum):
    """Why the hub cannot reach an instance: the two answers a public caller gets."""

    MISSING = "missing"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Signal:
    """A signal the hub forwarded, as its instance's receiver got it."""

    #: The routine the signal came in on.
    routine: RoutineName
    #: Read without regard to case, as HTTP headers are.
    headers: Mapping[str, str]
    body: bytes


#: A user as a channel's service knows them, such as a Telegram user id.
ChannelUser = NewType("ChannelUser", str)
#: A message a channel sent, as its service knows it.
SentRef = NewType("SentRef", str)


@dataclass(frozen=True)
class ChannelNotification:
    """A notification as a channel sends it: the hub's header, then what the source wrote."""

    notification_id: UUID
    #: The persona name and where the notification came from.
    header: str
    title: str
    text: str
    #: The page of the web app it came from. None while the hub has no public URL.
    link: str | None
    #: What the user can reply with.
    options: tuple[str, ...]


class InstanceRouting(Protocol):
    """How a public route finds the instance it carries traffic to."""

    async def endpoint(self, instance_id: UUID) -> InstanceEndpoint | InstanceUnreachable: ...

    async def signal_endpoint(self) -> InstanceEndpoint | InstanceUnreachable: ...

    def signal_accepted(self, instance_id: UUID, signal: Signal) -> None:
        """The instance authenticated the signal and recorded it as a new delivery."""

    def hub_route(self, instance_id: UUID) -> tuple[frozenset[Scope], Dispatcher]:
        """The scopes the instance holds on its hub route, once its control token checks out,
        and what it may call there."""

    def home_approvals(self, instance_id: UUID) -> HomeApprovals | None:
        """What the hub witnesses on the instance's relay: the home instance's approvals only."""


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
    #: The digest of the secrets it was created with. None on a container from before the label.
    secrets_digest: str | None = None


def secrets_digest(env: Mapping[str, str]) -> str:
    """Tell whether a container holds these secrets, without writing any of them down."""
    return hashlib.sha256(json.dumps(env, sort_keys=True).encode("utf-8")).hexdigest()


class ContainerRuntime(Protocol):
    async def create(self, spec: InstanceSpec) -> None: ...

    async def describe(self, instance_id: str) -> ContainerDescription | None: ...

    async def start(self, instance_id: str) -> None: ...

    async def stop(self, instance_id: str, *, grace_seconds: int) -> None: ...

    async def remove(self, instance_id: str) -> None: ...

    async def status(self, instance_id: str) -> RuntimeStatus: ...

    async def address(self, instance_id: str) -> str | None: ...

    def logs(self, instance_id: str, *, tail: int | None = None) -> AsyncIterator[bytes]: ...

    async def list(self) -> Sequence[str]: ...

    async def has_image(self, image: str) -> bool: ...

    async def has_volume(self, name: str) -> bool: ...

    async def delete_volume(self, name: str) -> None: ...

    async def run_setup(self, spec: SetupSpec, output: Callable[[str], None]) -> int:
        """Run a setup container to its exit code, and hand each line it prints to *output*.

        The container is removed however the run ends: it exits, a timeout cancels the
        caller, or the caller is cancelled.
        """
        ...

    async def remove_setup_containers(self) -> None:
        """Remove setup containers this hub left when it stopped mid-login.

        A login removes its container when the run ends. A hub that dies first does not,
        and the container keeps the login volume mounted.
        """
        ...


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
    """One pass of lifecycle recovery: what each instance came back as, what is not ours, and
    the creation of a home instance when the hub had none."""

    instances: tuple[RecoveredInstance, ...]
    unknown_containers: tuple[str, ...]
    home: LifecycleOperationResult | None = None


@dataclass(frozen=True)
class ImageArtifact:
    image_id: str
    revision: str
    dependency_id: str
    base_images: tuple[str, ...]
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImageSelection:
    revision: str
    #: One of the image recipes kinby ships, by name, as a factory's instance template names it.
    recipe: str | None = None


@dataclass(frozen=True)
class BuiltImage:
    """The artifact a build returns, and whether it was the one already recorded."""

    artifact: ImageArtifact
    reused: bool


class ImagePreparation(Protocol):
    async def build(self, selection: ImageSelection) -> BuiltImage:
        """Build the selected image, or reuse the artifact already recorded for it.

        Nothing runs inside the image.
        """
        ...

    async def describe(self, artifact: ImageArtifact) -> DeclaredSetup:
        """Ask the kinby inside the image what a vanilla instance declares."""
        ...

    async def resolve(self, revision: str) -> str:
        """The commit a revision names in the checkout images are built from."""
        ...

    async def collect(self) -> None:
        """Remove every recorded image that no instance, update candidate or container uses."""
        ...


@dataclass(frozen=True)
class BuildResult:
    image_id: str
    dependencies: tuple[str, ...] = ()


class ImageBackend(Protocol):
    async def resolve_base_images(self, dockerfile: Path) -> tuple[str, ...]: ...

    async def build(self, context: Path, base_images: tuple[str, ...]) -> BuildResult: ...

    async def exists(self, image_id: str) -> bool: ...

    async def remove(self, image_id: str) -> bool:
        """Whether the image is gone. Docker refuses to remove one a container still uses."""
        ...

    async def describe(self, image_id: str) -> DeclaredSetup: ...

    async def remove_inspection_containers(self) -> None:
        """Remove the containers inspections of this hub left when it stopped mid-inspection."""
        ...
