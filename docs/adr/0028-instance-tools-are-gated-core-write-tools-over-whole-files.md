# Instance tools are gated core write tools over whole files

Ticket #61 asked how the agent changes its own instance, given that the workspace file tools refuse every path outside the workspace and `auto` only auto-allows workspace writes (ADR 0014). kinby adds instance tools: core write tools built per instance the way the memory tools are, present on every turn, gated by the same mode presets and `permissions.toml` overrides as any other write. They take whole files. `routine_write` takes the complete `ROUTINE.md` text and an optional `run.py`, and `skill_write` takes the complete `SKILL.md`, so the file stays the one format the write-routine skill teaches and every frontmatter key stays a file matter, not a tool parameter.

## Considered options

- **Contract methods, like `routine.run`.** Rejected: the model has no path to the contract, and the conversation is where a user asks for a routine. Clients can get routine methods later without touching the model path.
- **Structured verbs, `routine_create(description, schedule, prompt, ...)`.** Rejected: every new frontmatter key becomes a new tool parameter, and kinby would serialize a file it also parses.
- **A stricter tier than ordinary writes, asking even in `full-access`.** Rejected: the presets already encode the user's trust, and a user who runs `full-access` chose silent writes. The `tools.<name>` override remains the knob.
- **Agent-authored tools in the same family.** Deferred to its own ticket: a tool's write flag is self-declared, so letting the model write `tools/*.py` is a gate escalation by construction and deserves its own answer.
- **A `set_model` verb rewriting `kinby.toml`.** Ruled out of the v1 map: the manifest is the user's, the `--model` flag exists, and kinby has no style-preserving TOML writer.

## Consequences

- Validation runs the same loaders the scheduler and the skills catalogue use, against a staged copy, before any file is replaced. A failing validation returns the loader's message as the tool result and touches nothing. The replacement is an atomic rename, so the scheduler, which re-reads routine files every tick, never sees a half-written routine.
- `routine_set_enabled` flips the one frontmatter line the failure policy already writes (ADR 0022). An approved enable is the explicit act that ADR left open: the scheduler folds it from the event log as the start of a fresh failure streak.
- `routine_delete` refuses while deliveries are pending for that routine, so no delivery fires against a routine that no longer exists.
- Instance tools stay in the tool snapshot on routine and signal wakes. The firing cue and the routine's own `mode` govern them. There is no second opt-in.
- The instance directory has no snapshot history. The `tool.call` event holds the new content; a user who wants before-images keeps the instance directory in git.
