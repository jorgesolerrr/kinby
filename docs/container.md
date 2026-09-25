# Container contract

The root `Dockerfile` builds one image for every kinby instance. Instance identity, behavior, workspace configuration, and state do not belong in the image.

The instance image is the Dockerfile's first stage and its default target. The hub builds instances from that stage alone. The `hub` target adds the built web app at `/usr/local/share/kinby/web`, from a Bun stage that runs only for that target. The Python wheel does not carry the app, and instances never serve it.

## Runtime contract

- Mount one instance directory at `/instance`. The image declares this path as a volume.
- `KINBY_INSTANCE` is set to `/instance`, so commands use the mounted instance unless an explicit path overrides it.
- `KINBY_CONTROL_TOKEN` is the instance's control token. With it set, `kinby serve` carries the contract over WebSocket at `GET /ws` and `GET /control` on the receiver's port, and a caller presents the token as `Authorization: Bearer <token>`. Without it, serve mode starts the receiver alone.
- `kinby repl --connect <url>` drives that socket from outside the container, reading its bearer token from `KINBY_TOKEN`. `kinby repl <dir>` runs the instance in its own process instead, and refuses to start while another process holds the instance's runtime lock at `.state/runtime.lock`.
- Pass provider credentials and other secrets as environment variables at runtime. Do not add them to the image or `kinby.toml`. The Anthropic SDK can use `ANTHROPIC_API_KEY` or an `ant auth login` profile mounted read-only at `/anthropic-profile`.
- The image entrypoint is `kinby-entrypoint`, a shell script that prepares the mounted instance and then runs `kinby` with the container command. The default command is `repl`.
- Runtime data written under the instance's `.state/` directory persists with the mounted instance, including the shadow repository at `.state/snapshots.git` that holds the workspace snapshots.
- The image ships git and uv only. Workspace snapshots run git as a subprocess. Without git, kinby boots with snapshots off and one warning. uv installs a package on top of the image. A package that needs other programs adds them with its own image recipe, as the software factory does for gh, Claude Code, and Codex.

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
3. Configures git to authenticate through gh when `GH_TOKEN` is set and the image has gh, so `git push` works with the same token gh uses.
4. Clones `[workspace].source` into the workspace path when that path is absent or empty. A non-empty workspace is left untouched, so a later start never overwrites work in progress.
5. When the image has Codex, writes `/root/.codex/config.toml` if the file is absent. Codex takes one skill folder per `[[skills.config]]` entry, so the entrypoint writes one entry for each folder under the workspace's `.claude/skills` directory. It also links `.agents/skills` to that directory because Codex discovers repository skills at `.agents/skills`. Existing config and links are left untouched.

[ADR 0003](adr/0003-container-entrypoint-owns-workspace-cloning.md) records why this boundary belongs to the container entrypoint.

## Hub mount mapping

The hub needs two explicit paths when it runs in a container: the instances directory as the hub sees it and the same directory on the Docker host. Docker bind sources are always host paths. For example, mount host `/srv/kinby` at `/hub` in the hub container, then configure the hub directory as `/hub` and the Docker-host directory as `/srv/kinby`. The hub rejects instance identities that do not resolve to one direct child of its instances directory. Named workspace and Codex volumes do not need path translation.

## Hub deployment

`compose.hub.yaml` is the reference recipe. Caddy terminates TLS for `KINBY_DOMAIN` and forwards every public request to the hub; `KINBY_HUB_DIR` is the hub directory on the Docker host, used both as the bind source for `/hub` and as `--docker-host-directory`. Instances join `kinby_private`. Caddy stays on `kinby_public`, and instance ports stay unpublished, so a client reaches an instance through the hub's relay. `kinby_private` is a normal bridge, so the instance can reach model providers, git, and GitHub.

If a previous hub created `kinby_private` as an internal network, the next instance create or start attaches every container on it, stopped ones included, to a temporary network first. It then recreates `kinby_private` as a normal bridge and moves those containers onto it. A failed step leaves each container on at least one of those networks.

```sh
printf 'KINBY_DOMAIN=kinby.example.com\nKINBY_HUB_DIR=/srv/kinby\n' > .env
docker compose -f compose.hub.yaml up --build --detach
docker compose -f compose.hub.yaml logs hub   # the access token is printed once
```

The hub serves these routes:

| Route | Purpose |
| --- | --- |
| `POST /auth/login` | Exchange the access token for the session cookie the browser carries. |
| `GET /ws` | The hub's own contract: instances, lifecycle operations. |
| `GET /instances/{instance_id}/ws` | One instance's contract, relayed frame for frame to its private `/ws`. A stopped instance answers 503, an unknown one 404. |
| `POST /instances/{instance_id}/signals/{routine}` | A webhook, forwarded byte for byte. The instance authenticates it and answers it; the hub queues nothing. |
| `POST /signals/{routine}` | The same, for the instance holding the signal alias. |
| `/assets`, `/{tail:.*}` | The built web app, with the `index.html` fallback. The hub serves the app the image carries, or the directory `--web-app` names. With neither, these routes are absent. |

The relay reaches an instance's `/ws` only, never its `/control`, so no client that arrives through the hub holds `instance:lifecycle`. Adoption keeps an established webhook URL by claiming the bare signal path:

```sh
kinby hub /srv/kinby signals <instance-id>
```

## The software factory

The software factory is a package in its own repository, [kinby-code-factory](https://github.com/jorgesolerrr/kinby-code-factory). It owns the image recipe that adds its coding clients, the instance template, and the webhook setup. [Set up a software factory](https://github.com/jorgesolerrr/kinby-code-factory/blob/main/docs/setup.md) walks through a new instance, and [the coder migration](https://github.com/jorgesolerrr/kinby-code-factory/blob/main/docs/migration.md) moves the existing coder onto the package and under the hub.
