#!/usr/bin/env bash
# Run hub.py inside the playground's hub container, signed in with the hub's access token.
#   hub.sh instances
#   hub.sh update <revision> [<instance-id> ...]
#   hub.sh secrets <instance-id>    < NAME=value lines
# The token is read from /hub/access-token inside the container and never leaves the box.
# stdin passes through, so secret values travel over ssh and never sit on a command line.
set -euo pipefail

for arg in "$@"; do
    if [[ ! $arg =~ ^[A-Za-z0-9-]+$ ]]; then
        echo "hub.sh: unexpected argument '$arg'" >&2
        exit 2
    fi
done

# The arguments expand on this side on purpose; the loop above allows no shell syntax in them.
# shellcheck disable=SC2029
ssh playground "cd ~/dev/kinby && docker compose -f compose.hub.yaml exec -T hub sh -c '\
export KINBY_TOKEN=\"\$(cat /hub/access-token)\"; \
exec python /source/.claude/skills/e2e-pass/hub.py $*'"
