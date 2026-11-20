"""Changes to the text of a factory file that keep its comments and every line they leave alone.

Runs and the factory check read the file with PyYAML. Only the hub's writes go through ruamel's
round trip, which keeps comments but lays out what it writes its own way.
"""

from __future__ import annotations

import io
from collections.abc import Mapping

from pydantic import JsonValue
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import ScalarString


class StepNotDeclared(LookupError):
    """The factory file declares no step by that id."""


def with_step(text: str, entry: Mapping[str, JsonValue]) -> str:
    """The factory file *text* with the step of the entry's id declared as *entry*. Each line
    outside that step stays as it was."""
    document = _yaml().load(text)
    at = _place(document, entry.get("id"))
    old = text.splitlines(keepends=True)
    before = _lines(document, at, len(old))
    _merge(document["steps"][at], entry)
    dumped = io.StringIO()
    _yaml().dump(document, dumped)
    new = dumped.getvalue().splitlines(keepends=True)
    after = _lines(_yaml().load(dumped.getvalue()), at, len(new))
    return "".join(old[: before.start] + new[after.start : after.stop] + old[before.stop :])


def with_step_from(text: str, other: str, step_id: str) -> str:
    """The factory file *text* with the step *step_id* declared in the lines the factory file
    *other* declares it in, moved to the column *text* writes its steps at. Each line outside that
    step stays as it was."""
    document, source = _yaml().load(text), _yaml().load(other)
    old, new = text.splitlines(keepends=True), other.splitlines(keepends=True)
    here = _lines(document, _place(document, step_id), len(old))
    there = _lines(source, _place(source, step_id), len(new))
    shift = old[here.start].index("-") - new[there.start].index("-")
    moved = [_shifted(line, shift) for line in new[there.start : there.stop]]
    return "".join(old[: here.start] + moved + old[here.stop :])


def _shifted(line: str, columns: int) -> str:
    """*line* moved *columns* to the right, or left when negative. A blank line stays blank."""
    if not line.strip():
        return line
    if columns >= 0:
        return " " * columns + line
    return line[min(-columns, len(line) - len(line.lstrip(" "))) :]


def with_step_off(text: str, step_id: str, *, off: bool) -> str:
    """The factory file *text* with the step *step_id* off, or on, which a step is unless it says
    otherwise. Each line outside that step stays as it was."""
    document = _yaml().load(text)
    entry = dict(document["steps"][_place(document, step_id)])
    entry.pop("off", None)
    return with_step(text, entry | {"off": True} if off else entry)


def with_step_after(text: str, after: str, entry: Mapping[str, JsonValue]) -> str:
    """The factory file *text* with *entry* declared right after the step *after*, before any
    comment that leads the next step. Each line of the file stays as it was."""
    document = _yaml().load(text)
    at = _place(document, after)
    old = text.splitlines(keepends=True)
    lines = _lines(document, at, len(old))
    column = document["steps"][at].lc.col
    end = lines.stop
    while end > lines.start + 1 and _leads(old[end - 1], column):
        end -= 1
    old[end - 1] = old[end - 1].removesuffix("\n") + "\n"
    dumped = io.StringIO()
    _yaml().dump({"steps": [dict(entry)]}, dumped)
    new = dumped.getvalue().splitlines(keepends=True)[1:]
    return "".join(old[:end] + new + old[end:])


def _leads(line: str, column: int) -> bool:
    """Whether *line* is blank or a comment left of a step's keys, at *column*, so it leads what
    follows."""
    stripped = line.lstrip()
    return not stripped.strip() or (stripped.startswith("#") and len(line) - len(stripped) < column)


def without_step(text: str, step_id: str) -> str:
    """The factory file *text* without the step *step_id*, and the comments that follow it."""
    document = _yaml().load(text)
    old = text.splitlines(keepends=True)
    gone = _lines(document, _place(document, step_id), len(old))
    return "".join(old[: gone.start] + old[gone.stop :])


def _yaml() -> YAML:
    yaml = YAML()
    yaml.indent(mapping=2, sequence=4, offset=2)
    # A narrower width folds again the descriptions the file already folds at 100 columns.
    yaml.width = 100
    yaml.preserve_quotes = True
    return yaml


def _place(document: CommentedMap, step_id: JsonValue) -> int:
    for at, step in enumerate(document["steps"]):
        if step["id"] == step_id:
            return at
    raise StepNotDeclared(f'The factory declares no step "{step_id}".')


def _lines(document: CommentedMap, at: int, count: int) -> range:
    """The lines of the file *document* was read from that declare the step at *at*, with the
    comments that follow it. The file has *count* lines."""
    steps = document["steps"]
    start = steps.lc.item(at)[0]
    if at + 1 < len(steps):
        return range(start, steps.lc.item(at + 1)[0])
    keys = list(document)
    later = keys[keys.index("steps") + 1 :]
    return range(start, document.lc.key(later[0])[0] if later else count)


def _merge(held: CommentedMap, entry: Mapping[str, JsonValue]) -> None:
    """Make *held* declare *entry*, leaving each value it already declares as it was written."""
    last = list(held)[-1]
    for key in [key for key in held if key not in entry]:
        del held[key]
    for key, value in entry.items():
        old = held.get(key)
        if isinstance(old, CommentedMap) and isinstance(value, Mapping):
            _merge(old, value)
        elif key not in held:
            # Before the last key, which carries the comments between this entry and the next.
            held.insert(len(held) - 1, key, value)
        elif old != value:
            styled = isinstance(old, ScalarString) and isinstance(value, str)
            held[key] = type(old)(value) if styled else value
    if last not in held and last in held.ca.items:
        held.ca.items[list(held)[-1]] = held.ca.items.pop(last)
