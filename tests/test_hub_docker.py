import asyncio
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import cast

import pytest

from docker import DockerClient
from kinby.hub import (
    DockerImageBackend,
    DockerRuntime,
    HubRegistry,
    ImagePreparer,
    InstanceSpec,
)


class FakeNetworks:
    def get(self, name: str) -> object:
        return object()


class FakeContainers:
    def __init__(self) -> None:
        self.arguments: tuple[object, object] | None = None
        self.options: dict[str, object] = {}
        self.thread_id: int | None = None

    def create(self, image: object, command: object, **options: object) -> object:
        self.arguments = (image, command)
        self.options = options
        self.thread_id = threading.get_ident()
        return object()


class FakeDockerClient:
    def __init__(self) -> None:
        self.containers = FakeContainers()
        self.networks = FakeNetworks()


def test_docker_runtime_translates_the_host_mount_and_offloads_creation(tmp_path):
    async def scenario() -> tuple[FakeDockerClient, int]:
        client = FakeDockerClient()
        instance_id = str(uuid.uuid4())
        (tmp_path / "inside" / "instances" / instance_id).mkdir(parents=True)
        runtime = DockerRuntime(
            "hub-id",
            tmp_path / "inside",
            Path("/srv/kinby"),
            network="kinby_private",
            client=cast(DockerClient, client),
        )
        loop_thread = threading.get_ident()
        await runtime.create(
            InstanceSpec(
                instance_id=instance_id,
                image="sha256:selected",
                env={"TOKEN": "value"},
            )
        )
        return client, loop_thread

    client, loop_thread = asyncio.run(scenario())

    assert client.containers.arguments == ("sha256:selected", ["serve"])
    assert client.containers.thread_id != loop_thread
    name = client.containers.options["name"]
    assert isinstance(name, str)
    assert client.containers.options["labels"] == {
        "kinby.hub": "hub-id",
        "kinby.instance": name.removeprefix("kinby-"),
    }
    mounts = client.containers.options["mounts"]
    assert isinstance(mounts, list)
    assert mounts[0]["Source"].startswith("/srv/kinby/instances/")
    assert mounts[0]["Target"] == "/instance"
    assert client.containers.options["network"] == "kinby_private"
    assert client.containers.options["restart_policy"] == {"Name": "unless-stopped"}


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=repository, check=True, capture_output=True)


@pytest.mark.skipif(not _docker_available(), reason="Docker daemon is not available")
def test_real_docker_artifact_has_an_immutable_identity_and_excludes_managed_data(tmp_path):
    async def scenario() -> str:
        source = tmp_path / "source"
        (source / "src").mkdir(parents=True)
        (source / "docker").mkdir()
        (source / "instances" / "private" / "workspace").mkdir(parents=True)
        (source / "Dockerfile").write_text(
            'FROM busybox:1.36\nCOPY src /snapshot/src\nCMD ["find", "/snapshot"]\n',
            encoding="utf-8",
        )
        (source / "pyproject.toml").write_text(
            "[project]\nname='artifact-test'\n", encoding="utf-8"
        )
        (source / "uv.lock").write_text("locked\n", encoding="utf-8")
        (source / "README.md").write_text("test\n", encoding="utf-8")
        (source / "LICENSE").write_text("test\n", encoding="utf-8")
        (source / "docker" / "entrypoint.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (source / "src" / "public.txt").write_text("public\n", encoding="utf-8")
        (source / "instances" / "private" / ".env").write_text(
            "TOKEN=subscription-secret\n", encoding="utf-8"
        )
        (source / "instances" / "private" / "workspace" / "private.txt").write_text(
            "private\n", encoding="utf-8"
        )
        _git(source, "init")
        _git(source, "config", "user.email", "test@example.com")
        _git(source, "config", "user.name", "Test")
        _git(source, "add", ".")
        _git(source, "commit", "-m", "source")
        artifact = await ImagePreparer(
            source,
            HubRegistry(tmp_path / "hub"),
            DockerImageBackend(),
        ).prepare("HEAD")
        assert artifact.image_id.startswith("sha256:")
        return artifact.image_id

    image_id = asyncio.run(scenario())
    try:
        result = subprocess.run(
            ["docker", "run", "--rm", image_id],
            check=True,
            capture_output=True,
            text=True,
        )
        assert "/snapshot/src/public.txt" in result.stdout
        assert "private" not in result.stdout
        assert "subscription-secret" not in result.stdout
    finally:
        subprocess.run(
            ["docker", "image", "rm", "--force", image_id],
            check=False,
            capture_output=True,
        )


@pytest.mark.skipif(not _docker_available(), reason="Docker daemon is not available")
def test_real_docker_runtime_labels_stopped_and_independent_instances(tmp_path):
    async def scenario() -> None:
        import docker

        client = docker.from_env()
        image = await asyncio.to_thread(client.images.pull, "busybox:1.36")
        network_name = f"kinby-test-{uuid.uuid4().hex}"
        await asyncio.to_thread(client.networks.create, network_name, internal=True)
        hub_id = str(uuid.uuid4())
        runtime = DockerRuntime(
            hub_id,
            tmp_path,
            tmp_path,
            network=network_name,
            client=client,
        )
        first = str(uuid.uuid4())
        second = str(uuid.uuid4())
        try:
            for instance_id in (first, second):
                (tmp_path / "instances" / instance_id).mkdir(parents=True)
                await runtime.create(
                    InstanceSpec(
                        instance_id=instance_id,
                        image=image.id,
                        command=("sh", "-c", "sleep 60"),
                    )
                )
            first_container = await asyncio.to_thread(client.containers.get, f"kinby-{first}")
            assert first_container.status == "created"
            assert first_container.labels == {
                "kinby.hub": hub_id,
                "kinby.instance": first,
            }
            await asyncio.gather(runtime.start(first), runtime.start(second))
            del runtime
            await asyncio.sleep(0.1)
            for instance_id in (first, second):
                container = await asyncio.to_thread(client.containers.get, f"kinby-{instance_id}")
                await asyncio.to_thread(container.reload)
                assert container.status == "running"
        finally:
            for instance_id in (first, second):
                try:
                    container = await asyncio.to_thread(
                        client.containers.get, f"kinby-{instance_id}"
                    )
                except Exception:
                    continue
                await asyncio.to_thread(container.remove, force=True, v=True)
                for suffix in ("workspace", "codex"):
                    try:
                        volume = await asyncio.to_thread(
                            client.volumes.get,
                            f"kinby-{instance_id}-{suffix}",
                        )
                    except Exception:
                        continue
                    await asyncio.to_thread(volume.remove, force=True)
            network = await asyncio.to_thread(client.networks.get, network_name)
            await asyncio.to_thread(network.remove)

    asyncio.run(scenario())
