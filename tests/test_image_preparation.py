import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from kinby.contracts import (
    PackageCommit,
    PackageDescription,
    PackageSelection,
    SetupFieldKind,
    StorageItem,
    StorageKind,
)
from kinby.hub import BuildResult, HubRegistry, ImagePreparer, ImageSelection
from kinby.packages import InstalledPackage, PackageDescriptor, vanilla_description


class FakeImageBackend:
    def __init__(self) -> None:
        self.builds: list[set[str]] = []
        self.images: set[str] = set()
        self.dockerfiles: list[str] = []
        self.inspected: list[StorageItem | None] = []
        self.vanilla_checks: list[str] = []

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

    async def inspect_package(
        self,
        image_id: str,
        package_id: str,
        instance: StorageItem | None = None,
    ) -> InstalledPackage:
        self.inspected.append(instance)
        return InstalledPackage(
            descriptor=PackageDescriptor(
                id=package_id,
                display_name="Writing teammate",
                description="Drafts articles.",
                icon="pen",
                distribution="kinby-writer",
                version="1.4.2",
            ),
            files={"SYSTEM.md": "Write clearly.\n"},
        )

    async def inspect_vanilla(self, image_id: str) -> PackageDescription:
        self.vanilla_checks.append(image_id)
        return vanilla_description()


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

        first = await preparer.prepare(ImageSelection("HEAD"))
        second = await preparer.prepare(ImageSelection("HEAD"))

        assert first == second
        assert first.artifact.revision == revision
        assert first.artifact.base_images == ("python:3.14@sha256:resolved-base",)
        assert first.artifact.dependencies == ("pydantic==2.0",)
        assert len(backend.builds) == 1
        assert "src/app.py" in backend.builds[0]
        assert "instances/private/.env" not in backend.builds[0]
        assert "instances/private/workspace/notes.txt" not in backend.builds[0]
        assert not any(path.endswith(".env") for path in backend.builds[0])

        backend.images.clear()
        rebuilt = await preparer.prepare(ImageSelection("HEAD"))
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
        described = await preparer.describe(again)

        assert again == first
        assert len(backend.builds) == 1
        assert backend.vanilla_checks == [first.image_id]
        assert described == vanilla_description()

    asyncio.run(scenario())


def test_describing_a_package_image_checks_it_and_puts_the_built_in_fields_first(tmp_path):
    async def scenario() -> None:
        source = tmp_path / "source"
        source.mkdir()
        _source_repo(source)
        backend = FakeImageBackend()
        preparer = ImagePreparer(source, HubRegistry(tmp_path / "hub"), backend)
        package = PackageSelection(id="writer", distribution="kinby-writer", version="1.4.2")

        artifact = await preparer.build(ImageSelection("HEAD", package))
        assert backend.inspected == []
        described = await preparer.describe(artifact)

        assert backend.inspected == [None]
        assert backend.vanilla_checks == []
        assert (described.display_name, described.version) == ("Writing teammate", "1.4.2")
        assert [(field.name, field.kind) for field in described.setup_fields] == [
            ("model", SetupFieldKind.CONFIG),
            ("api_key", SetupFieldKind.SECRET),
        ]

    asyncio.run(scenario())


def test_image_preparation_reads_a_source_checkout_another_user_owns(tmp_path, monkeypatch):
    """The hub runs as root in its container, and the bind-mounted checkout is the host user's."""

    async def scenario() -> None:
        source = tmp_path / "source"
        source.mkdir()
        revision = _source_repo(source)
        monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
        preparer = ImagePreparer(source, HubRegistry(tmp_path / "hub"), FakeImageBackend())

        prepared = await preparer.prepare(ImageSelection("HEAD"))

        assert prepared.artifact.revision == revision

    asyncio.run(scenario())


def test_pinned_package_is_installed_in_the_image_and_part_of_artifact_reuse(tmp_path):
    async def scenario() -> None:
        source = tmp_path / "source"
        source.mkdir()
        _source_repo(source)
        registry = HubRegistry(tmp_path / "hub")
        backend = FakeImageBackend()
        preparer = ImagePreparer(source, registry, backend)
        package = PackageSelection(
            id="writer",
            distribution="kinby-writer",
            version="1.4.2",
            image_recipe="RUN install-writing-client\n",
        )

        first = await preparer.prepare(ImageSelection("HEAD", package))
        second = await preparer.prepare(ImageSelection("HEAD", package))

        assert first == second
        assert first.package is not None
        assert first.package.descriptor.id == "writer"
        assert first.artifact.package == package
        assert len(backend.builds) == 1
        install = (
            'RUN ["uv", "pip", "install", "--system", "--no-cache", "--no-sources", '
            '"kinby-writer==1.4.2"]'
        )
        assert install in backend.dockerfiles[0]
        assert "RUN install-writing-client" in backend.dockerfiles[0]
        assert registry.image_artifacts()[0].package == package

        newer = package.model_copy(update={"version": "1.5.0"})
        await preparer.prepare(ImageSelection("HEAD", newer))
        assert len(backend.builds) == 2

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
        package = PackageSelection(id="writer", distribution="kinby-writer", version="1.4.2")

        await preparer.prepare(ImageSelection("HEAD", package))

        assert backend.dockerfiles == [
            "FROM python:3.14 AS instance\n"
            "COPY src /app/src\n"
            'RUN ["uv", "pip", "install", "--system", "--no-cache", "--no-sources", '
            '"kinby-writer==1.4.2"]\n'
        ]

    asyncio.run(scenario())


def test_the_candidate_check_reads_the_instance_the_preparation_is_for(tmp_path):
    async def scenario() -> None:
        source = tmp_path / "source"
        source.mkdir()
        _source_repo(source)
        backend = FakeImageBackend()
        preparer = ImagePreparer(source, HubRegistry(tmp_path / "hub"), backend)
        package = PackageSelection(id="writer", distribution="kinby-writer", version="1.4.2")
        instance = StorageItem(
            kind=StorageKind.BIND,
            source=str(tmp_path / "alice"),
            destination="/instance",
            writable=True,
        )

        await preparer.prepare(ImageSelection("HEAD", package))
        await preparer.prepare(ImageSelection("HEAD", package), instance)

        assert backend.inspected == [None, instance]

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
            await preparer.prepare(ImageSelection("HEAD"))

        assert registry.image_artifacts() == []

    asyncio.run(scenario())


#: A PEP 517 backend inside the package repository, so building it downloads nothing.
_IN_TREE_BACKEND = dedent(
    """
    import base64
    import hashlib
    import zipfile
    from pathlib import Path

    DIST = "kinby_writer-2.0.0.dist-info"
    METADATA = (
        "Metadata-Version: 2.1\\nName: kinby-writer\\nVersion: 2.0.0\\n"
        "Requires-Dist: kinby\\n"
    )
    WHEEL = "Wheel-Version: 1.0\\nGenerator: test\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n"


    def _line(name, data):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
        return f"{name},sha256={digest.decode()},{len(data)}"


    def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
        files = {
            "kinby_writer/__init__.py": Path("kinby_writer.py").read_bytes(),
            f"{DIST}/METADATA": METADATA.encode(),
            f"{DIST}/WHEEL": WHEEL.encode(),
        }
        record = [_line(name, data) for name, data in files.items()] + [f"{DIST}/RECORD,,"]
        name = "kinby_writer-2.0.0-py3-none-any.whl"
        with zipfile.ZipFile(Path(wheel_directory) / name, "w") as wheel:
            for path, data in files.items():
                wheel.writestr(path, data)
            wheel.writestr(f"{DIST}/RECORD", "\\n".join(record) + "\\n")
        return name
    """
)


def _package_repo(path: Path) -> tuple[str, str]:
    """Two commits of package distribution kinby-writer, which depends on kinby unpinned."""
    path.mkdir()
    (path / "backend.py").write_text(_IN_TREE_BACKEND, encoding="utf-8")
    (path / "pyproject.toml").write_text(
        dedent(
            """
            [project]
            name = "kinby-writer"
            version = "2.0.0"
            dependencies = ["kinby"]

            [build-system]
            requires = []
            build-backend = "backend"
            backend-path = ["."]
            """
        ),
        encoding="utf-8",
    )
    _git(path, "init")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    commits = []
    for edition in ("first", "second"):
        (path / "kinby_writer.py").write_text(f"EDITION = {edition!r}\n", encoding="utf-8")
        _git(path, "add", ".")
        _git(path, "commit", "-m", edition)
        commits.append(_git(path, "rev-parse", "HEAD"))
    return commits[0], commits[1]


class ImageEnvironment:
    """A Python environment standing in for the image, with kinby already installed."""

    KINBY = "kinby-0.1.0.dist-info"

    def __init__(self, path: Path) -> None:
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(path)], check=True)
        self.python = path / "bin" / "python"
        self.site = next(path.glob("lib/python*/site-packages"))
        (self.site / "kinby").mkdir()
        (self.site / "kinby" / "__init__.py").write_text("IMAGE = True\n", encoding="utf-8")
        dist = self.site / self.KINBY
        dist.mkdir()
        (dist / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: kinby\nVersion: 0.1.0\n", encoding="utf-8"
        )
        (dist / "RECORD").write_text(
            f"kinby/__init__.py,,\n{self.KINBY}/METADATA,,\n{self.KINBY}/RECORD,,\n",
            encoding="utf-8",
        )

    def edition(self) -> str:
        return (self.site / "kinby_writer" / "__init__.py").read_text(encoding="utf-8")

    def installed_commit(self) -> str:
        direct_url = self.site / "kinby_writer-2.0.0.dist-info" / "direct_url.json"
        return str(json.loads(direct_url.read_text(encoding="utf-8"))["vcs_info"]["commit_id"])

    def kinby(self) -> tuple[list[str], str]:
        return (
            sorted(path.name for path in self.site.glob("kinby-*.dist-info")),
            (self.site / "kinby" / "__init__.py").read_text(encoding="utf-8"),
        )


class InstallingBackend(FakeImageBackend):
    """Build by running the recipe's package install against the stand-in image."""

    def __init__(self, image: ImageEnvironment) -> None:
        super().__init__()
        self.image = image

    async def build(self, context: Path, base_images: tuple[str, ...]) -> BuildResult:
        built = await super().build(context, base_images)
        install = json.loads(self.dockerfiles[-1].splitlines()[-1].removeprefix("RUN "))
        command = [
            *install[: install.index("--system")],
            "--python",
            str(self.image.python),
            *install[install.index("--system") + 1 :],
        ]
        subprocess.run(command, check=True, capture_output=True)
        return built


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not on PATH")
def test_a_package_commit_pin_installs_that_commit_and_keeps_the_kinby_in_the_image(tmp_path):
    async def scenario() -> None:
        source = tmp_path / "source"
        source.mkdir()
        _source_repo(source)
        first, second = _package_repo(tmp_path / "writer")
        image = ImageEnvironment(tmp_path / "image")
        kinby = image.kinby()
        backend = InstallingBackend(image)
        registry = HubRegistry(tmp_path / "hub")
        preparer = ImagePreparer(source, registry, backend)
        url = (tmp_path / "writer").as_uri()

        def pinned(commit: str) -> PackageSelection:
            return PackageSelection(
                id="writer",
                distribution="kinby-writer",
                version=PackageCommit(url=url, sha=commit),
            )

        prepared = await preparer.prepare(ImageSelection("HEAD", pinned(first)))
        installed_first = (image.edition(), image.installed_commit())
        reused = await preparer.prepare(ImageSelection("HEAD", pinned(first)))
        await preparer.prepare(ImageSelection("HEAD", pinned(second)))

        install = (
            'RUN ["uv", "pip", "install", "--system", "--no-cache", "--no-sources", '
            f'"git+{url}@{first}"]'
        )
        assert install in backend.dockerfiles[0]
        assert installed_first == ("EDITION = 'first'\n", first)
        assert (image.edition(), image.installed_commit()) == ("EDITION = 'second'\n", second)
        assert image.kinby() == kinby
        assert reused == prepared
        assert len(backend.builds) == 2
        assert [artifact.package for artifact in registry.image_artifacts()] == [
            pinned(first),
            pinned(second),
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize("sha", ["abc123", "A" * 40, "g" * 40, ""])
def test_a_package_commit_names_one_full_commit_sha(sha):
    with pytest.raises(ValueError, match="sha"):
        PackageCommit(url="https://github.com/example/writer", sha=sha)
