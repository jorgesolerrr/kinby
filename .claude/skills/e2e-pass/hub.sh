#!/usr/bin/env bash
# Run hub.py inside the playground's hub container, signed in with the hub's access token.
#   hub.sh instances
#   hub.sh update <revision> [<instance-id> ...]
#   hub.sh secrets <instance-id>    < NAME=value lines
#   hub.sh retry <run>
#   hub.sh send-back <run> <step>
#   hub.sh cancel <run>
#   hub.sh tokens <instance-id> [--since <iso-date-or-time>]
# A <run> is a run id, or an issue reference like 825 or '#825' for the issue's unfinished run.
# The token is read from /hub/access-token inside the container and never leaves the box.
# stdin passes through, so secret values travel over ssh and never sit on a command line.
set -euo pipefail

args=()
for arg in "$@"; do
    # '.' and '+' admit an ISO time's fractional seconds and positive UTC offset.
    if [[ ! $arg =~ ^(#[0-9]+|[A-Za-z0-9_:.+-]+)$ ]]; then
        echo "hub.sh: unexpected argument '$arg'" >&2
        exit 2
    fi
    # A '#' would start a comment in the remote shell, and hub.py takes the bare issue number.
    args+=("${arg#\#}")
done

script=hub.py
if [[ ${args[0]-} == tokens ]]; then
    # The probe reads the instance's files, which the hub's container mounts under /hub/instances.
    args=("/hub/instances/${args[1]-}" "${args[@]:2}")
    script=tokens.py
fi

# The arguments expand on this side on purpose; the loop above allows no shell syntax in them.
# shellcheck disable=SC2029
ssh playground "cd ~/dev/kinby && docker compose -f compose.hub.yaml exec -T hub sh -c '\
export KINBY_TOKEN=\"\$(cat /hub/access-token)\"; \
exec python /source/.claude/skills/e2e-pass/$script ${args[*]}'"
