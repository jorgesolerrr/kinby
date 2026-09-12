import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[1]


def _write_command(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _run_deploy_wizard(repo: Path, commands: Path, hooks: Path) -> str:
    result = subprocess.run(
        ["bash", str(repo / "scripts" / "deploy-wizard.sh")],
        cwd=repo,
        env={
            **os.environ,
            "DEPLOY_TEST_HOOKS": str(hooks),
            "PATH": f"{commands}:{os.environ['PATH']}",
        },
        input="\n\n\n\n\nn\n\n\n",
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _deploy_wizard_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    instance = repo / "instances" / "coder"
    commands = tmp_path / "bin"
    scripts.mkdir(parents=True)
    instance.mkdir(parents=True)
    commands.mkdir()
    shutil.copy(PROJECT_ROOT / "scripts" / "deploy-wizard.sh", scripts)
    (repo / ".env").write_text("KINBY_DOMAIN=kinby.example.test\n", encoding="utf-8")
    (instance / ".env").write_text(
        "ANTHROPIC_API_KEY=api-key\n"
        "CLAUDE_CODE_OAUTH_TOKEN=claude-token\n"
        "GH_TOKEN=github-token\n"
        "GIT_USER_NAME=Kinby Coder\n"
        "GIT_USER_EMAIL=coder@example.test\n"
        "GITHUB_WEBHOOK_SECRET=webhook-secret\n",
        encoding="utf-8",
    )
    hooks = tmp_path / "hooks.txt"
    hooks.write_text(
        "https://kinby.example.test/signals/implement-ready-issue\n",
        encoding="utf-8",
    )
    _write_command(
        commands / "gh",
        """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

arguments = sys.argv[1:]
hooks = Path(os.environ["DEPLOY_TEST_HOOKS"])
if arguments[:2] == ["repo", "view"]:
    print("jorgesolerrr/kinby")
elif arguments[:2] == ["auth", "token"]:
    print("github-token")
elif arguments[:2] == ["api", "user"]:
    print("kinby-coder")
elif arguments[:1] == ["api"] and arguments[-1].endswith("/labels/merge-ready"):
    pass
elif arguments[:1] == ["api"] and "--method" not in arguments:
    print(hooks.read_text(encoding="utf-8"), end="")
elif arguments[:1] == ["api"]:
    url = next(value.split("=", 1)[1] for value in arguments if value.startswith("config[url]="))
    with hooks.open("a", encoding="utf-8") as stream:
        stream.write(f"{url}\\n")
    with hooks.with_suffix(".log").open("a", encoding="utf-8") as stream:
        stream.write("\\0".join(arguments) + "\\n")
""",
    )
    _write_command(
        commands / "curl",
        """#!/usr/bin/env bash
if [[ "$*" == *ifconfig.me* ]]; then printf '203.0.113.1'; fi
""",
    )
    _write_command(
        commands / "dig",
        """#!/usr/bin/env bash
printf '203.0.113.1\n'
""",
    )
    for command in ("docker", "git", "xdg-open"):
        _write_command(commands / command, "#!/usr/bin/env bash\n")
    return repo, commands, hooks


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


def test_deploy_wizard_registers_each_webhook_once(tmp_path: Path) -> None:
    repo, commands, hooks = _deploy_wizard_fixture(tmp_path)

    first = _run_deploy_wizard(repo, commands, hooks)

    implement_url = "https://kinby.example.test/signals/implement-ready-issue"
    babysit_url = "https://kinby.example.test/signals/babysit-pull-request"
    assert "Start the stack and register the permanent webhooks" in first
    assert f"Webhook for {implement_url} already registered." in first
    assert f"Registered the repository webhook at {babysit_url}." in first
    assert hooks.read_text(encoding="utf-8").splitlines() == [
        implement_url,
        babysit_url,
    ]
    registration = hooks.with_suffix(".log").read_text(encoding="utf-8")
    for argument in (
        "events[]=pull_request_review",
        "events[]=pull_request_review_comment",
        "events[]=issue_comment",
        f"config[url]={babysit_url}",
        "config[secret]=webhook-secret",
    ):
        assert argument in registration.split("\0")

    hooks.with_suffix(".log").unlink()
    second = _run_deploy_wizard(repo, commands, hooks)

    assert f"Webhook for {implement_url} already registered." in second
    assert f"Webhook for {babysit_url} already registered." in second
    assert not hooks.with_suffix(".log").exists()


def test_container_docs_show_both_webhook_commands() -> None:
    container = (PROJECT_ROOT / "docs" / "container.md").read_text(encoding="utf-8")

    assert (
        "gh webhook forward --repo jorgesolerrr/kinby \\\n"
        "  --events pull_request_review,pull_request_review_comment,issue_comment \\\n"
        "  --url http://localhost:8787/signals/babysit-pull-request \\\n"
        '  --secret "$GITHUB_WEBHOOK_SECRET"'
    ) in container
    assert (
        "gh api --method POST repos/jorgesolerrr/kinby/hooks \\\n"
        "  -f name=web -F active=true -f 'events[]=pull_request_review' \\\n"
        "  -f 'events[]=pull_request_review_comment' -f 'events[]=issue_comment' \\\n"
        '  -f "config[url]=https://$KINBY_DOMAIN/signals/babysit-pull-request" \\\n'
        '  -f config[content_type]=json -f "config[secret]=$GITHUB_WEBHOOK_SECRET"'
    ) in container


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
