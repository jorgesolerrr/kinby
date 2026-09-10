#!/bin/sh
# Prepare the mounted instance, then run kinby.
set -eu

instance="${KINBY_INSTANCE:-/instance}"

# Bind-mounted repositories belong to the host user.
git config --global --add safe.directory '*'
if [ -n "${GIT_USER_NAME:-}" ]; then
    git config --global user.name "$GIT_USER_NAME"
fi
if [ -n "${GIT_USER_EMAIL:-}" ]; then
    git config --global user.email "$GIT_USER_EMAIL"
fi
# git pushes through gh when a GitHub token is present.
if [ -n "${GH_TOKEN:-}" ]; then
    gh auth setup-git
fi

# An `ant auth login` profile mounted read-only becomes the SDK's profile, with the
# owner-only mode the SDK enforces on credentials. An existing copy keeps its refreshed token.
if [ -d /anthropic-profile ] && [ ! -d /root/.config/anthropic ]; then
    mkdir -p /root/.config
    cp -r /anthropic-profile /root/.config/anthropic
    chmod -R go-rwx /root/.config/anthropic
fi

# Clone the workspace source on first boot; leave a non-empty workspace untouched (ADR 0003).
eval "$(python - "$instance" <<'PY'
import shlex
import sys
from pathlib import Path

from kinby.instance import ManifestError, load_instance

try:
    workspace = load_instance(Path(sys.argv[1])).manifest.workspace
except ManifestError:
    # kinby reports the manifest problem itself when it starts.
    sys.exit(0)
print(f"workspace_path={shlex.quote(str(workspace.path))}")
print(f"workspace_source={shlex.quote(workspace.source or '')}")
PY
)"
if [ -n "${workspace_source:-}" ] && [ -z "$(ls -A "$workspace_path" 2>/dev/null)" ]; then
    git clone "$workspace_source" "$workspace_path"
fi

# Share the workspace's Claude skills with Codex. Codex config addresses one skill
# directory at a time, while repository discovery uses .agents/skills.
codex_home="${CODEX_HOME:-/root/.codex}"
python - "$workspace_path" "$codex_home" <<'PY'
import json
import sys
from pathlib import Path

workspace = Path(sys.argv[1])
codex_home = Path(sys.argv[2])
shared_skills = workspace / ".claude" / "skills"
if not shared_skills.is_dir():
    sys.exit(0)

config = codex_home / "config.toml"
if not config.exists():
    skill_directories = sorted(
        skill_file.parent
        for skill_file in shared_skills.glob("*/SKILL.md")
        if skill_file.is_file()
    )
    entries = [
        "[[skills.config]]\n"
        f"path = {json.dumps(str(skill_directory))}\n"
        "enabled = true"
        for skill_directory in skill_directories
    ]
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("\n\n".join(entries) + "\n", encoding="utf-8")

codex_skills = workspace / ".agents" / "skills"
if not codex_skills.exists() and not codex_skills.is_symlink():
    codex_skills.parent.mkdir(parents=True, exist_ok=True)
    codex_skills.symlink_to(Path("../.claude/skills"), target_is_directory=True)
PY

exec kinby "$@"
