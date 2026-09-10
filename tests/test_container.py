import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[1]


def _docker(
    *args: str,
    check: bool = True,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def _docker_is_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = _docker("info", "--format", "{{.ServerVersion}}", check=False, timeout=10)
    except OSError, subprocess.TimeoutExpired:
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


@pytest.mark.skipif(not _docker_is_available(), reason="Docker daemon is not available")
def test_image_runs_a_mounted_instance_with_the_container_contract() -> None:
    image = f"kinby-container-test-{uuid.uuid4().hex}"
    instance = PROJECT_ROOT / "examples" / "instances" / "minimal"

    try:
        _docker("build", "--quiet", "--tag", image, ".")
        inspection = _docker("image", "inspect", image)
        config = json.loads(inspection.stdout)[0]["Config"]

        assert "KINBY_INSTANCE=/instance" in config["Env"]
        assert config["Volumes"] == {"/instance": {}}
        assert config["Entrypoint"] == ["kinby-entrypoint"]
        assert config["Cmd"] == ["run"]

        result = _docker(
            "run",
            "--rm",
            "--mount",
            f"type=bind,src={instance},dst=/instance,readonly",
            image,
            "instance",
            "show",
            check=False,
        )

        assert result.returncode == 0
        assert "id: minimal" in result.stdout
        assert "path: /instance" in result.stdout
        assert "matching rule: KINBY_INSTANCE" in result.stdout
        assert result.stderr == ""
    finally:
        _docker("image", "rm", "--force", image, check=False)


@pytest.mark.skipif(not _docker_is_available(), reason="Docker daemon is not available")
@pytest.mark.parametrize(
    ("program", "prefix"),
    [("git", "git version "), ("gh", "gh version "), ("uv", "uv ")],
)
def test_image_ships_the_workspace_programs(program: str, prefix: str) -> None:
    image = f"kinby-container-test-{uuid.uuid4().hex}"

    try:
        _docker("build", "--quiet", "--tag", image, ".")

        result = _docker("run", "--rm", "--entrypoint", program, image, "--version", check=False)

        assert result.returncode == 0
        assert result.stdout.startswith(prefix)
    finally:
        _docker("image", "rm", "--force", image, check=False)


@pytest.mark.skipif(not _docker_is_available(), reason="Docker daemon is not available")
def test_entrypoint_clones_the_workspace_source_only_into_an_empty_workspace(
    tmp_path: Path,
) -> None:
    image = f"kinby-container-test-{uuid.uuid4().hex}"
    seed = tmp_path / "seed"
    seed.mkdir()
    git = ["git", "-c", "user.name=seed", "-c", "user.email=seed@example.com"]
    subprocess.run([*git, "init", "--quiet", "--initial-branch=main"], cwd=seed, check=True)
    (seed / "NOTES.md").write_text("seed\n", encoding="utf-8")
    subprocess.run([*git, "add", "NOTES.md"], cwd=seed, check=True)
    subprocess.run([*git, "commit", "--quiet", "--message", "seed"], cwd=seed, check=True)
    instance = tmp_path / "instance"
    instance.mkdir()
    (instance / "kinby.toml").write_text(
        'id = "cloned"\n[models]\nmain = "openai:gpt-5"\n[workspace]\nsource = "/seed"\n',
        encoding="utf-8",
    )
    mounts = (
        "--mount",
        f"type=bind,src={seed},dst=/seed,readonly",
        "--mount",
        f"type=bind,src={instance},dst=/instance",
    )

    try:
        _docker("build", "--quiet", "--tag", image, ".")

        first = _docker("run", "--rm", *mounts, image, "instance", "show", check=False)
        (instance / "workspace" / "NOTES.md").write_text("edited\n", encoding="utf-8")
        second = _docker("run", "--rm", *mounts, image, "instance", "show", check=False)

        assert first.returncode == 0, first.stderr
        assert "workspace: /instance/workspace (present)" in first.stdout
        assert second.returncode == 0, second.stderr
        assert (instance / "workspace" / "NOTES.md").read_text(encoding="utf-8") == "edited\n"
    finally:
        _docker("image", "rm", "--force", image, check=False)
