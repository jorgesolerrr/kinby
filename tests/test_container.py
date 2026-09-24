import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).parents[1]


def _docker(
    *args: str,
    check: bool = True,
    timeout: int | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
        env=env,
    )


def _docker_is_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = _docker("info", "--format", "{{.ServerVersion}}", check=False, timeout=10)
    except OSError, subprocess.TimeoutExpired:
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _allow_temp_mount_cleanup(
    image: str,
    mounts: tuple[str, ...],
    *paths: str,
) -> None:
    _docker(
        "run",
        "--rm",
        *mounts,
        "--entrypoint",
        "chmod",
        image,
        "-R",
        "a+rwX",
        *paths,
        check=False,
    )


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
        assert config["Cmd"] == ["repl"]

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

        without_manifest = _docker("run", "--rm", image, "--version", check=False)

        assert without_manifest.returncode == 0
        assert without_manifest.stdout.startswith("kinby ")
        assert without_manifest.stderr == ""
    finally:
        _docker("image", "rm", "--force", image, check=False)


@pytest.mark.skipif(not _docker_is_available(), reason="Docker daemon is not available")
@pytest.mark.parametrize(
    ("program", "prefix"),
    [
        ("git", "git version "),
        ("uv", "uv "),
    ],
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


def _compose_cli() -> bool:
    """True when ``docker compose`` can run. The daemon is a separate check."""
    if shutil.which("docker") is None:
        return False
    try:
        result = _docker("compose", "version", check=False, timeout=10)
    except OSError, subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


@pytest.mark.skipif(not _docker_is_available(), reason="Docker daemon is not available")
def test_base_image_boots_without_coding_clients_even_with_a_github_token(tmp_path: Path) -> None:
    image = f"kinby-container-test-{uuid.uuid4().hex}"
    instance = tmp_path / "instance"
    skills = instance / "workspace" / ".claude" / "skills" / "tdd"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("# tdd\n", encoding="utf-8")
    (instance / "kinby.toml").write_text(
        'id = "vanilla"\n[models]\nmain = "openai:gpt-5"\n',
        encoding="utf-8",
    )
    mounts = ("--mount", f"type=bind,src={instance},dst=/instance")

    try:
        _docker("build", "--quiet", "--tag", image, ".")

        installed = [
            program
            for program in ("gh", "claude", "codex")
            if _docker(
                "run", "--rm", "--entrypoint", "which", image, program, check=False
            ).returncode
            == 0
        ]
        result = _docker(
            "run",
            "--rm",
            "--env",
            "GH_TOKEN=token",
            *mounts,
            image,
            "instance",
            "show",
            check=False,
        )

        assert installed == []
        assert result.returncode == 0, result.stderr
        assert "id: vanilla" in result.stdout
        assert not (instance / "workspace" / ".agents").exists()
    finally:
        _allow_temp_mount_cleanup(image, mounts, "/instance")
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
        _docker(
            "run",
            "--rm",
            *mounts,
            "--entrypoint",
            "sh",
            image,
            "-c",
            "printf 'edited\\n' > /instance/workspace/NOTES.md",
        )
        second = _docker("run", "--rm", *mounts, image, "instance", "show", check=False)

        assert first.returncode == 0, first.stderr
        assert "workspace: /instance/workspace (present)" in first.stdout
        assert second.returncode == 0, second.stderr
        assert (instance / "workspace" / "NOTES.md").read_text(encoding="utf-8") == "edited\n"
    finally:
        _allow_temp_mount_cleanup(image, mounts, "/instance")
        _docker("image", "rm", "--force", image, check=False)


def _compose(name: str) -> dict[str, object]:
    parsed: dict[str, object] = yaml.safe_load((PROJECT_ROOT / name).read_text(encoding="utf-8"))
    return parsed


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict), value
    return value


def _sequence(value: object) -> list[object]:
    assert isinstance(value, list), value
    return value


def _strings(value: object) -> list[str]:
    return [str(item) for item in _sequence(value)]


def _variable(value: str) -> str:
    """The environment variable a compose value interpolates, without its default clause."""
    return value.removeprefix("${").split("}", 1)[0].split(":", 1)[0]


def test_the_hub_recipe_keeps_instances_private_and_maps_the_docker_host_path() -> None:
    recipe = _compose("compose.hub.yaml")
    services = _mapping(recipe["services"])
    hub = _mapping(services["hub"])
    caddy = _mapping(services["caddy"])
    flags = dict(
        argument.split("=", 1) for argument in _strings(hub["command"]) if argument.startswith("--")
    )
    binds = {
        str(_mapping(volume)["target"]): str(_mapping(volume)["source"])
        for volume in _sequence(hub["volumes"])
    }
    private = _mapping(_mapping(recipe["networks"])[flags["--network"]])
    caddyfile = (PROJECT_ROOT / "docker" / "Caddyfile.hub").read_text(encoding="utf-8")

    assert _variable(binds["/hub"]) == "KINBY_HUB_DIR"
    assert _variable(flags["--docker-host-directory"]) == "KINBY_HUB_DIR"
    assert binds["/var/run/docker.sock"] == "/var/run/docker.sock"
    assert private == {"name": flags["--network"]}
    assert set(_strings(hub["networks"])) == {"kinby_public", flags["--network"]}
    assert flags["--network"] not in _strings(caddy["networks"])
    assert f"reverse_proxy hub:{flags['--listen'].rsplit(':', 1)[1]}" in caddyfile


@pytest.mark.skipif(not _compose_cli(), reason="Docker Compose is not available")
def test_the_hub_recipe_is_a_valid_compose_project() -> None:
    result = _docker(
        "compose",
        "--file",
        "compose.hub.yaml",
        "config",
        "--no-path-resolution",
        "--quiet",
        check=False,
        env={
            **os.environ,
            "KINBY_HUB_DIR": "/tmp/hub",
            "KINBY_DOMAIN": "kinby.example",
        },
    )

    assert result.returncode == 0, result.stderr
