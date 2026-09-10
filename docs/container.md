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

The `coder` instance listens on `127.0.0.1:8787`. Its `implement-ready-issue` routine accepts GitHub webhook deliveries at `/signals/implement-ready-issue`, signed with `GITHUB_WEBHOOK_SECRET`, and starts a turn only when an open issue receives the `ready-for-agent` label.

A local box has no public URL for GitHub to call. Forward the repository's webhook deliveries with the gh webhook extension (`gh extension install cli/gh-webhook`), using the same secret as the instance:

```sh
gh webhook forward --repo jorgesolerrr/kinby --events issues,pull_request \
  --url http://localhost:8787/signals/implement-ready-issue \
  --secret "$GITHUB_WEBHOOK_SECRET"
```

The forwarder registers a temporary webhook and relays deliveries while it runs. An always-on box registers a permanent repository webhook for both `issues` and `pull_request` events. Point that webhook at the instance's public address.
