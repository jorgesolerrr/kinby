# Research: enforcing the Types rules with lint

Date: 2026-08-28. Status: steps 1 to 5 landed together (PR after #38); the allow list shrank to two files because the dispatcher no longer names `BaseModel`. Question: which of the [Types](../../CODING-STANDARD.md#types) rules can a tool enforce, so an agent cannot land a `-> BaseModel` or a stray `Any` without a red check?

## What ruff already offers

| Rule | Enforces | Rule it serves |
|---|---|---|
| `ANN001-003`, `ANN2xx` | every argument and return is annotated | the hinting policy under Python |
| `ANN401` | no `Any` on arguments | parse at the boundary (arguments only; returns are not covered) |
| `TID251` banned-api | a named import or attribute is forbidden, with a message | `typing.Any` and `pydantic.BaseModel` outside boundary modules |
| `RUF013` | implicit `Optional` (`x: str = None`) | absence lives in the type |
| `FBT001-003` | no positional booleans | name the domain (a bare `bool` argument is a mode flag) |
| `SIM101` | merged `isinstance` calls | weak; catches the symptom, not the cause |

`TID251` is the one that carries the project-specific rules. It takes `per-file-ignores`, so the ban applies everywhere except the modules allowed to see raw shapes:

```toml
[tool.ruff.lint]
select = ["E", "W", "F", "I", "UP", "B", "SIM", "C4", "RUF", "ANN", "FBT", "TID"]

[tool.ruff.lint.flake8-tidy-imports.banned-api]
"typing.Any".msg = "Parse at the boundary. Any lives only in the module that reads raw input."
"pydantic.BaseModel".msg = "Annotate the specific contract model. BaseModel is for code that routes any model."

[tool.ruff.lint.per-file-ignores]
"src/kinby/contracts/models.py" = ["TID251"]   # defines ContractModel
"src/kinby/instance/manifest.py" = ["TID251"]  # reads TOML
"tests/**" = ["ANN", "FBT", "TID251"]
```

Trade-off: `TID251` bans the *name*, not the annotation. A module on the allow list can still return `BaseModel` from a function that knows better (`create_thread -> BaseModel` in the dispatcher). Two ways to close that: keep the allow list to modules that only define or route, and put the specific handlers in their own module; or add the AST test below.

## What ty offers

ty has no "strict" preset and no rule against `Any` in annotations. It enforces the consequences: `invalid-return-type`, `unsound-return-statement`, `invalid-declaration` are `error` by default. Two rules worth turning on so suppressions stay honest:

```toml
[tool.ty.rules]
blanket-ignore-comment = "error"   # `# ty: ignore` must name a rule
possibly-unresolved-reference = "error"
```

`unused-ignore-comment` is already `error`.

## What no tool checks: an architecture test

Rules like "no `Mapping[str, Any]` past the parsing function" and "no `-> BaseModel` outside routing code" are about *where* a type appears, which is a project fact. The lean way to enforce a project fact is a test in `tests/` that walks `src/` with `ast` and asserts it. Roughly 30 lines:

```python
BANNED_ANNOTATIONS = {"BaseModel", "Any"}
ALLOWED = {"contracts/models.py", "core/dispatcher.py", "instance/manifest.py"}


def test_signatures_name_the_domain() -> None:
    offenders = [
        f"{path}:{node.lineno}"
        for path, node in _function_defs(SRC)
        if str(path.relative_to(SRC)) not in ALLOWED
        for name in _annotation_names(node)
        if name in BANNED_ANNOTATIONS
    ]
    assert offenders == []
```

It runs under `uv run pytest`, which is already in the commit gate, and the allow list is the same set as the `per-file-ignores`. Semgrep or a ruff plugin would do the same with more machinery; skip them until the AST test proves insufficient.

## Proposed order

1. Add `TID251` with the two bans and the allow list. Fix what it finds (the `-> BaseModel` handlers, the `Any` helpers in `manifest.py`). This is the highest-value rule and the smallest diff.
2. Add `ANN` and `RUF013`. `ANN` will be nearly clean given the existing hinting policy.
3. Add `FBT`. Expect a handful of `bool` parameters to become keyword-only.
4. Add the AST test once step 1 has shrunk the allow list, so it stays short.
5. ty rules as above.

Each step is its own PR with the fixes it forces, so a reviewer sees the rule and the code it changed together.

## Sources

- ruff `any-type` (ANN401): https://docs.astral.sh/ruff/rules/any-type/
- ruff `banned-api` (TID251): https://docs.astral.sh/ruff/rules/banned-api/
- ruff `boolean-type-hint-positional-argument` (FBT001): https://docs.astral.sh/ruff/rules/boolean-type-hint-positional-argument/
- ruff rules index: https://docs.astral.sh/ruff/rules/
- ty rules reference: https://docs.astral.sh/ty/reference/rules/
- ty configuration: https://docs.astral.sh/ty/reference/configuration/
