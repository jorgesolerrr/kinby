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

## Validate `kinby.toml` in an editor

Add this Taplo schema directive as the first line of `kinby.toml`:

```toml
#:schema https://raw.githubusercontent.com/jorgesolerrr/kinby/main/docs/schema/kinby.schema.json
```

The schema comes from the manifest model. After the model changes, regenerate the checked-in
schema with `uv run python -m kinby.instance.schema`.

## License

[Apache-2.0](LICENSE)
