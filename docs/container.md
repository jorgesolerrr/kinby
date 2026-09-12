# Container contract

The root `Dockerfile` builds one image for every kinby instance. Instance identity, behavior, workspace configuration, and state do not belong in the image.

## Runtime contract

- Mount one instance directory at `/instance`. The image declares this path as a volume.
- `KINBY_INSTANCE` is set to `/instance`, so commands use the mounted instance unless an explicit path overrides it.
- Pass provider credentials and other secrets as environment variables at runtime. Do not add them to the image or `kinby.toml`. Claude Code reads `CLAUDE_CODE_OAUTH_TOKEN`, a one-year subscription token from `claude setup-token`. The Anthropic SDK can use `ANTHROPIC_API_KEY` or an `ant auth login` profile mounted read-only at `/anthropic-profile`.
- The image entrypoint is `kinby-entrypoint`, a shell script that prepares the mounted instance and then runs `kinby` with the container command. The default command is `run`.
- Runtime data written under the instance's `.state/` directory persists with the mounted instance, including the shadow repository at `.state/snapshots.git` that holds the workspace snapshots.
- The image ships git, gh, uv, Claude Code, and Codex. The Dockerfile pins both coding client versions. Workspace snapshots run git as a subprocess. Without git, kinby boots with snapshots off and one warning. gh and uv serve a coding workspace through issue and pull request operations and the workspace's own checks.

Build the image and inspect the minimal example:

```sh
docker build -t kinby .
docker run --rm \
  --mount type=bind,src="$PWD/examples/instances/minimal",dst=/instance \
  kinby instance show
```

Pass secrets with individual `--env` flags, `--env-file`, or the equivalent setting in the container platform. An env file used by the platform should live outside the image build context.

## Entrypoint

`docker/entrypoint.sh` runs before kinby on every start:

1. Marks every directory safe for git, since a bind-mounted repository belongs to the host user, and sets the commit identity from `GIT_USER_NAME` and `GIT_USER_EMAIL` when they are set.
2. Copies a profile mounted at `/anthropic-profile` into `/root/.config/anthropic` with owner-only permissions, unless a copy already exists.
3. Configures git to authenticate through gh when `GH_TOKEN` is set, so `git push` works with the same token gh uses.
4. Clones `[workspace].source` into the workspace path when that path is absent or empty. A non-empty workspace is left untouched, so a later start never overwrites work in progress.
5. Writes `/root/.codex/config.toml` when the file is absent. Codex takes one skill folder per `[[skills.config]]` entry, so the entrypoint writes one entry for each folder under the workspace's `.claude/skills` directory. It also links `.agents/skills` to that directory because Codex discovers repository skills at `.agents/skills`. Existing config and links are left untouched.

[ADR 0003](adr/0003-container-entrypoint-owns-workspace-cloning.md) records why this boundary belongs to the container entrypoint.

## Compose

`compose.yaml` at the repository root runs the instances under `instances/`, one service per instance directory. Each service builds the same image, mounts its instance at `/instance`, reads secrets from the instance's `.env` (copy `.env.example`), and runs `kinby serve`. The `coder-workspace` volume stores the cloned workspace at `/instance/workspace`. The `coder-codex` volume stores the container's Codex login and config at `/root/.codex`. This separate volume prevents the container from reading or changing the host's Codex login.

```sh
cp instances/coder/.env.example instances/coder/.env
claude setup-token
```

Copy the printed token into `CLAUDE_CODE_OAUTH_TOKEN` in `instances/coder/.env`. Claude Code uses this subscription token for headless runs. Then start the service:

```sh
docker compose up --build --detach coder
docker compose logs --follow coder
```

After the service starts, sign Codex in to ChatGPT once with device-code authentication:

```sh
docker compose exec coder codex login --device-auth
```

Open the URL from the command, sign in, and enter the one-time code. Codex stores the login in `coder-codex`, so restarts keep it. Removing the Compose volumes also removes this login and the workspace clone.

Run one-word prompts to check both subscription logins:

```sh
docker compose exec coder codex exec "Reply with one word: ready"
docker compose exec coder claude -p "Reply with one word: ready"
```

The `coder` instance listens on `127.0.0.1:8787`. Its two GitHub routines use separate signal paths, both signed with `GITHUB_WEBHOOK_SECRET`:

- `/signals/implement-ready-issue` accepts `issues` and `pull_request` deliveries.
- `/signals/babysit-pull-request` accepts `pull_request_review`, `pull_request_review_comment`, and `issue_comment` deliveries.

Relevant deliveries start a scan, and each routine's hourly schedule catches missed deliveries.

A local box has no public URL for GitHub to call. Forward the repository's webhook deliveries with the gh webhook extension (`gh extension install cli/gh-webhook`), using the same secret as the instance. Run each forwarder in its own terminal:

```sh
gh webhook forward --repo jorgesolerrr/kinby --events issues,pull_request \
  --url http://localhost:8787/signals/implement-ready-issue \
  --secret "$GITHUB_WEBHOOK_SECRET"
gh webhook forward --repo jorgesolerrr/kinby \
  --events pull_request_review,pull_request_review_comment,issue_comment \
  --url http://localhost:8787/signals/babysit-pull-request \
  --secret "$GITHUB_WEBHOOK_SECRET"
```

The forwarder registers a temporary webhook and relays deliveries while it runs. An always-on box registers a permanent webhook instead, as described next.

## Always-on box

`compose.public.yaml` adds Caddy in front of the coder. Caddy obtains a certificate for `KINBY_DOMAIN`, read from a `.env` next to `compose.yaml`, and proxies to the receiver. The box needs a public IPv4 address, a DNS `A` record for that name, and ports 80 and 443 open. [ADR 0030](adr/0030-the-coder-runs-on-a-public-box-behind-caddy.md) records the deploy decisions.

```sh
echo KINBY_DOMAIN=kinby.example.com > .env
docker compose -f compose.yaml -f compose.public.yaml up --build --detach
curl -fsS "https://$KINBY_DOMAIN/health"
```

On a box without an `ant auth login` profile, set `ANTHROPIC_API_KEY` in the instance `.env`; the profile mount then stays empty and the entrypoint leaves it alone. The instance `.env` also needs `CLAUDE_CODE_OAUTH_TOKEN`, `GH_TOKEN`, `GITHUB_WEBHOOK_SECRET`, and the commit identity. `scripts/deploy-wizard.sh` walks through every value, starts the stack, registers the permanent webhook, and signs Codex in. Run it on the box from the repository root.

Register the permanent webhooks by hand with the same secret as the instance. The `merge-ready` label must also exist:

```sh
gh api --method POST repos/jorgesolerrr/kinby/hooks \
  -f name=web -F active=true -f 'events[]=issues' -f 'events[]=pull_request' \
  -f "config[url]=https://$KINBY_DOMAIN/signals/implement-ready-issue" \
  -f config[content_type]=json -f "config[secret]=$GITHUB_WEBHOOK_SECRET"
gh api --method POST repos/jorgesolerrr/kinby/hooks \
  -f name=web -F active=true -f 'events[]=pull_request_review' \
  -f 'events[]=pull_request_review_comment' -f 'events[]=issue_comment' \
  -f "config[url]=https://$KINBY_DOMAIN/signals/babysit-pull-request" \
  -f config[content_type]=json -f "config[secret]=$GITHUB_WEBHOOK_SECRET"
gh label create merge-ready --color 0E8A16 \
  --description "Agent pull request is reviewed and ready to merge"
```

`docker/update.sh` keeps the box on main. It fetches, exits when nothing changed, defers while a coding client is running inside the coder, and otherwise fast-forwards and rebuilds. Run it from cron:

```
30 * * * * /home/jorge/dev/kinby/docker/update.sh >> /home/jorge/kinby-update.log 2>&1
```

A restart, whether from the update script or from the box rebooting, starts the hourly catch-up scan a few seconds later. Any stale `ready-for-agent` label becomes a run, so the label means what it says.
