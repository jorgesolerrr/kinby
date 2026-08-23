# kinby

An open-source, self-hosted personal AI teammate. *kinby*  is a teammate that remembers, acts on your behalf, and shows up proactively. Open, single-user.

Thesis and status live in `README.md`. Vocabulary lives in `CONTEXT.md`, the project's ubiquitous language. Name things with its terms. When a new concept needs a name, add it there first.

## Maintainer's note

> I like ambitious ideas, simple systems, and software that feels obvious. Do not preserve complexity just because it already exists. Do not introduce machinery because it looks architecturally impressive. Understand the real constraint, then fight for the smallest model that makes the correct behavior unsurprising.

This governs every decision in this repo: design, code, docs, scope. When two approaches work, pick the one with the smaller model.

## How to work here

- **Code.** Follow `CODING-STANDARD.md`. Lint and tests pass before any commit: `uv run ruff check .`, `uv run ruff format .`, `uv run pytest`.
- **User-facing communication.** Run the `unslop` skill (`/unslop`) over anything the user reads: replies, PR descriptions, issue comments, README and doc prose. Plain and specific.
- **Architecture decisions.** Record them as ADRs in `docs/adr/`, one decision per file.

## Agent skills

### Issue tracker

Issues are GitHub Issues on `jorgesolerrr/kinby`, operated via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` at the repo root plus ADRs in `docs/adr/`. See `docs/agents/domain.md`.
