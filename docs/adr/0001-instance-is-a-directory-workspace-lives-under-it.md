# An instance is one directory; the workspace lives under it

A kinby instance is a single self-contained directory (manifest `kinby.toml`, behavior files, memory, and a gitignored `.state/` for runtime data) that is also the one volume a container mounts. The user's repo or notes folder — the workspace — sits *under* the instance (`workspace/`, cloned or linked there), never the reverse: kinby does not drop a `.kinby/` directory into a repo. A repo-scoped coding agent is therefore an ordinary instance whose workspace is that repo, optionally reading the repo's own `AGENTS.md` / `.agents/skills` as extra behavior sources, with the instance winning on conflict.

## Considered options

- **`.kinby/` inside the repo, Claude Code style.** Rejected: it would fragment long-lived memory per project, put a checkpointer DB and vector index inside the user's repo, and give kinby two instance shapes to maintain.
- **Runtime state outside the instance (`~/.local/share/kinby/…`).** Rejected as the default because it breaks "tar the directory and you have moved the instance" and would need a second container mount. Available as a manifest override (`state_dir`).

## Consequences

- Discovery is a walk up from cwd for `kinby.toml`; running from inside `<instance>/workspace/…` finds the instance. A symlinked workspace is not found by walking and needs `KINBY_INSTANCE` — no global registry.
- Secrets are environment only; the manifest is committable.
