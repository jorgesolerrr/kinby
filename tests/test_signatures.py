"""Signatures name the domain (CODING-STANDARD.md, Types). Lint bans the imports; this
checks the annotations themselves, including modules allowed to import the generic names."""

import ast
from collections.abc import Iterator
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "kinby"
GENERIC_NAMES = {"BaseModel", "Any"}
# Functions that read raw input or route any model. Keep this list short.
ALLOWED = {
    "contracts/models.py",
    "core/dispatcher.py",
    "instance/manifest.py",
}


def _functions(path: Path) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node


def _annotation_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[str]:
    annotations = [arg.annotation for arg in ast.walk(function.args) if isinstance(arg, ast.arg)]
    annotations.append(function.returns)
    for annotation in annotations:
        if annotation is None:
            continue
        yield from (node.id for node in ast.walk(annotation) if isinstance(node, ast.Name))


def test_signatures_name_the_domain() -> None:
    offenders = [
        f"{path.relative_to(SRC).as_posix()}:{function.lineno} {function.name}: {name}"
        for path in sorted(SRC.rglob("*.py"))
        if path.relative_to(SRC).as_posix() not in ALLOWED
        for function in _functions(path)
        for name in _annotation_names(function)
        if name in GENERIC_NAMES
    ]
    assert offenders == []
