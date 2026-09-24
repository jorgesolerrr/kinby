"""Resolve source and prepare reusable immutable image artifacts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from kinby.contracts import StorageItem
from kinby.hub.models import (
    BuildResult,
    ImageArtifact,
    ImageBackend,
    ImageSelection,
    PreparedImage,
)
from kinby.hub.registry import HubRegistry
from kinby.packages import InstalledPackage

_ROOT_FILES = frozenset({"Dockerfile", "pyproject.toml", "uv.lock", "README.md", "LICENSE"})


def _git(repository: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout


def _included(path: str) -> bool:
    return path in _ROOT_FILES or path.startswith("src/") or path == "docker/entrypoint.sh"


class ImagePreparer:
    """Build an exact Git revision from a source-only context, or reuse its artifact."""

    def __init__(
        self,
        repository: Path,
        registry: HubRegistry,
        backend: ImageBackend,
    ) -> None:
        self._repository = Path(repository).resolve()
        self._registry = registry
        self._backend = backend

    async def prepare(
        self,
        selection: ImageSelection,
        instance: StorageItem | None = None,
    ) -> PreparedImage:
        resolved = await asyncio.to_thread(self._resolve, selection.revision)
        with TemporaryDirectory(prefix="kinby-build-") as temporary:
            context = Path(temporary)
            await asyncio.to_thread(self._export, resolved, context)
            if selection.package is not None:
                self._install_package(context / "Dockerfile", selection)
            dependency_id = self._dependency_id(context, selection)
            base_images = await self._backend.resolve_base_images(context / "Dockerfile")
            input_key = self._input_key(resolved, dependency_id, base_images, selection)
            recorded = self._registry.image_artifact(input_key)
            if recorded is not None and await self._backend.exists(recorded.image_id):
                package = await self._inspect_package(recorded, instance)
                return PreparedImage(artifact=recorded, package=package)
            built = await self._backend.build(context, base_images)
        artifact = ImageArtifact(
            image_id=built.image_id,
            revision=resolved,
            dependency_id=dependency_id,
            base_images=base_images,
            dependencies=built.dependencies,
            package=selection.package,
        )
        self._registry.record_image_artifact(input_key, artifact)
        package = await self._inspect_package(artifact, instance)
        return PreparedImage(artifact=artifact, package=package)

    async def _inspect_package(
        self,
        artifact: ImageArtifact,
        instance: StorageItem | None,
    ) -> InstalledPackage | None:
        if artifact.package is None:
            return None
        return await self._backend.inspect_package(artifact.image_id, artifact.package.id, instance)

    @staticmethod
    def _install_package(dockerfile: Path, selection: ImageSelection) -> None:
        package = selection.package
        if package is None:
            return
        body = dockerfile.read_text(encoding="utf-8").rstrip() + "\n"
        if package.image_recipe:
            body += package.image_recipe.rstrip() + "\n"
        command = [
            "uv",
            "pip",
            "install",
            "--system",
            "--no-cache",
            f"{package.distribution}=={package.version}",
        ]
        dockerfile.write_text(f"{body}RUN {json.dumps(command)}\n", encoding="utf-8")

    def _resolve(self, revision: str) -> str:
        return (
            _git(self._repository, "rev-parse", "--verify", f"{revision}^{{commit}}")
            .decode()
            .strip()
        )

    def _export(self, revision: str, context: Path) -> None:
        output = _git(self._repository, "ls-tree", "-r", "--name-only", revision)
        names = output.decode().splitlines()
        for name in names:
            if not _included(name):
                continue
            destination = context / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(_git(self._repository, "show", f"{revision}:{name}"))

    @staticmethod
    def _dependency_id(context: Path, selection: ImageSelection) -> str:
        digest = hashlib.sha256()
        for name in ("pyproject.toml", "uv.lock"):
            path = context / name
            if path.is_file():
                digest.update(name.encode())
                digest.update(path.read_bytes())
        if selection.package is not None:
            digest.update(selection.package.model_dump_json().encode())
        return f"sha256:{digest.hexdigest()}"

    @staticmethod
    def _input_key(
        revision: str,
        dependency_id: str,
        base_images: tuple[str, ...],
        selection: ImageSelection,
    ) -> str:
        encoded = json.dumps(
            [
                revision,
                dependency_id,
                base_images,
                selection.package.model_dump(mode="json") if selection.package else None,
            ],
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


__all__ = ["BuildResult", "ImagePreparer"]
