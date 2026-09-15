import asyncio
import subprocess
from pathlib import Path

import pytest

from kinby.hub import BuildResult, HubRegistry, ImagePreparer


class FakeImageBackend:
    def __init__(self) -> None:
        self.builds: list[set[str]] = []
        self.images: set[str] = set()

    async def resolve_base_images(self, dockerfile: Path) -> tuple[str, ...]:
        return ("python:3.14@sha256:resolved-base",)

    async def build(self, context: Path, base_images: tuple[str, ...]) -> BuildResult:
        files = {
            path.relative_to(context).as_posix() for path in context.rglob("*") if path.is_file()
        }
        self.builds.append(files)
        image_id = f"sha256:image-{len(self.builds)}"
        self.images.add(image_id)
        return BuildResult(image_id=image_id, dependencies=("pydantic==2.0",))

    async def exists(self, image_id: str) -> bool:
        return image_id in self.images


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _source_repo(path: Path) -> str:
    (path / "src").mkdir(parents=True)
    (path / "docker").mkdir()
    (path / "instances" / "private" / "workspace").mkdir(parents=True)
    (path / "Dockerfile").write_text("FROM python:3.14\nCOPY src /app/src\n", encoding="utf-8")
    (path / "pyproject.toml").write_text("[project]\nname='example'\n", encoding="utf-8")
    (path / "uv.lock").write_text("locked\n", encoding="utf-8")
    (path / "README.md").write_text("example\n", encoding="utf-8")
    (path / "LICENSE").write_text("license\n", encoding="utf-8")
    (path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (path / "docker" / "entrypoint.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (path / "instances" / "private" / ".env").write_text(
        "TOKEN=subscription-secret\n", encoding="utf-8"
    )
    (path / "instances" / "private" / "workspace" / "notes.txt").write_text(
        "private workspace\n", encoding="utf-8"
    )
    _git(path, "init")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "source")
    return _git(path, "rev-parse", "HEAD")


def test_image_preparation_resolves_revision_restricts_context_and_reuses_artifact(tmp_path):
    async def scenario() -> None:
        source = tmp_path / "source"
        source.mkdir()
        revision = _source_repo(source)
        registry = HubRegistry(tmp_path / "hub")
        backend = FakeImageBackend()
        preparer = ImagePreparer(source, registry, backend)

        first = await preparer.prepare("HEAD")
        second = await preparer.prepare("HEAD")

        assert first == second
        assert first.revision == revision
        assert first.base_images == ("python:3.14@sha256:resolved-base",)
        assert first.dependencies == ("pydantic==2.0",)
        assert len(backend.builds) == 1
        assert "src/app.py" in backend.builds[0]
        assert "instances/private/.env" not in backend.builds[0]
        assert "instances/private/workspace/notes.txt" not in backend.builds[0]
        assert not any(path.endswith(".env") for path in backend.builds[0])

        backend.images.clear()
        rebuilt = await preparer.prepare("HEAD")
        assert rebuilt.image_id == "sha256:image-2"
        assert len(backend.builds) == 2

    asyncio.run(scenario())


def test_image_preparation_records_no_artifact_when_the_build_fails(tmp_path):
    class FailingBackend(FakeImageBackend):
        async def build(self, context: Path, base_images: tuple[str, ...]) -> BuildResult:
            raise RuntimeError("controlled build failure")

    async def scenario() -> None:
        source = tmp_path / "source"
        source.mkdir()
        _source_repo(source)
        registry = HubRegistry(tmp_path / "hub")
        preparer = ImagePreparer(source, registry, FailingBackend())

        with pytest.raises(RuntimeError, match="controlled build failure"):
            await preparer.prepare("HEAD")

        assert registry.image_artifacts() == []

    asyncio.run(scenario())
