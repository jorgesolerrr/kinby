"""Docker implementations of image preparation and the container runtime."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path
from typing import cast

from docker.errors import DockerException, ImageNotFound, NotFound
from docker.models.containers import Container
from docker.models.images import Image
from docker.models.networks import Network
from docker.types import Mount

import docker
from kinby.contracts import ContainerOwner, StorageItem, StorageKind
from kinby.hub.models import BuildResult, ContainerDescription, InstanceSpec, RuntimeStatus
from kinby.packages import InstalledPackage, installed_package_from_json

_FROM = re.compile(r"^(FROM\s+)(\S+)(.*)$", re.MULTILINE | re.IGNORECASE)
_END = object()


class DockerImageBackend:
    """Build immutable images through the blocking Docker SDK off the event loop."""

    def __init__(self, client: docker.DockerClient | None = None) -> None:
        self._client = client or docker.from_env()

    async def resolve_base_images(self, dockerfile: Path) -> tuple[str, ...]:
        body = dockerfile.read_text(encoding="utf-8")
        sources = [match.group(2) for match in _FROM.finditer(body)]
        images = await asyncio.gather(
            *(asyncio.to_thread(self._client.images.pull, source) for source in sources)
        )
        return tuple(
            self._identity(source, image) for source, image in zip(sources, images, strict=True)
        )

    @staticmethod
    def _identity(source: str, image: Image) -> str:
        attributes = image.attrs or {}
        repo_digests = attributes.get("RepoDigests") or []
        repository = source.split("@", 1)[0].rsplit(":", 1)[0]
        matching = [digest for digest in repo_digests if digest.split("@", 1)[0] == repository]
        return matching[0] if matching else image.id

    async def build(self, context: Path, base_images: tuple[str, ...]) -> BuildResult:
        dockerfile = context / "Dockerfile"
        original = dockerfile.read_text(encoding="utf-8")
        identities = iter(base_images)

        def replace(match: re.Match[str]) -> str:
            return f"{match.group(1)}{next(identities)}{match.group(3)}"

        dockerfile.write_text(_FROM.sub(replace, original), encoding="utf-8")
        image, _ = await asyncio.to_thread(
            self._client.images.build,
            path=str(context),
            rm=True,
            forcerm=True,
            pull=False,
            labels={"kinby.image-artifact": "true"},
        )
        dependencies = await self._dependencies(image)
        return BuildResult(image_id=image.id, dependencies=dependencies)

    async def _dependencies(self, image: Image) -> tuple[str, ...]:
        try:
            output = await asyncio.to_thread(
                self._client.containers.run,
                image.id,
                command=["-m", "pip", "freeze", "--all"],
                entrypoint="python",
                remove=True,
            )
        except DockerException:
            return ()
        return tuple(sorted(cast(bytes, output).decode().splitlines()))

    async def exists(self, image_id: str) -> bool:
        try:
            await asyncio.to_thread(self._client.images.get, image_id)
        except ImageNotFound:
            return False
        return True

    async def inspect_package(
        self,
        image_id: str,
        package_id: str,
    ) -> InstalledPackage:
        output = await asyncio.to_thread(
            self._client.containers.run,
            image_id,
            command=["-m", "kinby.packages", package_id],
            entrypoint="python",
            remove=True,
        )
        return installed_package_from_json(cast(bytes, output).decode())


class DockerRuntime:
    """Manage labeled instance containers without exposing Docker identities upward."""

    def __init__(
        self,
        hub_id: str,
        *,
        network: str,
        client: docker.DockerClient | None = None,
    ) -> None:
        self._hub_id = hub_id
        self._network = network
        self._client = client or docker.from_env()
        self._network_lock = asyncio.Lock()

    async def create(self, spec: InstanceSpec) -> None:
        """Mount exactly the storage inventory the hub recorded, and nothing it did not."""
        runtime_id = spec.instance_id
        await self._ensure_network()
        mounts = [
            Mount(
                target=item.destination,
                source=item.source,
                type=item.kind.value,
                read_only=not item.writable,
            )
            for item in spec.storage
        ]
        await asyncio.to_thread(
            self._client.containers.create,
            spec.image,
            list(spec.command),
            name=runtime_id,
            labels={
                "kinby.hub": self._hub_id,
                "kinby.instance": runtime_id,
                "kinby.port": str(spec.port),
            },
            environment=dict(spec.env),
            mounts=mounts,
            network=self._network,
            restart_policy={"Name": "unless-stopped"},
            healthcheck={
                "test": ["CMD", "curl", "--fail", f"http://localhost:{spec.port}/health"],
                "interval": 30_000_000_000,
                "timeout": 5_000_000_000,
                "retries": 3,
                "start_period": 10_000_000_000,
            },
            detach=True,
        )

    async def _ensure_network(self) -> None:
        """The instance network exists and has a route out.

        An older hub created this network as internal. Containers move onto a
        temporary network first, including stopped ones, and only then does the
        hub recreate this network and move them back. A failed step leaves every
        container attached to at least one of those networks. One migration
        runs at a time, and a temporary network is used only when this hub
        labeled it.
        """
        async with self._network_lock:
            migrate = await self._owned_migrate_network()
            current = await self._optional_network(self._network)
            if current is not None and _is_internal(current):
                await self._move_off_internal(current)
                current = None
                migrate = await self._owned_migrate_network()
            if current is None:
                await self._create_named(self._network)
                current = await self._required_network(self._network)
            if migrate is not None:
                await self._move_onto(current, migrate)

    @property
    def _migrate_network(self) -> str:
        return f"{self._network}_migrate"

    async def _owned_migrate_network(self) -> Network | None:
        network = await self._optional_network(self._migrate_network)
        if network is None:
            return None
        if _hub_label(network) != self._hub_id:
            raise RuntimeError(
                f"Network {self._migrate_network} already exists and is not owned by this hub."
            )
        return network

    async def _move_off_internal(self, network: Network) -> None:
        migrate = await self._owned_migrate_network()
        if migrate is None:
            await self._create_named(self._migrate_network)
            migrate = await self._required_network(self._migrate_network)
        container_ids = await self._attached(network)
        await self._connect_missing(migrate, container_ids)
        await self._disconnect_present(network, container_ids)
        await asyncio.to_thread(network.remove)

    async def _move_onto(self, target: Network, migrate: Network) -> None:
        container_ids = await self._attached(migrate)
        await self._connect_missing(target, container_ids)
        await self._disconnect_present(migrate, container_ids)
        await asyncio.to_thread(migrate.remove)

    async def _connect_missing(self, network: Network, container_ids: tuple[str, ...]) -> None:
        attached = set(await self._attached(network))
        for container_id in container_ids:
            if container_id in attached:
                continue
            await asyncio.to_thread(network.connect, container_id)
            attached.add(container_id)

    async def _disconnect_present(self, network: Network, container_ids: tuple[str, ...]) -> None:
        attached = set(await self._attached(network))
        for container_id in container_ids:
            if container_id not in attached:
                continue
            await asyncio.to_thread(network.disconnect, container_id, force=True)

    async def _attached(self, network: Network) -> tuple[str, ...]:
        await asyncio.to_thread(network.reload)
        return _attached_containers(_network_attrs(network))

    async def _optional_network(self, name: str) -> Network | None:
        try:
            return await asyncio.to_thread(self._client.networks.get, name)
        except NotFound:
            return None

    async def _required_network(self, name: str) -> Network:
        return await asyncio.to_thread(self._client.networks.get, name)

    async def _create_named(self, name: str) -> None:
        await asyncio.to_thread(
            self._client.networks.create,
            name,
            internal=False,
            labels={"kinby.hub": self._hub_id},
        )

    async def start(self, instance_id: str) -> None:
        container = await self._container(instance_id)
        await asyncio.to_thread(container.start)

    async def stop(self, instance_id: str, *, grace_seconds: int) -> None:
        """Signal the container, then terminate it if the process has not exited by then."""
        container = await self._container(instance_id)
        await asyncio.to_thread(container.stop, timeout=grace_seconds)

    async def remove(self, instance_id: str, *, delete_data: bool = False) -> None:
        """Remove the container. Its named volumes go only when the caller asks for the data."""
        described = await self.describe(instance_id)
        container = await self._container(instance_id)
        await asyncio.to_thread(container.remove, v=True)
        if not delete_data or described is None:
            return
        for item in described.storage:
            if item.kind is not StorageKind.VOLUME:
                continue
            try:
                volume = await asyncio.to_thread(self._client.volumes.get, item.source)
            except NotFound:
                continue
            await asyncio.to_thread(volume.remove)

    async def describe(self, instance_id: str) -> ContainerDescription | None:
        """Read one existing container: its image, the storage it mounts, and who manages it."""
        try:
            container = await self._container(instance_id)
        except NotFound:
            return None
        await asyncio.to_thread(container.reload)
        attributes = container.attrs or {}
        labels = container.labels or {}
        owner, owner_name = self._owner(labels)
        return ContainerDescription(
            runtime_id=instance_id,
            image=str(attributes.get("Image", "")),
            owner=owner,
            owner_name=owner_name,
            storage=_mounted(attributes),
        )

    def _owner(self, labels: dict[str, str]) -> tuple[ContainerOwner, str]:
        """Who manages this container, by the label its manager left on it."""
        hub = labels.get("kinby.hub")
        if hub == self._hub_id:
            return ContainerOwner.HUB, hub
        if hub is not None:
            return ContainerOwner.OTHER_HUB, hub
        compose = labels.get("com.docker.compose.project")
        if compose is not None:
            return ContainerOwner.COMPOSE, compose
        return ContainerOwner.UNMANAGED, ""

    async def status(self, instance_id: str) -> RuntimeStatus:
        try:
            container = await self._container(instance_id)
        except NotFound:
            return RuntimeStatus("absent", None)
        await asyncio.to_thread(container.reload)
        attributes = container.attrs or {}
        state = attributes.get("State", {})
        docker_state = state.get("Status", container.status)
        health = state.get("Health", {}).get("Status")
        detail = docker_state
        if docker_state == "running":
            if health == "starting":
                return RuntimeStatus("starting", healthy=False, detail=detail)
            if health == "unhealthy":
                return RuntimeStatus("running", healthy=False, detail=detail)
            return RuntimeStatus(
                "running",
                healthy=True if health == "healthy" else None,
                detail=detail,
            )
        if docker_state == "created":
            return RuntimeStatus("created", None, detail)
        if docker_state in {"restarting", "paused"}:
            return RuntimeStatus("starting", healthy=False, detail=detail)
        if docker_state == "exited" and state.get("ExitCode") == 0:
            return RuntimeStatus("stopped", None, "exited (0)")
        if docker_state == "exited":
            return RuntimeStatus("failed", None, f"exited ({state.get('ExitCode')})")
        return RuntimeStatus("failed", None, detail)

    async def address(self, instance_id: str) -> str | None:
        """The private URL of a running instance, read from the container the hub labeled."""
        try:
            container = await self._container(instance_id)
        except NotFound:
            return None
        await asyncio.to_thread(container.reload)
        attributes = container.attrs or {}
        if attributes.get("State", {}).get("Status") != "running":
            return None
        # Containers created before this label existed listen on the spec default.
        port = container.labels.get("kinby.port", str(InstanceSpec.port))
        return f"http://{instance_id}:{port}"

    async def logs(
        self,
        instance_id: str,
        *,
        tail: int | None = None,
        follow: bool = False,
    ) -> AsyncIterator[bytes]:
        container = await self._container(instance_id)
        selected_tail: int | str = tail if tail is not None else "all"
        output = await asyncio.to_thread(
            container.logs,
            stdout=True,
            stderr=True,
            stream=follow,
            follow=follow,
            tail=selected_tail,
        )
        if not follow:
            yield cast(bytes, output)
            return
        iterator = cast(Iterator[bytes], output)
        while (chunk := await asyncio.to_thread(_next_or_end, iterator)) is not _END:
            yield cast(bytes, chunk)

    async def exec(self, instance_id: str, command: Sequence[str]) -> AsyncIterator[bytes]:
        container = await self._container(instance_id)
        result = await asyncio.to_thread(container.exec_run, list(command), stream=True)
        iterator = cast(Iterator[bytes], result.output)
        while (chunk := await asyncio.to_thread(_next_or_end, iterator)) is not _END:
            yield cast(bytes, chunk)

    async def list(self) -> Sequence[str]:
        containers = await asyncio.to_thread(
            self._client.containers.list,
            all=True,
            filters={"label": f"kinby.hub={self._hub_id}"},
        )
        return tuple(
            container.labels["kinby.instance"]
            for container in containers
            if "kinby.instance" in container.labels
        )

    async def _container(self, instance_id: str) -> Container:
        return await asyncio.to_thread(self._client.containers.get, instance_id)


def _mounted(attributes: dict[str, object]) -> tuple[StorageItem, ...]:
    """What the container actually mounts: bind sources are the Docker host's own paths."""
    mounts = attributes.get("Mounts")
    if not isinstance(mounts, list):
        return ()
    return tuple(
        StorageItem(
            kind=StorageKind.VOLUME if mount.get("Type") == "volume" else StorageKind.BIND,
            source=str(mount.get("Name") or mount.get("Source", "")),
            destination=str(mount.get("Destination", "")),
            writable=bool(mount.get("RW", False)),
        )
        for mount in mounts
        if isinstance(mount, dict) and mount.get("Type") in {"volume", "bind"}
    )


def _is_internal(network: Network | None) -> bool:
    return bool(_network_attrs(network).get("Internal"))


def _hub_label(network: Network) -> str | None:
    labels = _network_attrs(network).get("Labels")
    if not isinstance(labels, dict):
        return None
    label = labels.get("kinby.hub")
    return label if isinstance(label, str) else None


def _network_attrs(network: Network | None) -> dict[str, object]:
    if network is None:
        return {}
    raw = getattr(network, "attrs", None)
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items()}


def _attached_containers(attrs: dict[str, object]) -> tuple[str, ...]:
    containers = attrs.get("Containers")
    if not isinstance(containers, dict):
        return ()
    return tuple(str(container_id) for container_id in containers)


def _next_or_end(iterator: Iterator[bytes]) -> bytes | object:
    return next(iterator, _END)
