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
from docker.types import Mount

import docker
from kinby.hub.models import BuildResult, InstanceSpec, RuntimeStatus

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


class DockerRuntime:
    """Manage labeled instance containers without exposing Docker identities upward."""

    def __init__(
        self,
        hub_id: str,
        hub_directory: Path,
        docker_host_directory: Path,
        *,
        network: str,
        client: docker.DockerClient | None = None,
    ) -> None:
        self._hub_id = hub_id
        self._hub_directory = Path(hub_directory).resolve()
        self._docker_host_directory = Path(docker_host_directory).resolve()
        self._network = network
        self._client = client or docker.from_env()

    def _name(self, instance_id: str) -> str:
        return f"kinby-{instance_id}"

    def _instance_source(self, instance_id: str) -> str:
        local = (self._hub_directory / "instances" / instance_id).resolve()
        instances = (self._hub_directory / "instances").resolve()
        if local.parent != instances:
            raise ValueError("Instance identity does not map to one hub instance directory.")
        return str(self._docker_host_directory / "instances" / instance_id)

    async def create(self, spec: InstanceSpec) -> None:
        runtime_id = spec.instance_id
        await self._ensure_network()
        mounts = [
            Mount(
                target="/instance",
                source=self._instance_source(runtime_id),
                type="bind",
            ),
            Mount(
                target="/instance/workspace",
                source=f"kinby-{runtime_id}-workspace",
                type="volume",
            ),
            Mount(
                target="/root/.codex",
                source=f"kinby-{runtime_id}-codex",
                type="volume",
            ),
        ]
        await asyncio.to_thread(
            self._client.containers.create,
            spec.image,
            list(spec.command),
            name=self._name(runtime_id),
            labels={"kinby.hub": self._hub_id, "kinby.instance": runtime_id},
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
        try:
            await asyncio.to_thread(self._client.networks.get, self._network)
        except NotFound:
            await asyncio.to_thread(
                self._client.networks.create,
                self._network,
                internal=True,
                labels={"kinby.hub": self._hub_id},
            )

    async def start(self, instance_id: str) -> None:
        container = await self._container(instance_id)
        await asyncio.to_thread(container.start)

    async def stop(self, instance_id: str) -> None:
        container = await self._container(instance_id)
        await asyncio.to_thread(container.stop)

    async def remove(self, instance_id: str, *, delete_data: bool = False) -> None:
        container = await self._container(instance_id)
        await asyncio.to_thread(container.remove, v=True)
        if delete_data:
            for suffix in ("workspace", "codex"):
                try:
                    volume = await asyncio.to_thread(
                        self._client.volumes.get,
                        f"kinby-{instance_id}-{suffix}",
                    )
                except NotFound:
                    continue
                await asyncio.to_thread(volume.remove)

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
        return await asyncio.to_thread(self._client.containers.get, self._name(instance_id))


def _next_or_end(iterator: Iterator[bytes]) -> bytes | object:
    return next(iterator, _END)
