#!/bin/sh
# Copies the checkout mounted at /source onto the container's own disk, then runs the gate there.
# The tests run about three times slower on a Windows bind mount than on the container's disk.
set -e
git -C /source -c safe.directory=/source ls-files -z --cached --others --exclude-standard \
    | tar -C /source --null --files-from=- --ignore-failed-read --warning=no-failed-read -cf - \
    | tar -C /kinby -xf -
cd /kinby
exec bun run check
