# Coding standard

How code gets written in kinby. `pyproject.toml` is the source of truth for tooling and versions; this file carries the reasoning the config can't.

## How to approach the code

The maintainer's note in `AGENTS.md` is the standard. Everything below puts it into practice.

- **Constraint first.** Before writing code, name the real constraint the change serves: the behavior a user needs, or the invariant the system must hold. If you can't state it in a sentence, you're not ready to code.
- **Smallest model.** Fight for the design a reader can hold in their head whole. Fewer concepts, fewer states, fewer places a behavior could come from. A reader guessing how the code works should guess right.
- **Obvious over clever.** A boring five-line function beats an elegant abstraction that needs explaining. If code needs a comment to be understood, first try rewriting it so it doesn't.
- **Delete freely.** The fact that complexity already exists does not justify it. When a change touches convoluted code, simplifying it is part of the change, not scope creep.
- **No speculative machinery.** Build for the ticket in front of you. An abstraction earns its place with a second real caller, not an imagined one. No plugin systems, base classes, or config knobs for futures that may never arrive.

## Python

- Target Python 3.10+ (see `pyproject.toml`). Use modern syntax: `X | None`, `match` where it clarifies, dataclasses for structured data instead of loose dicts.
- Type-hint every function signature. Inside bodies, annotate only where inference fails.
- `pathlib.Path` for paths, f-strings for formatting.
- Raise specific exceptions, fail early and loudly. Catch an exception only where you can act on it.
- Modules mirror the domain (`instance/`, `memory/`, `cli/`, `core/`) and stay small. Names come from `CONTEXT.md`.
- Runtime dependencies are a decision, not a convenience. kinby core stays lean; argue each new one in the PR, or in an ADR if it shapes architecture.
- Comments and docstrings explain *why*. Skip any that restate the code.

## Tests

- pytest, in `tests/`, run with `uv run pytest`. Every behavior change lands with a test that fails without it.
- Test through public entry points, the CLI or the package API, not private internals. Tests that survive a refactor are the point.

## Lint and format

Ruff is both linter and formatter. Before any commit:

```sh
uv run ruff check .
uv run ruff format .
```

Rule selection lives in `pyproject.toml` under `[tool.ruff]`. The families and why:

| Family | What it enforces |
|---|---|
| `E`, `W`, `F` | pycodestyle and pyflakes baseline: dead names, undefined variables, style errors |
| `I` | deterministic import order |
| `UP` | pyupgrade, keeps syntax at 3.10+ with no legacy idioms |
| `B` | bugbear, catches real bug patterns like mutable defaults and silent exceptions |
| `SIM` | flags code a smaller construct replaces |
| `C4` | comprehension misuse |
| `RUF` | ruff-native correctness checks |

Fix the code, not the config. A `# noqa` needs a reason on the same line, and per-file ignores are for `tests/` only.
