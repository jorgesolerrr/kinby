#!/usr/bin/env bash
# Deploy main to the playground hub, then update its instances to that commit.
#   deploy.sh                    update every active instance
#   deploy.sh <instance-id> ...  update only these
# Takes several minutes: the hub image and each instance image build on the box.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"

ssh playground bash -s <<'REMOTE'
set -euo pipefail
cd ~/dev/kinby
git pull --ff-only origin main
docker compose -f compose.hub.yaml up --build --detach
# The recreated hub needs a moment before it listens.
for _ in $(seq 60); do
    if docker compose -f compose.hub.yaml exec -T hub \
        python -c 'import socket; socket.create_connection(("localhost", 8080), 1)' 2>/dev/null; then
        exit 0
    fi
    sleep 2
done
echo "The hub did not listen on 8080 within two minutes." >&2
docker compose -f compose.hub.yaml logs --tail 50 hub >&2
exit 1
REMOTE

revision="$(ssh playground git -C dev/kinby rev-parse HEAD)"
echo "Hub rebuilt at $revision."
"$here/hub.sh" update "$revision" "$@"
