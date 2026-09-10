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

exec kinby "$@"
