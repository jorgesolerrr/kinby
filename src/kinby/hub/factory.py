"""Compose a Docker-backed hub from deployment paths."""

from __future__ import annotations

from pathlib import Path

import docker
from kinby.hub.control import HttpInstanceControl
from kinby.hub.docker import DockerImageBackend, DockerRuntime
from kinby.hub.images import ImagePreparer
from kinby.hub.registry import HubRegistry
from kinby.hub.service import Hub


def build_docker_hub(
    directory: Path,
    source_directory: Path,
    docker_host_directory: Path,
    *,
    network: str,
) -> Hub:
    directory = Path(directory).resolve()
    registry = HubRegistry(directory)
    client = docker.from_env()
    runtime = DockerRuntime(registry.hub_id(), network=network, client=client)
    images = ImagePreparer(
        source_directory,
        registry,
        DockerImageBackend(client),
    )
    return Hub(
        directory,
        runtime=runtime,
        images=images,
        control=HttpInstanceControl(),
        docker_host_directory=docker_host_directory,
    )
