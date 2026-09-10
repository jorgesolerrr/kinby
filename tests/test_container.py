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
    [
        ("git", "git version "),
        ("gh", "gh version "),
        ("uv", "uv "),
        ("claude", "2.1.268 (Claude Code)"),
        ("codex", "codex-cli 0.154.0"),
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


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI is not available")
def test_compose_persists_codex_login_and_passes_claude_token() -> None:
    result = _docker(
        "compose",
        "config",
        "--format",
        "json",
        "--no-env-resolution",
        "--no-path-resolution",
    )
    config = json.loads(result.stdout)
    coder = config["services"]["coder"]

    assert {volume["source"]: volume["target"] for volume in coder["volumes"]}[
        "coder-codex"
    ] == "/root/.codex"
    assert "coder-codex" in config["volumes"]
    assert coder["env_file"] == [{"path": "instances/coder/.env"}]
    assert "CLAUDE_CODE_OAUTH_TOKEN=" in (
        PROJECT_ROOT / "instances" / "coder" / ".env.example"
    ).read_text(encoding="utf-8")


@pytest.mark.skipif(not _docker_is_available(), reason="Docker daemon is not available")
def test_entrypoint_writes_codex_skill_config_once(tmp_path: Path) -> None:
    image = f"kinby-container-test-{uuid.uuid4().hex}"
    instance = tmp_path / "instance"
    instance.mkdir()
    (instance / "kinby.toml").write_text(
        'id = "coder"\n[models]\nmain = "openai:gpt-5"\n',
        encoding="utf-8",
    )
    skills = instance / "workspace" / ".claude" / "skills"
    for name in ("implement-ticket", "tdd"):
        skill = skills / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    mounts = (
        "--mount",
        f"type=bind,src={instance},dst=/instance",
        "--mount",
        f"type=bind,src={codex_home},dst=/root/.codex",
    )

    try:
        _docker("build", "--quiet", "--tag", image, ".")

        first = _docker("run", "--rm", *mounts, image, "instance", "show", check=False)

        assert first.returncode == 0, first.stderr
        assert (codex_home / "config.toml").read_text(encoding="utf-8") == (
            '[[skills.config]]\npath = "/instance/workspace/.claude/skills/implement-ticket"\n'
            "enabled = true\n\n"
            '[[skills.config]]\npath = "/instance/workspace/.claude/skills/tdd"\n'
            "enabled = true\n"
        )
        assert (instance / "workspace" / ".agents" / "skills").readlink() == Path(
            "../.claude/skills"
        )

        _docker(
            "run",
            "--rm",
            *mounts,
            "--entrypoint",
            "sh",
            image,
            "-c",
            "printf 'edited = true\\n' > /root/.codex/config.toml",
        )
        second = _docker("run", "--rm", *mounts, image, "instance", "show", check=False)

        assert second.returncode == 0, second.stderr
        assert (codex_home / "config.toml").read_text(encoding="utf-8") == "edited = true\n"
    finally:
        _allow_temp_mount_cleanup(image, mounts, "/instance", "/root/.codex")
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
