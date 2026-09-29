from __future__ import annotations

import asyncio
import shutil
import subprocess
import threading
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import cast

import pytest
from docker.errors import ContainerError, DockerException, ImageNotFound, NotFound
from docker.types import Mount

from docker import DockerClient
from kinby.contracts import PackageDescription, StorageItem, StorageKind
from kinby.hub import (
    BuiltImage,
    DockerImageBackend,
    DockerRuntime,
    Hub,
    HubRegistry,
    ImageArtifact,
    ImagePreparer,
    ImageSelection,
    InstanceSpec,
    PreparedImage,
    RecoveredState,
    SetupSpec,
)
from kinby.packages import InstalledPackage, PackageDescriptor, package_json, vanilla_description
from tests.test_hub import hub_client, started_instance


class FakeNetworks:
    def get(self, name: str) -> object:
        if name != "kinby_private":
            raise NotFound(name)
        return object()


class FakeContainer:
    def __init__(self, status: str, labels: dict[str, str], name: str = "") -> None:
        self.name = name
        self.status = status
        self.attrs: dict[str, object] = {"State": {"Status": status}}
        self.labels = labels
        self.stop_timeout: int | None = None
        self.removal: dict[str, bool] | None = None

    def reload(self) -> None:
        return None

    def stop(self, timeout: int | None = None) -> None:
        self.stop_timeout = timeout

    def remove(self, *, force: bool = False, v: bool = False) -> None:
        self.removal = {"force": force, "v": v}


class FakeContainers:
    def __init__(self) -> None:
        self.container: FakeContainer | None = None
        self.named: dict[str, FakeContainer] = {}
        self.arguments: tuple[object, object] | None = None
        self.options: dict[str, object] = {}
        self.thread_id: int | None = None
        self.run_arguments: tuple[object, object] | None = None
        self.run_options: dict[str, object] = {}
        self.run_failure: bytes | None = None
        #: What the check prints. The package check's output unless a test sets another.
        self.run_output: bytes | None = None
        #: What the next create returns: a container a test drives, or a bare placeholder.
        self.creates: object | None = None
        #: Containers `list` can see. A test that sets `list_error` makes `list` raise it.
        self.present: list[FakeContainer] = []
        self.list_error: DockerException | None = None

    def list(
        self, *, all: bool = False, filters: dict[str, str | Sequence[str]] | None = None
    ) -> Sequence[FakeContainer]:
        if self.list_error is not None:
            raise self.list_error
        labels: str | Sequence[str] = () if filters is None else filters.get("label", ())
        required = (labels,) if isinstance(labels, str) else tuple(labels)
        found = [container for container in self.present if _has_labels(container.labels, required)]
        if all:
            return found
        return [container for container in found if container.status == "running"]

    def create(self, image: object, command: object = None, **options: object) -> object:
        self.arguments = (image, command)
        self.options = options
        self.thread_id = threading.get_ident()
        return self.creates or object()

    def get(self, name: str) -> FakeContainer:
        found = self.named.get(name, self.container)
        if found is None:
            raise NotFound(name)
        if not found.name:
            found.name = name
        return found

    def run(self, image: object, command: object, **options: object) -> bytes:
        self.run_arguments = (image, command)
        self.run_options = options
        if self.run_failure is not None:
            raise ContainerError(object(), 1, command, image, self.run_failure)
        if self.run_output is not None:
            return self.run_output
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


class FakeDockerImages:
    def __init__(self, present: set[str]) -> None:
        self.present = present

    def get(self, name: str) -> object:
        if name not in self.present:
            raise ImageNotFound(name)
        return object()


class FakeDockerVolume:
    def __init__(self, volumes: FakeDockerVolumes, name: str) -> None:
        self._volumes = volumes
        self._name = name

    def remove(self) -> None:
        self._volumes.present.discard(self._name)
        self._volumes.removed.append(self._name)


class FakeDockerVolumes:
    def __init__(self, present: set[str]) -> None:
        self.present = present
        self.removed: list[str] = []

    def get(self, name: str) -> FakeDockerVolume:
        if name not in self.present:
            raise NotFound(name)
        return FakeDockerVolume(self, name)


class FakeDockerClient:
    def __init__(self) -> None:
        self.containers = FakeContainers()
        self.networks = FakeNetworks()
        self.images = FakeDockerImages({"sha256:selected"})
        self.volumes = FakeDockerVolumes({"kinby-alice-workspace"})


def test_docker_runtime_reports_a_missing_image_or_volume_without_creating_either():
    async def scenario() -> list[bool]:
        runtime = DockerRuntime(
            "hub-id",
            network="kinby_private",
            client=cast(DockerClient, FakeDockerClient()),
        )
        return [
            await runtime.has_image("sha256:selected"),
            await runtime.has_image("sha256:gone"),
            await runtime.has_volume("kinby-alice-workspace"),
            await runtime.has_volume("kinby-alice-codex"),
        ]

    assert asyncio.run(scenario()) == [True, False, True, False]


def test_docker_runtime_deletes_a_named_volume_and_takes_a_missing_one_as_deleted():
    client = FakeDockerClient()
    runtime = DockerRuntime("hub-id", network="kinby_private", client=cast(DockerClient, client))

    async def scenario() -> None:
        await runtime.delete_volume("kinby-alice-workspace")
        await runtime.delete_volume("kinby-alice-codex")

    asyncio.run(scenario())

    assert client.volumes.removed == ["kinby-alice-workspace"]
    assert client.volumes.present == set()


def test_docker_runtime_mounts_the_recorded_storage_and_offloads_creation(tmp_path):
    async def scenario() -> tuple[FakeDockerClient, int]:
        client = FakeDockerClient()
        instance_id = str(uuid.uuid4())
        runtime = DockerRuntime(
            "hub-id",
            network="kinby_private",
            client=cast(DockerClient, client),
        )
        loop_thread = threading.get_ident()
        await runtime.create(
            InstanceSpec(
                instance_id=instance_id,
                image="sha256:selected",
                storage=(
                    StorageItem(
                        kind=StorageKind.BIND,
                        source=f"/srv/kinby/instances/{instance_id}",
                        destination="/instance",
                        writable=True,
                    ),
                    StorageItem(
                        kind=StorageKind.VOLUME,
                        source=f"kinby-{instance_id}-workspace",
                        destination="/instance/workspace",
                        writable=True,
                    ),
                    StorageItem(
                        kind=StorageKind.BIND,
                        source="/home/jorge/.config/Anthropic",
                        destination="/anthropic-profile",
                        writable=False,
                    ),
                ),
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
        "kinby.instance": name,
        # The hub reads the port back off the container when it routes to the instance.
        "kinby.port": "8787",
    }
    mounts = client.containers.options["mounts"]
    assert isinstance(mounts, list)
    assert mounts[0]["Source"].startswith("/srv/kinby/instances/")
    assert mounts[0]["Target"] == "/instance"
    assert mounts[0]["ReadOnly"] is False
    assert mounts[1]["Type"] == "volume"
    assert mounts[2]["ReadOnly"] is True
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
    assert client.containers.run_options == {
        "entrypoint": "python",
        "remove": True,
        "mounts": [],
        "network_mode": "none",
    }


def test_the_candidate_check_mounts_package_yaml_and_not_instance_secrets(tmp_path):
    instance_dir = tmp_path / "alice"
    instance_dir.mkdir()
    (instance_dir / ".env").write_text("KINBY_CONTROL_TOKEN=secret\n", encoding="utf-8")
    (instance_dir / "package.yaml").write_text("tone: plain\n", encoding="utf-8")
    instance = StorageItem(
        kind=StorageKind.BIND,
        source=str(instance_dir),
        destination="/instance",
        writable=True,
    )

    async def scenario() -> FakeDockerClient:
        client = FakeDockerClient()
        backend = DockerImageBackend(cast(DockerClient, client))
        await backend.inspect_package("sha256:selected", "writer", instance)
        return client

    client = asyncio.run(scenario())

    assert client.containers.run_arguments == (
        "sha256:selected",
        ["-m", "kinby.packages", "writer", "/instance"],
    )
    assert client.containers.run_options["network_mode"] == "none"
    assert client.containers.run_options["mounts"] == [
        Mount(
            target="/instance/package.yaml",
            source=str(instance_dir / "package.yaml"),
            type="bind",
            read_only=True,
        )
    ]


def test_the_candidate_check_mounts_a_host_config_file_the_hub_cannot_see():
    instance = StorageItem(
        kind=StorageKind.BIND,
        source="/var/lib/kinby/instances/alice/package.yaml",
        destination="/instance/package.yaml",
        writable=False,
    )

    async def scenario() -> FakeDockerClient:
        client = FakeDockerClient()
        backend = DockerImageBackend(cast(DockerClient, client))
        await backend.inspect_package("sha256:selected", "writer", instance)
        return client

    client = asyncio.run(scenario())

    assert client.containers.run_arguments == (
        "sha256:selected",
        ["-m", "kinby.packages", "writer", "/instance"],
    )
    assert client.containers.run_options["mounts"] == [
        Mount(
            target="/instance/package.yaml",
            source="/var/lib/kinby/instances/alice/package.yaml",
            type="bind",
            read_only=True,
        )
    ]


def test_a_missing_package_yaml_is_not_created_for_the_candidate_check(tmp_path):
    instance_dir = tmp_path / "alice"
    instance_dir.mkdir()
    (instance_dir / ".env").write_text("KINBY_CONTROL_TOKEN=secret\n", encoding="utf-8")
    instance = StorageItem(
        kind=StorageKind.BIND,
        source=str(instance_dir),
        destination="/instance",
        writable=True,
    )

    async def scenario() -> FakeDockerClient:
        client = FakeDockerClient()
        backend = DockerImageBackend(cast(DockerClient, client))
        await backend.inspect_package("sha256:selected", "writer", instance)
        return client

    client = asyncio.run(scenario())

    assert client.containers.run_arguments == (
        "sha256:selected",
        ["-m", "kinby.packages", "writer", "/instance"],
    )
    assert client.containers.run_options["mounts"] == []
    assert client.containers.run_options["network_mode"] == "none"
    assert not (instance_dir / "package.yaml").exists()


def test_docker_image_backend_asks_the_kinby_in_the_image_what_vanilla_declares():
    async def scenario() -> tuple[FakeDockerClient, PackageDescription]:
        client = FakeDockerClient()
        client.containers.run_output = vanilla_description().model_dump_json().encode()
        backend = DockerImageBackend(cast(DockerClient, client))
        return client, await backend.inspect_vanilla("sha256:selected")

    client, described = asyncio.run(scenario())

    assert described == vanilla_description()
    assert client.containers.run_arguments == ("sha256:selected", ["-m", "kinby.packages"])
    assert client.containers.run_options == {
        "entrypoint": "python",
        "remove": True,
        "network_mode": "none",
    }


def test_a_failing_vanilla_check_reports_what_it_printed():
    async def scenario() -> None:
        client = FakeDockerClient()
        client.containers.run_failure = b"usage: python -m kinby.packages <package-id>\n"
        backend = DockerImageBackend(cast(DockerClient, client))
        await backend.inspect_vanilla("sha256:selected")

    with pytest.raises(ValueError) as failure:
        asyncio.run(scenario())

    assert str(failure.value) == (
        "The vanilla check failed in image sha256:selected.\n"
        "usage: python -m kinby.packages <package-id>"
    )


def test_a_failing_candidate_check_reports_what_the_check_printed():
    async def scenario() -> None:
        client = FakeDockerClient()
        client.containers.run_failure = (
            b'Executable "claude" is not on PATH.\n/instance/package.yaml: tone: Field required\n'
        )
        backend = DockerImageBackend(cast(DockerClient, client))
        await backend.inspect_package("sha256:selected", "writer")

    with pytest.raises(ValueError) as failure:
        asyncio.run(scenario())

    assert str(failure.value) == (
        'Package "writer" failed its check in image sha256:selected.\n'
        'Executable "claude" is not on PATH.\n'
        "/instance/package.yaml: tone: Field required"
    )


def docker_available() -> bool:
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


@pytest.mark.skipif(not docker_available(), reason="Docker daemon is not available")
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


@pytest.mark.skipif(not docker_available(), reason="Docker daemon is not available")
def test_real_docker_runtime_labels_stopped_and_independent_instances(tmp_path):
    async def scenario() -> None:
        import docker

        client = docker.from_env()
        image = await asyncio.to_thread(client.images.pull, "busybox:1.36")
        network_name = f"kinby-test-{uuid.uuid4().hex}"
        await asyncio.to_thread(client.networks.create, network_name, internal=False)
        hub_id = str(uuid.uuid4())
        runtime = DockerRuntime(hub_id, network=network_name, client=client)
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
            first_container = await asyncio.to_thread(client.containers.get, first)
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
                container = await asyncio.to_thread(client.containers.get, instance_id)
                await asyncio.to_thread(container.reload)
                assert container.status == "running"
        finally:
            for instance_id in (first, second):
                try:
                    container = await asyncio.to_thread(client.containers.get, instance_id)
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


class PulledImage:
    """Prepare nothing: this image is already on the daemon."""

    def __init__(self, image_id: str) -> None:
        self._image_id = image_id

    async def prepare(
        self,
        selection: ImageSelection,
        instance: StorageItem | None = None,
    ) -> PreparedImage:
        return PreparedImage(artifact=(await self.build(selection)).artifact)

    async def build(self, selection: ImageSelection) -> BuiltImage:
        artifact = ImageArtifact(
            image_id=self._image_id,
            revision="a" * 40,
            dependency_id="sha256:dependencies",
            base_images=("busybox:1.36",),
        )
        return BuiltImage(artifact, reused=False)

    async def describe(self, artifact: ImageArtifact) -> PackageDescription:
        return vanilla_description()

    async def resolve(self, revision: str) -> str:
        return "a" * 40


@pytest.mark.skipif(not docker_available(), reason="Docker daemon is not available")
def test_a_real_docker_hub_restart_keeps_one_instance_up_and_starts_the_other_again(tmp_path):
    async def scenario() -> None:
        import docker

        client = docker.from_env()
        context = tmp_path / "image"
        context.mkdir()
        (context / "Dockerfile").write_text(
            'FROM busybox:1.36\nENTRYPOINT ["sh", "-c", "sleep 300"]\n',
            encoding="utf-8",
        )
        await asyncio.to_thread(client.images.pull, "busybox:1.36")
        image, _ = await asyncio.to_thread(
            client.images.build,
            path=str(context),
            rm=True,
            forcerm=True,
            pull=False,
        )
        network_name = f"kinby-test-{uuid.uuid4().hex}"
        await asyncio.to_thread(client.networks.create, network_name, internal=False)
        directory = tmp_path / "hub"
        images = PulledImage(image.id)
        runtime_ids: list[str] = []
        try:
            hub = _docker_hub(directory, network_name, client, images)
            first = await started_instance(hub_client(hub), hub)
            runtime_ids.append(str(first.instance_id))
            second = await started_instance(hub_client(hub), hub)
            runtime_ids.append(str(second.instance_id))
            running = await asyncio.to_thread(client.containers.get, str(first.instance_id))
            stopping = await asyncio.to_thread(client.containers.get, str(second.instance_id))
            await asyncio.to_thread(stopping.stop, timeout=1)
            hub.close()

            reopened = _docker_hub(directory, network_name, client, images)
            recovery = await reopened.recover()
            reopened.close()

            assert recovery.unknown_containers == ()
            assert {instance.instance_id: instance.state for instance in recovery.instances} == {
                first.instance_id: RecoveredState.RUNNING,
                second.instance_id: RecoveredState.STARTED,
            }
            kept = await asyncio.to_thread(client.containers.get, str(first.instance_id))
            assert kept.id == running.id
            await asyncio.to_thread(kept.reload)
            assert kept.status == "running"
            started = await asyncio.to_thread(client.containers.get, str(second.instance_id))
            await asyncio.to_thread(started.reload)
            assert started.status == "running"
        finally:
            for runtime_id in runtime_ids:
                await _discard(client, runtime_id)
            network = await asyncio.to_thread(client.networks.get, network_name)
            await asyncio.to_thread(network.remove)
            await asyncio.to_thread(client.images.remove, image.id, force=True)

    asyncio.run(scenario())


def _docker_hub(
    directory: Path,
    network: str,
    client: DockerClient,
    images: PulledImage,
) -> Hub:
    registry = HubRegistry(directory)
    return Hub(
        directory,
        runtime=DockerRuntime(registry.hub_id(), network=network, client=client),
        images=images,
    )


async def _discard(client: DockerClient, runtime_id: str) -> None:
    container = None
    for name in (runtime_id, f"kinby-{runtime_id}"):
        try:
            container = await asyncio.to_thread(client.containers.get, name)
        except Exception:
            continue
        break
    if container is not None:
        await asyncio.to_thread(container.remove, force=True, v=True)
    for suffix in ("workspace", "codex"):
        try:
            volume = await asyncio.to_thread(client.volumes.get, f"kinby-{runtime_id}-{suffix}")
        except Exception:
            continue
        await asyncio.to_thread(volume.remove, force=True)


@pytest.mark.parametrize(
    ("status", "labels", "expected"),
    [
        ("running", {"kinby.port": "8787"}, "http://abc:8787"),
        ("running", {"kinby.port": "9000"}, "http://abc:9000"),
        ("exited", {"kinby.port": "8787"}, None),
        ("running", {}, "http://abc:8787"),
    ],
)
def test_the_docker_runtime_addresses_only_a_running_instance(tmp_path, status, labels, expected):
    client = FakeDockerClient()
    client.containers.container = FakeContainer(status, labels)
    runtime = DockerRuntime("hub-id", network="kinby_private", client=cast(DockerClient, client))

    assert asyncio.run(runtime.address("abc")) == expected


def test_the_docker_runtime_has_no_address_for_a_container_that_is_gone(tmp_path):
    runtime = DockerRuntime(
        "hub-id",
        network="kinby_private",
        client=cast(DockerClient, FakeDockerClient()),
    )

    assert asyncio.run(runtime.address("abc")) is None


def test_the_docker_runtime_reaches_a_container_that_still_uses_the_legacy_name():
    """Hub records store the unprefixed id; earlier releases named the container kinby-<id>."""
    client = FakeDockerClient()
    client.containers.named["kinby-abc"] = FakeContainer("running", {"kinby.port": "8787"})
    runtime = DockerRuntime(
        "hub-id",
        network="kinby_private",
        client=cast(DockerClient, client),
    )

    async def scenario() -> tuple[str, str | None]:
        status = await runtime.status("abc")
        return status.state, await runtime.address("abc")

    state, address = asyncio.run(scenario())

    assert state == "running"
    assert address == "http://kinby-abc:8787"


def test_docker_runtime_stops_within_the_grace_period(tmp_path):
    async def scenario() -> FakeDockerClient:
        client = FakeDockerClient()
        client.containers.container = FakeContainer("running", {"kinby.port": "8787"})
        runtime = DockerRuntime(
            "hub-id",
            network="kinby_private",
            client=cast(DockerClient, client),
        )
        await runtime.stop("alice", grace_seconds=45)
        return client

    client = asyncio.run(scenario())

    assert client.containers.container is not None
    assert client.containers.container.stop_timeout == 45


class FakeSetupContainer:
    """A setup container that prints its chunks, then exits, or keeps printing until removed."""

    def __init__(self, chunks: list[bytes], exit_code: int | None = 0) -> None:
        self.chunks = chunks
        self.exit_code = exit_code
        self.events: list[str] = []
        self._gone = threading.Event()

    def start(self) -> None:
        self.events.append("start")

    def logs(self, **options: object) -> Iterator[bytes]:
        assert options == {"stdout": True, "stderr": True, "stream": True, "follow": True}
        yield from self.chunks
        if self.exit_code is None:
            self._gone.wait(timeout=5)

    def wait(self) -> dict[str, object]:
        self.events.append("wait")
        return {"StatusCode": self.exit_code, "Error": None}

    def remove(self, *, force: bool = False, v: bool = False) -> None:
        self.events.append(f"remove force={force} v={v}")
        self._gone.set()


LOGIN_VOLUME = StorageItem(
    kind=StorageKind.VOLUME,
    source="kinby-alice-codex",
    destination="/root/.codex",
    writable=True,
)


def run_setup(
    container: FakeSetupContainer, *, seconds: float = 5
) -> tuple[FakeDockerClient, list[str], int | None]:
    client = FakeDockerClient()
    client.containers.creates = container
    runtime = DockerRuntime("hub-id", network="kinby_private", client=cast(DockerClient, client))
    lines: list[str] = []

    async def scenario() -> int | None:
        try:
            async with asyncio.timeout(seconds):
                return await runtime.run_setup(
                    SetupSpec(
                        instance_id="alice",
                        image="sha256:selected",
                        command=("codex", "login", "--device-auth"),
                        volume=LOGIN_VOLUME,
                    ),
                    lines.append,
                )
        except TimeoutError:
            return None

    return client, lines, asyncio.run(scenario())


def _has_labels(labels: dict[str, str], required: tuple[object, ...]) -> bool:
    for item in required:
        text = str(item)
        if "=" in text:
            key, value = text.split("=", 1)
            if labels.get(key) != value:
                return False
        elif text not in labels:
            return False
    return True


def test_a_reopened_runtime_removes_setup_containers_this_hub_left_behind():
    ours = FakeContainer("exited", {"kinby.hub": "hub-id", "kinby.setup": "alice"})
    other_hub = FakeContainer("running", {"kinby.hub": "other", "kinby.setup": "bob"})
    instance = FakeContainer("running", {"kinby.hub": "hub-id", "kinby.instance": "alice"})
    client = FakeDockerClient()
    client.containers.present = [ours, other_hub, instance]
    runtime = DockerRuntime("hub-id", network="kinby_private", client=cast(DockerClient, client))

    asyncio.run(runtime.remove_setup_containers())

    # v=True takes the anonymous /instance volume with it; Docker keeps named volumes.
    assert ours.removal == {"force": True, "v": True}
    assert other_hub.removal is None
    assert instance.removal is None
    assert client.volumes.removed == []


def test_a_setup_cleanup_the_runtime_cannot_reach_does_not_raise():
    client = FakeDockerClient()
    client.containers.list_error = DockerException("daemon down")
    runtime = DockerRuntime("hub-id", network="kinby_private", client=cast(DockerClient, client))

    asyncio.run(runtime.remove_setup_containers())


def test_a_setup_container_signs_in_on_a_scratch_home_and_copies_it_to_the_volume_on_success():
    container = FakeSetupContainer(
        [b"Open https://auth.example/device and", b" enter AB12-C3D\r\nWaiting", b"\n"]
    )

    client, lines, exit_code = run_setup(container)

    assert client.containers.arguments == ("sha256:selected", None)
    assert client.containers.options == {
        # The login command replaces the image's entrypoint, so kinby never starts.
        "entrypoint": [
            "sh",
            "-c",
            '"$@" && cp -a /root/.codex/. /kinby/login/',
            "sh",
            "codex",
            "login",
            "--device-auth",
        ],
        "labels": {"kinby.hub": "hub-id", "kinby.setup": "alice"},
        "mounts": [
            Mount(target="/root/.codex", source=None, type="tmpfs", tmpfs_mode=0o700),
            Mount(target="/kinby/login", source="kinby-alice-codex", type="volume"),
        ],
    }
    assert lines == [
        "Open https://auth.example/device and enter AB12-C3D",
        "Waiting",
    ]
    assert exit_code == 0
    # v=True takes the anonymous /instance volume with it; Docker keeps named volumes.
    assert container.events == ["start", "wait", "remove force=True v=True"]
    assert client.volumes.removed == []


@pytest.mark.skipif(not docker_available(), reason="Docker daemon is not available")
def test_a_real_sign_in_changes_the_login_volume_only_when_it_succeeds():
    import docker

    client = docker.from_env()
    image = client.images.pull("busybox", tag="1.36")
    volume = client.volumes.create(f"kinby-test-login-{uuid.uuid4().hex}")
    mount = [Mount(target="/v", source=volume.name, type="volume")]
    runtime = DockerRuntime(str(uuid.uuid4()), network="unused", client=client)

    def sign_in(exit_code: int) -> int:
        spec = SetupSpec(
            instance_id="alice",
            image=str(image.id),
            command=("sh", "-c", f"echo new > /root/.codex/auth.json; exit {exit_code}"),
            volume=StorageItem(
                kind=StorageKind.VOLUME,
                source=str(volume.name),
                destination="/root/.codex",
                writable=True,
            ),
        )
        return asyncio.run(runtime.run_setup(spec, lambda line: None))

    def volume_files() -> str:
        printed = client.containers.run(
            image.id,
            ["sh", "-c", "cat /v/auth.json /v/sessions/one"],
            mounts=mount,
            remove=True,
        )
        return cast(bytes, printed).decode()

    try:
        client.containers.run(
            image.id,
            ["sh", "-c", "echo old > /v/auth.json; mkdir /v/sessions; echo kept > /v/sessions/one"],
            mounts=mount,
            remove=True,
        )

        assert sign_in(1) == 1
        assert volume_files() == "old\nkept\n"
        assert sign_in(0) == 0
        assert volume_files() == "new\nkept\n"
    finally:
        volume.remove(force=True)


def test_a_setup_container_that_fails_reports_its_exit_code_and_goes():
    container = FakeSetupContainer([b"error: no device code"], exit_code=1)

    _, lines, exit_code = run_setup(container)

    assert lines == ["error: no device code"]
    assert exit_code == 1
    assert container.events == ["start", "wait", "remove force=True v=True"]


def test_a_setup_container_that_outlives_its_timeout_goes():
    container = FakeSetupContainer([b"Open https://auth.example/device and enter AB12-C3D\n"], None)

    _, lines, exit_code = run_setup(container, seconds=0.1)

    assert lines == ["Open https://auth.example/device and enter AB12-C3D"]
    assert exit_code is None
    assert container.events == ["start", "remove force=True v=True"]


class _RecordingNetwork:
    def __init__(
        self,
        name: str,
        owner: _RecordingNetworks,
        *,
        internal: bool,
        containers: tuple[str, ...] = (),
        labels: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self._owner = owner
        self.containers: dict[str, object] = {container_id: {} for container_id in containers}
        self.attrs: dict[str, object] = {
            "Internal": internal,
            "Containers": self.containers,
            "Labels": dict(labels or {}),
        }
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
        self.hold_create = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.max_active_creates = 0
        self._active_creates = 0
        self._create_lock = threading.Lock()

    def add(
        self,
        name: str,
        *,
        internal: bool,
        containers: tuple[str, ...] = (),
        labels: dict[str, str] | None = None,
    ) -> _RecordingNetwork:
        network = _RecordingNetwork(
            name,
            self,
            internal=internal,
            containers=containers,
            labels=labels,
        )
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
        with self._create_lock:
            self._active_creates += 1
            self.max_active_creates = max(self.max_active_creates, self._active_creates)
            hold = self.hold_create
            self.hold_create = False
        if hold:
            self.entered.set()
            assert self.release.wait(timeout=2)
        with self._create_lock:
            self._active_creates -= 1
        self.events.append(("create", name))
        self.created.append((name, options))
        raw_labels = options.get("labels")
        labels = (
            {str(key): str(value) for key, value in raw_labels.items()}
            if isinstance(raw_labels, dict)
            else {}
        )
        network = _RecordingNetwork(
            name,
            self,
            internal=bool(options.get("internal")),
            labels=labels,
        )
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


def test_a_migration_network_owned_by_someone_else_is_left_alone(tmp_path: Path) -> None:
    networks = _RecordingNetworks()
    networks.add("kinby_private", internal=False)
    foreign = networks.add(
        "kinby_private_migrate",
        internal=False,
        containers=("other-workload",),
        labels={"kinby.hub": "other-hub"},
    )
    runtime = _runtime_on(tmp_path, networks)

    with pytest.raises(RuntimeError, match="not owned"):
        asyncio.run(runtime.create(InstanceSpec(instance_id="abc", image="sha256:selected")))

    assert foreign.removed is False
    assert set(foreign.containers) == {"other-workload"}
    assert networks.stranded == []


def test_two_creates_migrate_one_network_at_a_time(tmp_path: Path) -> None:
    networks = _RecordingNetworks()
    networks.hold_create = True
    networks.add("kinby_private", internal=True, containers=("stopped-instance",))
    runtime = _runtime_on(tmp_path, networks)
    (tmp_path / "instances" / "two").mkdir()

    async def both() -> None:
        first = asyncio.create_task(
            runtime.create(InstanceSpec(instance_id="abc", image="sha256:selected"))
        )
        assert await asyncio.to_thread(networks.entered.wait, 2)
        second = asyncio.create_task(
            runtime.create(InstanceSpec(instance_id="two", image="sha256:selected"))
        )
        await asyncio.sleep(0.05)
        assert networks.max_active_creates == 1
        networks.release.set()
        await first
        await second

    asyncio.run(both())

    assert networks.max_active_creates == 1
    assert networks.stranded == []
    assert set(networks.get("kinby_private").containers) == {"stopped-instance"}
