from __future__ import annotations

import asyncio
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import cast

import pytest
from docker.errors import NotFound

from docker import DockerClient
from kinby.hub import (
    DockerImageBackend,
    DockerRuntime,
    HubRegistry,
    ImagePreparer,
    ImageSelection,
    InstanceSpec,
)
from kinby.packages import InstalledPackage, PackageDescriptor, package_json


class FakeNetworks:
    def get(self, name: str) -> object:
        if name != "kinby_private":
            raise NotFound(name)
        return object()


class FakeContainer:
    def __init__(self, status: str, labels: dict[str, str]) -> None:
        self.attrs: dict[str, object] = {"State": {"Status": status}}
        self.labels = labels

    def reload(self) -> None:
        return None


class FakeContainers:
    def __init__(self) -> None:
        self.container: FakeContainer | None = None
        self.arguments: tuple[object, object] | None = None
        self.options: dict[str, object] = {}
        self.thread_id: int | None = None
        self.run_arguments: tuple[object, object] | None = None
        self.run_options: dict[str, object] = {}

    def create(self, image: object, command: object, **options: object) -> object:
        self.arguments = (image, command)
        self.options = options
        self.thread_id = threading.get_ident()
        return object()

    def get(self, name: str) -> FakeContainer:
        if self.container is None:
            raise NotFound(name)
        return self.container

    def run(self, image: object, command: object, **options: object) -> bytes:
        self.run_arguments = (image, command)
        self.run_options = options
        return package_json(
            InstalledPackage(
                descriptor=PackageDescriptor(
                    id="writer",
                    display_name="Writing teammate",
                    description="Drafts articles.",
                    icon="pen",
                    distribution="kinby-writer",
                    version="1.4.2",
                ),
                files={"SYSTEM.md": "Write clearly.\n"},
            )
        ).encode()


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
        # The hub reads the port back off the container when it routes to the instance.
        "kinby.port": "8787",
    }
    mounts = client.containers.options["mounts"]
    assert isinstance(mounts, list)
    assert mounts[0]["Source"].startswith("/srv/kinby/instances/")
    assert mounts[0]["Target"] == "/instance"
    assert client.containers.options["network"] == "kinby_private"
    assert client.containers.options["restart_policy"] == {"Name": "unless-stopped"}


def test_docker_image_backend_inspects_the_authoritative_package_in_the_image():
    async def scenario() -> tuple[FakeDockerClient, InstalledPackage]:
        client = FakeDockerClient()
        backend = DockerImageBackend(cast(DockerClient, client))
        package = await backend.inspect_package("sha256:selected", "writer")
        return client, package

    client, package = asyncio.run(scenario())

    assert package.descriptor.distribution == "kinby-writer"
    assert package.descriptor.version == "1.4.2"
    assert package.files == {"SYSTEM.md": "Write clearly.\n"}
    assert client.containers.run_arguments == (
        "sha256:selected",
        ["-m", "kinby.packages", "writer"],
    )
    assert client.containers.run_options == {"entrypoint": "python", "remove": True}


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
        prepared = await ImagePreparer(
            source,
            HubRegistry(tmp_path / "hub"),
            DockerImageBackend(),
        ).prepare(ImageSelection("HEAD"))
        artifact = prepared.artifact
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
        await asyncio.to_thread(client.networks.create, network_name, internal=False)
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
                "kinby.port": "8787",
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


@pytest.mark.parametrize(
    ("status", "labels", "expected"),
    [
        ("running", {"kinby.port": "8787"}, "http://kinby-abc:8787"),
        ("running", {"kinby.port": "9000"}, "http://kinby-abc:9000"),
        ("exited", {"kinby.port": "8787"}, None),
        ("running", {}, "http://kinby-abc:8787"),
    ],
)
def test_the_docker_runtime_addresses_only_a_running_instance(tmp_path, status, labels, expected):
    client = FakeDockerClient()
    client.containers.container = FakeContainer(status, labels)
    runtime = DockerRuntime(
        "hub-id",
        tmp_path,
        tmp_path,
        network="kinby_private",
        client=cast(DockerClient, client),
    )

    assert asyncio.run(runtime.address("abc")) == expected


def test_the_docker_runtime_has_no_address_for_a_container_that_is_gone(tmp_path):
    runtime = DockerRuntime(
        "hub-id",
        tmp_path,
        tmp_path,
        network="kinby_private",
        client=cast(DockerClient, FakeDockerClient()),
    )

    assert asyncio.run(runtime.address("abc")) is None


class _RecordingNetwork:
    def __init__(
        self,
        name: str,
        owner: _RecordingNetworks,
        *,
        internal: bool,
        containers: tuple[str, ...] = (),
    ) -> None:
        self.name = name
        self._owner = owner
        self.containers: dict[str, object] = {container_id: {} for container_id in containers}
        self.attrs: dict[str, object] = {"Internal": internal, "Containers": self.containers}
        self.removed = False
        self.fail_connect: set[str] = set()

    def reload(self) -> None:
        return None

    def disconnect(self, container: str, force: bool = False) -> None:
        self._owner.events.append(("disconnect", self.name, container))
        self.containers.pop(container, None)
        if not self._owner.hosts(container):
            self._owner.stranded.append(container)

    def connect(self, container: str) -> None:
        if container in self.fail_connect:
            raise RuntimeError(f"could not attach {container} to {self.name}")
        self._owner.events.append(("connect", self.name, container))
        self.containers[container] = {}

    def remove(self) -> None:
        if self.containers:
            raise RuntimeError("network still has containers attached")
        self._owner.events.append(("remove", self.name))
        self.removed = True


class _RecordingNetworks:
    def __init__(self) -> None:
        self.networks: dict[str, _RecordingNetwork] = {}
        self.created: list[tuple[str, dict[str, object]]] = []
        self.events: list[tuple[str, ...]] = []
        self.stranded: list[str] = []
        self.fail_once: set[str] = set()

    def add(
        self,
        name: str,
        *,
        internal: bool,
        containers: tuple[str, ...] = (),
    ) -> _RecordingNetwork:
        network = _RecordingNetwork(name, self, internal=internal, containers=containers)
        self.networks[name] = network
        return network

    def hosts(self, container: str) -> bool:
        return any(
            container in network.containers
            for network in self.networks.values()
            if not network.removed
        )

    def get(self, name: str) -> _RecordingNetwork:
        network = self.networks.get(name)
        if network is None or network.removed:
            raise NotFound(name)
        return network

    def create(self, name: str, **options: object) -> _RecordingNetwork:
        if name in self.fail_once:
            self.fail_once.remove(name)
            raise RuntimeError("docker could not create the network")
        self.events.append(("create", name))
        self.created.append((name, options))
        network = _RecordingNetwork(name, self, internal=bool(options.get("internal")))
        self.networks[name] = network
        return network


def _runtime_on(tmp_path: Path, networks: _RecordingNetworks) -> DockerRuntime:
    class _Client:
        def __init__(self) -> None:
            self.containers = FakeContainers()
            self.networks = networks

    (tmp_path / "instances" / "abc").mkdir(parents=True)
    return DockerRuntime(
        "hub-id",
        tmp_path,
        tmp_path,
        network="kinby_private",
        client=cast(DockerClient, _Client()),
    )


def test_a_missing_network_is_created_with_a_route_out(tmp_path: Path) -> None:
    networks = _RecordingNetworks()
    runtime = _runtime_on(tmp_path, networks)

    asyncio.run(runtime.create(InstanceSpec(instance_id="abc", image="sha256:selected")))

    assert networks.created == [
        ("kinby_private", {"internal": False, "labels": {"kinby.hub": "hub-id"}})
    ]


def test_an_empty_internal_network_is_replaced_by_one_with_a_route_out(tmp_path: Path) -> None:
    networks = _RecordingNetworks()
    internal = networks.add("kinby_private", internal=True)
    runtime = _runtime_on(tmp_path, networks)

    asyncio.run(runtime.create(InstanceSpec(instance_id="abc", image="sha256:selected")))

    assert internal.removed
    assert networks.stranded == []
    assert networks.created == [
        ("kinby_private_migrate", {"internal": False, "labels": {"kinby.hub": "hub-id"}}),
        ("kinby_private", {"internal": False, "labels": {"kinby.hub": "hub-id"}}),
    ]
    assert networks.get("kinby_private").attrs["Internal"] is False


def test_stopped_containers_move_onto_the_replacement_before_the_old_network_is_removed(
    tmp_path: Path,
) -> None:
    networks = _RecordingNetworks()
    internal = networks.add("kinby_private", internal=True, containers=("stopped-instance", "hub"))
    runtime = _runtime_on(tmp_path, networks)

    asyncio.run(runtime.create(InstanceSpec(instance_id="abc", image="sha256:selected")))

    restored = networks.get("kinby_private")
    connected_before_disconnect = networks.events.index(
        ("connect", "kinby_private_migrate", "stopped-instance")
    ) < networks.events.index(("disconnect", "kinby_private", "stopped-instance"))
    assert connected_before_disconnect
    assert internal.removed
    assert networks.stranded == []
    assert set(restored.containers) == {"stopped-instance", "hub"}
    assert restored.attrs["Internal"] is False
    with pytest.raises(NotFound):
        networks.get("kinby_private_migrate")


def test_a_failed_migration_leaves_every_container_attached(tmp_path: Path) -> None:
    networks = _RecordingNetworks()
    networks.add("kinby_private", internal=True, containers=("stopped-instance", "hub"))
    networks.fail_once.add("kinby_private")
    runtime = _runtime_on(tmp_path, networks)

    with pytest.raises(RuntimeError, match="could not create"):
        asyncio.run(runtime.create(InstanceSpec(instance_id="abc", image="sha256:selected")))

    assert networks.stranded == []
    assert set(networks.get("kinby_private_migrate").containers) == {"stopped-instance", "hub"}

    asyncio.run(runtime.create(InstanceSpec(instance_id="abc", image="sha256:selected")))

    assert networks.stranded == []
    assert set(networks.get("kinby_private").containers) == {"stopped-instance", "hub"}
    assert networks.get("kinby_private").attrs["Internal"] is False
    with pytest.raises(NotFound):
        networks.get("kinby_private_migrate")
