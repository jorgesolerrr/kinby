"""Compose a Docker-backed hub from deployment paths."""

from __future__ import annotations

from pathlib import Path

import docker
from kinby.hub.browser import BrowserLimits
from kinby.hub.control import HttpInstanceControl
from kinby.hub.docker import DockerBrowserRuntime, DockerImageBackend, DockerRuntime
from kinby.hub.images import ImagePreparer
from kinby.hub.registry import HubRegistry
from kinby.hub.service import Hub


def build_docker_hub(
    directory: Path,
    source_directory: Path,
    docker_host_directory: Path,
    *,
    network: str,
    private_url: str,
    public_url: str | None = None,
    browser_image: str | None = None,
    browser_network: str = "kinby_browser",
    browser_cap: int = BrowserLimits.cap,
) -> Hub:
    """A hub whose browser-on instances get browsers on a service it runs from *browser_image*,
    when it names one."""
    directory = Path(directory).resolve()
    registry = HubRegistry(directory)
    client = docker.from_env()
    hub_id = registry.hub_id()
    runtime = DockerRuntime(hub_id, network=network, client=client)
    images = ImagePreparer(
        source_directory,
        registry,
        DockerImageBackend(hub_id, client),
    )
    return Hub(
        directory,
        runtime=runtime,
        images=images,
        control=HttpInstanceControl(),
        docker_host_directory=docker_host_directory,
        private_url=private_url,
        public_url=public_url,
        browser=(
            DockerBrowserRuntime(
                hub_id, image=browser_image, network=browser_network, client=client
            )
            if browser_image is not None
            else None
        ),
        browser_limits=BrowserLimits(cap=browser_cap),
    )
