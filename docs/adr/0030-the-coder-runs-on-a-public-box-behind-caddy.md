# The coder runs on a public box behind Caddy

The first always-on deploy of the **coder** instance follows the first supervised run
(#187). Its nine findings and the runtime contract in `docs/container.md` set these
decisions.

## Decisions

- **Host.** A Linux VPS with a public IPv4 address runs the Compose stack under
  `/home/jorge/dev/kinby`. The box is shared with other agent work; kinby is one Compose
  project on it.
- **Ingress.** `compose.public.yaml` adds a Caddy service that terminates TLS for
  `KINBY_DOMAIN` and proxies to the receiver. GitHub calls a permanent repository webhook
  for `issues` and `pull_request` events at that name. The receiver serves only
  `/signals/<routine>` and `/health`, so the proxy forwards everything.
- **Authentication.** The Anthropic SDK uses `ANTHROPIC_API_KEY`. Claude Code uses the
  one-year `claude setup-token`. Codex signs in once by device code into the Compose volume.
  The entrypoint copies a mounted profile only when the mount holds files, so a box
  without a host profile boots on the key.
- **Identity.** The coder pushes and comments through `GH_TOKEN`. A bot account is
  the recommended identity: GitHub drops a review request whose reviewer is the PR
  author, and a separate name marks agent work in the repository. The deploy works
  with the maintainer's token when no bot account exists yet.
- **Updates.** `docker/update.sh` runs from cron. It fast-forwards the checkout to
  `origin/main` and rebuilds, and it defers while `docker compose top` shows a Codex or
  Claude Code process, so a pipeline finishes before the container restarts.
- **Watching.** `restart: unless-stopped` and a Docker healthcheck on `/health`. The
  coder's comment on each issue is the human-facing signal for success and failure.

## Rejected options

- **A registry image built by GitHub Actions.** Rejected for the first deploy: a build on
  the box needs no registry credentials or workflow, and a 4 vCPU box builds the image
  in minutes. Revisit when more than one box runs kinby.
- **A tunnel from a home box.** Rejected because the VPS exists and a tunnel is one
  more process to watch.
- **A mounted `ant auth login` profile.** Rejected on the box: the container rotates the
  refresh token, so a copied profile dies on the next container (finding 5 of #187),
  and the orchestrator's spend is cents per run.
- **Publishing the receiver port directly.** Rejected: GitHub signs deliveries, but the
  receiver would then speak plain HTTP on a public port, and Caddy costs one service.

## Accepted risks

- The box reboots for security updates at 04:30 UTC when a package asks for it. A
  pipeline running at that moment fails; the label stays, and the catch-up scan after
  boot starts it again.
- A restart runs the catch-up scan within seconds, so a stale `ready-for-agent` label is
  a run (finding 6 of #187). The label is the contract.
