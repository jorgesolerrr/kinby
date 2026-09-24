# CI updates instances through an update-only hub token

GitHub Actions updates instances through the hub's contract, the same `instance.update` a person runs. It holds a second hub secret, the **update token**. The token holds one scope, `hub:update`, and `instance.update` and `operation.get` are the only methods that require it. Every other hub method, the browser login, and the relay to an instance's own socket refuse it. The access token and its sessions keep every scope, `hub:update` included, because a hub still has one user ([ADR 0049](0049-a-cookie-session-for-the-browser-and-a-control-token-per-instance.md)).

`kinby hub <dir> update-token rotate` issues the token and prints it once. Running it again replaces the token, and the old one stops working on its next connection. The hub stores only the hash, next to the access token's, and the access token is untouched.

A leaked update token can move an instance to any revision the hub's source checkout already has. It cannot read the instance list, logs, or secrets, create or remove anything, or reach an agent. We accepted that. The update still prepares the candidate before it stops anything, so a bad revision fails and leaves the running instance where it was.

`kinby hub update --connect <url> <hub-instance-id> --revision <sha>` is the one command CI runs. It reads the token from `KINBY_TOKEN`, calls `instance.update`, reads the operation with `operation.get` until it ends, prints each step, and exits non-zero unless the update succeeded. `--package <id> --package-commit <sha>` adds a package pin ([ADR 0056](0056-a-package-can-be-pinned-to-a-git-commit.md)). The pin has to name the package, and the update token cannot read an instance to look the package up, so the command takes the ID as well. kinby's own workflow runs the four checks on every pull request and push to `main`. After a green push to `main`, it updates the coder to that commit, and it skips with a notice while `KINBY_HUB_URL` is unset. The factory repository runs the same command with a package pin.

The hub builds from its own source checkout, and the update does not fetch. The commit CI names has to reach that checkout first. `docker/update.sh` and its cron stay until the factory migration adopts the coder into the hub.

Decision agreed during [Roll out instance updates from CI through the hub](https://github.com/jorgesolerrr/kinby/issues/269), under [ADR 0049](0049-a-cookie-session-for-the-browser-and-a-control-token-per-instance.md) and [ADR 0052](0052-an-update-prepares-the-candidate-then-replaces-the-container.md).
