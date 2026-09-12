import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tests.test_factory import (
    _arguments,
    _records,
    _write_executable,
    _write_fake_github,
)

PROJECT_ROOT = Path(__file__).parents[1]


@dataclass(frozen=True)
class DeployWizardSandbox:
    repo: Path
    commands: Path
    responses: Path
    log: Path

    @property
    def hooks(self) -> Path:
        return self.responses / "hooks.txt"


def _wizard_answers() -> str:
    ready_to_start = ""
    keep_domain = ""
    dns_record_saved = ""
    keep_anthropic_key = ""
    keep_claude_token = ""
    use_bot_account = "n"
    keep_git_user_name = ""
    keep_git_user_email = ""
    return "\n".join(
        (
            ready_to_start,
            keep_domain,
            dns_record_saved,
            keep_anthropic_key,
            keep_claude_token,
            use_bot_account,
            keep_git_user_name,
            keep_git_user_email,
            "",
        )
    )


def _run_deploy_wizard(sandbox: DeployWizardSandbox) -> str:
    result = subprocess.run(
        ["bash", str(sandbox.repo / "scripts" / "deploy-wizard.sh")],
        cwd=sandbox.repo,
        env={
            **os.environ,
            "FAKE_GITHUB_LOG": str(sandbox.log),
            "FAKE_GITHUB_RESPONSES": str(sandbox.responses),
            "PATH": f"{sandbox.commands}:{os.environ['PATH']}",
        },
        input=_wizard_answers(),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _deploy_wizard_sandbox(tmp_path: Path) -> DeployWizardSandbox:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    instance = repo / "instances" / "coder"
    commands = tmp_path / "bin"
    responses = tmp_path / "github"
    scripts.mkdir(parents=True)
    instance.mkdir(parents=True)
    commands.mkdir()
    responses.mkdir()
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
    (responses / "repository.txt").write_text("jorgesolerrr/kinby\n", encoding="utf-8")
    (responses / "auth-token.txt").write_text("github-token\n", encoding="utf-8")
    (responses / "user.txt").write_text("kinby-coder\n", encoding="utf-8")
    (responses / "hooks.txt").write_text(
        "https://kinby.example.test/signals/implement-ready-issue\n",
        encoding="utf-8",
    )
    _write_fake_github(commands / "gh")
    _write_executable(
        commands / "curl",
        """import sys

if "https://ifconfig.me" in sys.argv:
    print("203.0.113.1")
""",
    )
    _write_executable(commands / "dig", 'print("203.0.113.1")\n')
    for command in ("docker", "git", "xdg-open"):
        _write_executable(commands / command, "")
    return DeployWizardSandbox(repo, commands, responses, tmp_path / "github.jsonl")


def test_deploy_wizard_registers_each_webhook_once(tmp_path: Path) -> None:
    sandbox = _deploy_wizard_sandbox(tmp_path)

    first = _run_deploy_wizard(sandbox)

    implement_url = "https://kinby.example.test/signals/implement-ready-issue"
    babysit_url = "https://kinby.example.test/signals/babysit-pull-request"
    assert "Start the stack and register the permanent webhooks" in first
    assert f"Webhook for {implement_url} already registered." in first
    assert f"Registered the repository webhook at {babysit_url}." in first
    assert sandbox.hooks.read_text(encoding="utf-8").splitlines() == [
        implement_url,
        babysit_url,
    ]
    registrations = [
        _arguments(record)
        for record in _records(sandbox.log)
        if _arguments(record)[:3] == ["api", "--method", "POST"]
    ]
    assert len(registrations) == 1
    for argument in (
        "events[]=pull_request_review",
        "events[]=pull_request_review_comment",
        "events[]=issue_comment",
        f"config[url]={babysit_url}",
        "config[secret]=webhook-secret",
    ):
        assert argument in registrations[0]

    second = _run_deploy_wizard(sandbox)

    assert f"Webhook for {implement_url} already registered." in second
    assert f"Webhook for {babysit_url} already registered." in second
    registrations = [
        record
        for record in _records(sandbox.log)
        if _arguments(record)[:3] == ["api", "--method", "POST"]
    ]
    assert len(registrations) == 1


def test_container_docs_show_both_webhook_commands() -> None:
    container = (PROJECT_ROOT / "docs" / "container.md").read_text(encoding="utf-8")
    shell_blocks = re.findall(r"```sh\n(.*?)```", container, flags=re.DOTALL)
    forward = next(
        block
        for block in shell_blocks
        if "gh webhook forward" in block and "/signals/babysit-pull-request" in block
    )
    permanent = next(
        block
        for block in shell_blocks
        if "gh api --method POST" in block and "/signals/babysit-pull-request" in block
    )

    assert "registers the permanent webhooks" in container
    for block in (forward, permanent):
        assert "/signals/implement-ready-issue" in block
        assert "/signals/babysit-pull-request" in block
        for event in (
            "pull_request_review",
            "pull_request_review_comment",
            "issue_comment",
        ):
            assert event in block
    assert '--secret "$GITHUB_WEBHOOK_SECRET"' in forward
    assert "config[secret]=$GITHUB_WEBHOOK_SECRET" in permanent
