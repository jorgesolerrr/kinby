# kinby

An open-source, self-hosted personal AI teammate. *kinby*  is a teammate that remembers, acts on your behalf, and shows up proactively. Open, single-user.

Thesis and status live in `README.md`. Vocabulary lives in `CONTEXT.md`, the project's ubiquitous language. Name things with its terms. When a new concept needs a name, add it there first.

## Jorge's note

I'm a passionate programmer who likes complex things done in a simple way. That doesn't mean I like mess and lazy code. That means you don't need to overengineer everything, or add machinery just because it's nice or impressive. The idea always is: understand the requirements, and construct the optimal approach using our coding standards.

## How to work here

- **Code.** Lean and pythonic: approach, book, PEP 8, Protocol, Callable, tests, type checking, and lint live in `CODING-STANDARD.md`. Checks pass before any commit: `uv run ruff check .`, `uv run ruff format .`, `uv run ty check`, `uv run pytest`.
- **User-facing communication.** Run the `unslop` skill (`/unslop`) over anything the user reads: replies, PR descriptions, issue comments, README and doc prose. Plain and specific.
- **Architecture decisions.** Record them as ADRs in `docs/adr/`, one decision per file.

## Agent skills

### Issue tracker

Issues are GitHub Issues on `jorgesolerrr/kinby`, operated via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` at the repo root plus ADRs in `docs/adr/`. See `docs/agents/domain.md`.
