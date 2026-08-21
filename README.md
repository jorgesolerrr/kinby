# kinby

**An open-source, self-hosted personal AI teammate.**

*kinby* (an invented word — *kin* + *by*: kin at your side) is a personal AI agent you run on your own hardware. Not a chatbot you visit, but a teammate that remembers, acts on your behalf, and shows up proactively — in the spirit of Lindy, but open, single-user, and yours.

## Thesis

- **Graph-based memory.** Long-term memory is a knowledge graph (Neo4j-backed), not a pile of chat logs — the agent accumulates a durable, queryable model of your world, with a hybrid file layer for preferences and inspection.
- **Routines as a first-class primitive.** Proactive behavior is built from routines — trigger (cron or event) + prompt + destination — with per-routine autonomy settings and an approval-first default. The ambition: an agent that notices your patterns and proposes routines itself.
- **Web-first interface.** A self-hosted web chat is the primary surface (and the test bed); messaging channels come later.
- **Self-hosted by design.** Single user, reference deployment is Docker Compose on any always-on box. Your agent, your data, your keys.

## Status

**Blueprint phase.** This repo currently holds the design effort, not an implementation:

- [`CONTEXT.md`](CONTEXT.md) — the project's ubiquitous language.

The v0.1 build starts once the blueprint is complete.

## License

[Apache-2.0](LICENSE)
