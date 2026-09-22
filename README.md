# kinby

**An open-source, self-hosted personal AI teammate.**

*kinby* (an invented word — *kin* + *by*: kin at your side) is a personal AI agent you run on your own hardware. Not a chatbot you visit, but a teammate that remembers, acts on your behalf, and shows up proactively — in the spirit of Lindy, but open, single-user, and yours.

## Thesis

- **Graph-based memory.** Long-term memory is a knowledge graph, not a pile of chat logs. The first graph feed stores user-readable markdown nodes. kinby adds a database-backed feed only when memory evals justify it, while the profile remains the always-present file feed for preferences and standing instructions.
- **Routines as a first-class primitive.** Proactive behavior is built from routines — trigger (cron or event) + prompt + destination — with per-routine autonomy settings and an approval-first default. The ambition: an agent that notices your patterns and proposes routines itself.
- **Web-first interface.** A self-hosted web chat is the primary surface (and the test bed); messaging channels come later.
- **Self-hosted by design.** Single user, reference deployment is Docker Compose on any always-on box. Your agent, your data, your keys.

## Status

The package scaffold is in place. Clone the repo, run `uv sync`, then `uv run kinby --version`.

- [`CONTEXT.md`](CONTEXT.md) — the project's ubiquitous language.

Instance init, manifest parsing, and discovery are in place. The agent loop comes later.

The single-user hub can now prepare, start, and inspect vanilla instances through the typed contract. Run its Docker-backed process with an explicit state directory and source checkout:

```sh
uv run kinby hub .kinby/hub --source .
```

When the hub itself runs in a container, pass `--docker-host-directory` with the same directory's path on the Docker host.

Every start checks what the hub manages. Containers it still has go back to their intended running or stopped state, and the hub prints one line for everything else it found: a container that is gone, an instance whose last operation failed, a container it does not own. It adopts nothing and creates nothing on its own. `instance.recreate` replaces a container when you ask it to, and it is also how a replaced secret reaches one. `instance.secrets.set` writes the value into the instance's protected environment file and says that applying it needs a recreation.

The hub serves its contract at `GET /ws` on the `--listen` address, `0.0.0.0:8080` by default. On its first start it prints an access token and never prints it again, so store it then. A client sends that token as `Authorization: Bearer <token>`. A browser posts it to `POST /auth/login` and gets a session cookie back. `uv run kinby hub .kinby/hub token rotate` replaces the token and ends open sessions. Pass `--web-app <dir>` and the hub serves the built web app from it.

The hub is also the only public way to an instance. `GET /instances/{instance_id}/ws` relays a client to that instance's private contract, and `POST /instances/{instance_id}/signals/{routine}` forwards a webhook to it unchanged. `compose.hub.yaml` is the reference deployment. Caddy terminates TLS in front of the hub. Instances sit on `kinby_private`, and Caddy stays on `kinby_public`. The instance network has a route out, so an instance can call model providers, git, and GitHub.

An instance that already exists can move under the hub. `instance.adopt.preview` reads the instance directory and the container that runs it, then reports the identity, the storage as the Docker host knows it, the selected image, who manages that container now, what the handoff costs, and every finding that would stop it. The preview changes nothing. `instance.adopt` runs those checks again, drains the previous runtime, writes the hub's control token beside the instance, and recreates the container under hub labels on the private network with the same bind locations and named volumes. A runtime too old to drain is interrupted only when you acknowledge that. The manifest id and persona name stay as they are, and `claim_signals` points the public `/signals/{routine}` path at the adopted instance, so a webhook registered before the hub keeps its URL. Take the instance out of Compose and out of the old update cron first. The preview lists those steps, and the hub rewrites neither.

## Run an instance

The reference deployment is Docker Compose: `compose.yaml` runs the instances under `instances/`, one container each. [`docs/container.md`](docs/container.md) has the container contract, the entrypoint, and the webhook setup for a coding instance.

## Validate `kinby.toml` in an editor

Add this Taplo schema directive as the first line of `kinby.toml`:

```toml
#:schema https://raw.githubusercontent.com/jorgesolerrr/kinby/main/docs/schema/kinby.schema.json
```

The schema comes from the manifest model. After the model changes, regenerate the checked-in
schema with `uv run python -m kinby.instance.schema`.

## License

[Apache-2.0](LICENSE)
