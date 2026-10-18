#!/bin/sh
# Copies the checkout mounted at /source onto the container's own disk, then runs the gate there.
# The tests run about three times slower on a Windows bind mount than on the container's disk.
# git on the host lists the files in /source/.check-files: in a linked worktree, the git metadata
# the container would need sits outside the mount.
set -e
tar -C /source --null --files-from=/source/.check-files \
    --ignore-failed-read --warning=no-failed-read -cf - | tar -C /kinby -xf -
cd /kinby
exec bun run check
