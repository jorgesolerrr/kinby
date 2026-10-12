import asyncio
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from kinby.contracts import DeclaredSetup
from kinby.factories import RECIPES_DIRECTORY
from kinby.hub import BuildResult, HubRegistry, ImagePreparer, ImageSelection
from kinby.hub.images import GitFailed, RevisionNotFound
from kinby.setup import vanilla_description


class FakeImageBackend:
    def __init__(self) -> None:
        self.builds: list[set[str]] = []
        self.images: set[str] = set()
        self.dockerfiles: list[str] = []
        self.described: list[str] = []
        self.removed: list[str] = []
        #: Images Docker refuses to remove, as it does one a container still uses.
        self.refused: set[str] = set()
        #: Inspection containers a hub that stopped mid-inspection left behind.
        self.inspection_containers = 0

    async def resolve_base_images(self, dockerfile: Path) -> tuple[str, ...]:
        return ("python:3.14@sha256:resolved-base",)

    async def build(self, context: Path, base_images: tuple[str, ...]) -> BuildResult:
        files = {
            path.relative_to(context).as_posix() for path in context.rglob("*") if path.is_file()
        }
        self.builds.append(files)
        self.dockerfiles.append((context / "Dockerfile").read_text(encoding="utf-8"))
        image_id = f"sha256:image-{len(self.builds)}"
        self.images.add(image_id)
        return BuildResult(image_id=image_id, dependencies=("pydantic==2.0",))

    async def exists(self, image_id: str) -> bool:
        return image_id in self.images

    async def describe(self, image_id: str) -> DeclaredSetup:
        self.described.append(image_id)
        return vanilla_description()

    async def remove(self, image_id: str) -> bool:
        if image_id in self.refused:
            return False
        self.removed.append(image_id)
        self.images.discard(image_id)
        return True

    async def remove_inspection_containers(self) -> None:
        self.inspection_containers = 0


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

        first = await preparer.build(ImageSelection("HEAD"))
        second = await preparer.build(ImageSelection("HEAD"))

        assert first.artifact == second.artifact
        assert first.artifact.revision == revision
        assert first.artifact.base_images == ("python:3.14@sha256:resolved-base",)
        assert first.artifact.dependencies == ("pydantic==2.0",)
        assert len(backend.builds) == 1
        assert "src/app.py" in backend.builds[0]
        assert "instances/private/.env" not in backend.builds[0]
        assert "instances/private/workspace/notes.txt" not in backend.builds[0]
        assert not any(path.endswith(".env") for path in backend.builds[0])

        backend.images.clear()
        rebuilt = await preparer.build(ImageSelection("HEAD"))
        assert rebuilt.artifact.image_id == "sha256:image-2"
        assert len(backend.builds) == 2

    asyncio.run(scenario())


def test_preparing_vanilla_again_reuses_its_image_and_describes_it_from_inside(tmp_path):
    async def scenario() -> None:
        source = tmp_path / "source"
        source.mkdir()
        _source_repo(source)
        backend = FakeImageBackend()
        preparer = ImagePreparer(source, HubRegistry(tmp_path / "hub"), backend)

        first = await preparer.build(ImageSelection("HEAD"))
        again = await preparer.build(ImageSelection("HEAD"))
        described = await preparer.describe(again.artifact)

        assert (first.reused, again.reused) == (False, True)
        assert again.artifact == first.artifact
        assert len(backend.builds) == 1
        assert backend.described == [first.artifact.image_id]
        assert described == vanilla_description()

    asyncio.run(scenario())


def test_image_preparation_reads_a_source_checkout_another_user_owns(tmp_path, monkeypatch):
    """The hub runs as root in its container, and the bind-mounted checkout is the host user's."""

    async def scenario() -> None:
        source = tmp_path / "source"
        source.mkdir()
        revision = _source_repo(source)
        monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
        preparer = ImagePreparer(source, HubRegistry(tmp_path / "hub"), FakeImageBackend())

        built = await preparer.build(ImageSelection("HEAD"))

        assert built.artifact.revision == revision

    asyncio.run(scenario())


def test_an_unknown_ref_is_named_as_missing_from_the_hub_checkout(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _source_repo(source)
    preparer = ImagePreparer(source, HubRegistry(tmp_path / "hub"), FakeImageBackend())

    with pytest.raises(RevisionNotFound) as raised:
        asyncio.run(preparer.resolve("no-such-ref-xyz"))

    assert str(raised.value) == 'No commit "no-such-ref-xyz" in the hub\'s checkout.'


def test_a_short_ref_resolves_to_its_full_commit(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    revision = _source_repo(source)
    preparer = ImagePreparer(source, HubRegistry(tmp_path / "hub"), FakeImageBackend())

    assert asyncio.run(preparer.resolve(revision[:7])) == revision


def test_other_git_failures_carry_git_s_own_message(tmp_path):
    not_a_checkout = tmp_path / "source"
    not_a_checkout.mkdir()
    preparer = ImagePreparer(not_a_checkout, HubRegistry(tmp_path / "hub"), FakeImageBackend())

    with pytest.raises(GitFailed) as raised:
        asyncio.run(preparer.resolve("HEAD"))

    assert str(raised.value).startswith("fatal: not a git repository")


def test_a_template_recipe_is_built_into_the_image(tmp_path):
    async def scenario() -> None:
        source = tmp_path / "source"
        source.mkdir()
        _source_repo(source)
        backend = FakeImageBackend()
        preparer = ImagePreparer(source, HubRegistry(tmp_path / "hub"), backend)

        first = await preparer.build(ImageSelection("HEAD", recipe="coder"))
        again = await preparer.build(ImageSelection("HEAD", recipe="coder"))
        base = await preparer.build(ImageSelection("HEAD"))

        assert first.artifact == again.artifact
        assert base.artifact.image_id != first.artifact.image_id
        assert len(backend.builds) == 2
        recipe = (RECIPES_DIRECTORY / "coder.Dockerfile").read_text(encoding="utf-8")
        assert backend.dockerfiles[0].endswith(recipe)
        assert recipe.rstrip() not in backend.dockerfiles[1]

    asyncio.run(scenario())


def test_an_instance_image_builds_the_first_stage_and_leaves_the_web_build_out(tmp_path):
    """The hub's builder would run every stage, and the context carries no web app sources."""

    async def scenario() -> None:
        source = tmp_path / "source"
        source.mkdir()
        _source_repo(source)
        (source / "Dockerfile").write_text(
            dedent(
                """\
                FROM python:3.14 AS instance
                COPY src /app/src

                FROM oven/bun:1.4.2 AS web
                RUN bun run build

                FROM instance AS hub
                COPY --from=web /web/dist /usr/local/share/kinby/web
                """
            ),
            encoding="utf-8",
        )
        _git(source, "commit", "--all", "--message", "hub stages")
        backend = FakeImageBackend()
        preparer = ImagePreparer(source, HubRegistry(tmp_path / "hub"), backend)
        await preparer.build(ImageSelection("HEAD"))

        [dockerfile] = backend.dockerfiles
        assert dockerfile.rstrip() == "FROM python:3.14 AS instance\nCOPY src /app/src"

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
            await preparer.build(ImageSelection("HEAD"))

        assert registry.image_artifacts() == []

    asyncio.run(scenario())
