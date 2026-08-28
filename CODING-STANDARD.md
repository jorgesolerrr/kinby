# Coding standard

How code gets written in kinby. `pyproject.toml` is the source of truth for tooling and versions; this file carries the reasoning the config can't.

## How to approach the code

The maintainer's note in `AGENTS.md` is the standard. Everything below puts it into practice.

- **Constraint first.** Before writing code, name the real constraint the change serves: the behavior a user needs, or the invariant the system must hold. If you can't state it in a sentence, you're not ready to code.
- **Smallest model.** A reader guessing how the code works should guess right. Fewer concepts, fewer states, fewer places a behavior could come from.
- **Book.** Code reads like a book. Variable, type, and function names carry what a reader needs. A comment earns its place only for a *why* the names cannot.
- **Obvious over clever.** A boring five-line function beats an elegant abstraction that needs explaining.
- **Delete freely.** The fact that complexity already exists does not justify it. When a change touches convoluted code, simplifying it is part of the change, not scope creep.
- **No speculative machinery.** Build for the ticket in front of you. An abstraction earns its place with a second real caller, not an imagined one. No plugin systems, base classes, or config knobs for futures that may never arrive.
- **Once.** One meaning lives in one place. A second copy is that second caller — extract then.
- **Single responsibility.** One reason to change per function or module.
- **Open-closed.** New behavior arrives as a new path, leaving the existing one intact.
- **Decouple at seams.** Independent change should not force a rewrite. The seam is an interface; when one is earned and which kind is decided in [Interfaces](#interfaces) below.

## Python

- **PEP 8.** Follow [PEP 8](https://peps.python.org/pep-0008/) for Python style. This file and `pyproject.toml` win on conflict; ruff enforces the mechanical subset.
- **Pythonic, modern.** Target Python 3.14+ (see `pyproject.toml`). Use `X | None`, `match` where it clarifies, and dataclasses for structured data.
- Type-hint every function signature and every public value. Inside bodies, annotate only where inference fails. Name the type; `Any` only when a real type would lie or block the design.
- **Functions first.** Stateless behavior is a function. A class earns its place by holding state.
- `pathlib.Path` for paths, f-strings for formatting.
- Raise specific exceptions, fail early and loudly. Catch an exception only where you can act on it.
- Modules mirror the domain (`instance/`, `memory/`, `cli/`, `core/`) and stay small. Names come from `CONTEXT.md`.
- Runtime dependencies are a decision, not a convenience. Prefer the stdlib. kinby core stays lean; argue each new one in the PR, or in an ADR if it shapes architecture.

## Interfaces

An interface exists to decouple. The layer above depends on a behavior and knows nothing about how it is implemented. Whether the behavior arrives as a `Callable`, a `Protocol`, or an ABC, the caller only needs an object that acts the way the interface says.

Interfaces are a big-picture decision. Ask these about the software as a whole, not about the ticket in front of you:

- Does another component need this behavior?
- Is this behavior a contract a concrete implementation must honor?
- Will there be more than one implementation?

Convinced yeses earn an interface. Hesitant ones mean the concrete type is the interface for now: call it directly and revisit when the shape of the system changes. The ticket rarely justifies a seam on its own; the shape of the software does.

Once an interface is earned, pick the kind by its shape:

- **Callable.** One method, and that method will hold its signature for a while. Implementations are plain functions, so adding or removing one never touches the layer above. Describe the signature with `Callable`, or a callback `Protocol` when the parameters need names.
- **Protocol.** Duck typing for more than one method. No inheritance; an object satisfies it by having the methods. Use it when the same behavior is expected from more than one object.
- **ABC.** Inheritance. Use it when the base carries state, a class-level parameter, or shared implementation the subclasses actually reuse. A base every subclass overrides in full is a Protocol wearing inheritance.

## Tests

- pytest, in `tests/`, run with `uv run pytest`. Every behavior change lands with a test that fails without it.
- Test through public entry points, the CLI or the package API, not private internals. Tests that survive a refactor are the point.

## Type checking

ty checks the whole project with the Python lower bound from `requires-python`. Run it before every commit:

```sh
uv run ty check
```

Fix the code or its annotations when ty reports an error. Put deliberate rule-level changes in `pyproject.toml` and explain why in review.

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
| `UP` | pyupgrade, keeps syntax at 3.14+ with no legacy idioms |
| `B` | bugbear, catches real bug patterns like mutable defaults and silent exceptions |
| `SIM` | flags code a smaller construct replaces |
| `C4` | comprehension misuse |
| `RUF` | ruff-native correctness checks |

Fix the code, not the config. A `# noqa` needs a reason on the same line, and per-file ignores are for `tests/` only.
