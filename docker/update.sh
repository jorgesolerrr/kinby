#!/bin/sh
# Rebuild and restart the always-on instances when main moves. Run from cron on the box.
# The restart waits while a coding client is running, so a pipeline is never cut short.
set -eu

cd "$(dirname "$0")/.."
git fetch --quiet origin main
if [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ]; then
    exit 0
fi
if docker compose top coder 2>/dev/null | grep -Eq 'codex|claude'; then
    echo "update deferred: a coding client is running"
    exit 0
fi
git merge --ff-only --quiet origin/main
echo "updating to $(git rev-parse --short HEAD)"
docker compose -f compose.yaml -f compose.public.yaml up --build --detach --remove-orphans
docker image prune --force >/dev/null
