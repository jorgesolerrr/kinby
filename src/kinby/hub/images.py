"""Resolve source and prepare reusable immutable image artifacts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from kinby.contracts import DeclaredSetup
from kinby.factories import RECIPES_DIRECTORY
from kinby.hub.models import BuildResult, BuiltImage, ImageArtifact, ImageBackend, ImageSelection
from kinby.hub.registry import HubRegistry

_ROOT_FILES = frozenset({"Dockerfile", "pyproject.toml", "uv.lock", "README.md", "LICENSE"})
_STAGE = re.compile(r"^FROM\s", re.MULTILINE | re.IGNORECASE)


class GitFailed(RuntimeError):
    """Git refused a command. The message is git's own stderr."""

    def __init__(self, status: int, stderr: str) -> None:
        super().__init__(stderr or f"git exited with status {status}.")
        self.status = status


class RevisionNotFound(LookupError):
    def __init__(self, revision: str) -> None:
        super().__init__(f'No commit "{revision}" in the hub\'s checkout.')


def _git(repository: Path, *arguments: str) -> bytes:
    # The hub runs as root in its container and the host user owns the mounted checkout,
    # so git would refuse it as dubious. The hub only reads that checkout.
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={repository}", *arguments],
        cwd=repository,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise GitFailed(completed.returncode, completed.stderr.decode().strip())
    return completed.stdout


def _included(path: str) -> bool:
    return path in _ROOT_FILES or path.startswith("src/") or path == "docker/entrypoint.sh"


def _recipe(name: str) -> str:
    """The steps of one of the image recipes kinby ships."""
    return (RECIPES_DIRECTORY / f"{name}.Dockerfile").read_text(encoding="utf-8")


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

    async def resolve(self, revision: str) -> str:
        return await asyncio.to_thread(self._resolve, revision)

    async def build(self, selection: ImageSelection) -> BuiltImage:
        resolved = await self.resolve(selection.revision)
        with TemporaryDirectory(prefix="kinby-build-") as temporary:
            context = Path(temporary)
            await asyncio.to_thread(self._export, resolved, context)
            self._keep_instance_stage(context / "Dockerfile")
            if selection.recipe is not None:
                self._append_recipe(context / "Dockerfile", selection.recipe)
            dependency_id = self._dependency_id(context, selection)
            base_images = await self._backend.resolve_base_images(context / "Dockerfile")
            input_key = self._input_key(resolved, dependency_id, base_images)
            recorded = self._registry.image_artifact(input_key)
            if recorded is not None and await self._backend.exists(recorded.image_id):
                return BuiltImage(recorded, reused=True)
            built = await self._backend.build(context, base_images)
        artifact = ImageArtifact(
            image_id=built.image_id,
            revision=resolved,
            dependency_id=dependency_id,
            base_images=base_images,
            dependencies=built.dependencies,
        )
        self._registry.record_image_artifact(input_key, artifact)
        return BuiltImage(artifact, reused=False)

    async def describe(self, artifact: ImageArtifact) -> DeclaredSetup:
        return await self._backend.describe(artifact.image_id)

    @staticmethod
    def _keep_instance_stage(dockerfile: Path) -> None:
        """An instance image is the Dockerfile's first stage; the stages after it build the hub.

        They need web app sources this context never carries, and the classic builder the
        Docker SDK drives runs every stage up to its target, needed or not.
        """
        body = dockerfile.read_text(encoding="utf-8")
        stages = [match.start() for match in _STAGE.finditer(body)]
        if len(stages) > 1:
            dockerfile.write_text(body[: stages[1]], encoding="utf-8")

    @staticmethod
    def _append_recipe(dockerfile: Path, recipe: str) -> None:
        body = dockerfile.read_text(encoding="utf-8").rstrip() + "\n"
        dockerfile.write_text(body + _recipe(recipe).rstrip() + "\n", encoding="utf-8")

    def _resolve(self, revision: str) -> str:
        # With --quiet, git exits 1 without a word only when the revision names no commit.
        try:
            output = _git(
                self._repository, "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"
            )
        except GitFailed as error:
            if error.status == 1:
                raise RevisionNotFound(revision) from None
            raise
        return output.decode().strip()

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
        if selection.recipe is not None:
            digest.update(_recipe(selection.recipe).encode())
        return f"sha256:{digest.hexdigest()}"

    @staticmethod
    def _input_key(revision: str, dependency_id: str, base_images: tuple[str, ...]) -> str:
        encoded = json.dumps([revision, dependency_id, base_images], separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


__all__ = ["BuildResult", "ImagePreparer"]
