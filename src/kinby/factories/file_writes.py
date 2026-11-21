"""Changes to the text of a factory file that keep its comments and every line they leave alone.

Runs and the factory check read the file with PyYAML. Only the hub's writes go through ruamel's
round trip, which keeps comments but lays out what it writes its own way.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeIs

from pydantic import JsonValue
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.emitter import Emitter
from ruamel.yaml.scalarstring import ScalarString


class StepNotDeclared(LookupError):
    """The factory file declares no step by that id."""


def with_step(text: str, entry: Mapping[str, JsonValue]) -> str:
    """The factory file *text* with the step of the entry's id declared as *entry*. Only the
    lines of the values that changed are written again; every other line stays as it was."""
    read = _yaml().load(text)
    at = _place(read, entry.get("id"))
    old = text.splitlines(keepends=True)
    document = _yaml().load(text)
    _merge(document["steps"][at], entry)
    line, column = read["steps"].lc.item(at)
    plain, spaced = (
        _step(_dumped(document, column, old[line].index("-"), spaced=spaced), at)
        for spaced in (False, True)
    )
    step = _lines(read, at, len(old))
    held = _Declared(read["steps"][at], old, _own(old, step, column), column)
    return "".join(old[: held.own.start] + _written(held, plain, spaced) + old[held.own.stop :])


@dataclass(frozen=True)
class _Declared:
    """A value of a factory file, and the lines of the file that declare it, without the comments
    that follow them. Its key, or its step's first key, sits at *column*."""

    value: object
    lines: list[str]
    own: range
    column: int

    def text(self) -> list[str]:
        return self.lines[self.own.start : self.own.stop]

    def text_at(self, column: int) -> list[str]:
        """The lines, moved so the key sits at *column*."""
        return [_shifted(line, column - self.column) for line in self.text()]


def _dumped(document: CommentedMap, column: int, offset: int, *, spaced: bool) -> list[str]:
    yaml = _yaml()
    # Written as the file writes its steps, so the spliced lines line up with the others.
    yaml.indent(mapping=2, sequence=column, offset=offset)
    if spaced:
        yaml.Emitter = _SpacedEmitter
    dumped = io.StringIO()
    yaml.dump(document, dumped)
    return dumped.getvalue().splitlines(keepends=True)


def _step(lines: list[str], at: int) -> _Declared:
    document = _yaml().load("".join(lines))
    column = document["steps"].lc.item(at)[1]
    own = _own(lines, _lines(document, at, len(lines)), column)
    return _Declared(document["steps"][at], lines, own, column)


class _SpacedEmitter(Emitter):
    """Writes a flow mapping with a space inside each brace, as `{ retry: 3 }`."""

    def write_indicator(
        self,
        indicator: str,
        need_whitespace: bool,  # noqa: FBT001 ruamel's signature
        whitespace: bool = False,  # noqa: FBT001, FBT002 ruamel's signature
        indention: bool = False,  # noqa: FBT001, FBT002 ruamel's signature
    ) -> None:
        if indicator.strip() == self.flow_map_start:
            # Not followed by whitespace, so the key after it writes a space first.
            whitespace = False
        elif indicator == self.flow_map_end:
            need_whitespace = True
        super().write_indicator(indicator, need_whitespace, whitespace, indention)


def _written(held: _Declared, plain: _Declared, spaced: _Declared) -> list[str]:
    """The lines that declare *plain*'s value where *held* declares the value the file had:
    *held*'s own lines for each value that stayed. *spaced* is *plain* with spaces inside its
    flow mappings' braces, taken where *held* writes them so."""
    was, now = held.value, plain.value
    if was == now:
        return held.text()
    # Kept key by key only when the first key stays first, as it may share its line with the
    # step's dash.
    if _block(was) and _block(now) and next(iter(was)) == next(iter(now)):
        return _written_keys(held, plain, spaced)
    return (spaced if "{ " in "".join(held.text()) else plain).text_at(held.column)


def _block(value: object) -> TypeIs[CommentedMap]:
    return isinstance(value, CommentedMap) and bool(value) and not value.fa.flow_style()


def _written_keys(held: _Declared, plain: _Declared, spaced: _Declared) -> list[str]:
    """The lines of the block mapping *plain*, with *held*'s comments between its keys. A removed
    key leaves its comments after the key before it."""
    keys, plains, spaceds = _keys(held), _keys(plain), _keys(spaced)
    first = next(iter(keys.values()))[0]
    comments: dict[object, list[str]] = {}
    kept = None
    for key, (_, after) in keys.items():
        kept = key if key in plains else kept
        comments.setdefault(kept, []).extend(held.lines[after.start : after.stop])
    lines = held.lines[held.own.start : first.own.start]
    for key, (value, _) in plains.items():
        if key in keys:
            lines += _written(keys[key][0], value, spaceds[key][0]) + comments.get(key, [])
        else:
            lines += value.text_at(first.column)
    return lines


def _keys(mapping: _Declared) -> dict[object, tuple[_Declared, range]]:
    """Each key of the block mapping *mapping*, with the lines that declare its value and the
    comment lines after them."""
    value = mapping.value
    assert isinstance(value, CommentedMap)
    keys = list(value)
    starts = [value.lc.key(key)[0] for key in keys] + [mapping.own.stop]
    spans = {}
    for key, start, stop in zip(keys, starts, starts[1:], strict=False):
        column = value.lc.key(key)[1]
        own = _own(mapping.lines, range(start, stop), column)
        spans[key] = (_Declared(value[key], mapping.lines, own, column), range(own.stop, stop))
    return spans


def _own(lines: list[str], span: range, column: int) -> range:
    """*span* without the blank and comment lines at its end left of or at *column*, which a value
    declared at *column* can't hold."""
    stop = span.stop
    while stop > span.start + 1 and _trails(lines[stop - 1], column):
        stop -= 1
    return range(span.start, stop)


def _trails(line: str, column: int) -> bool:
    stripped = line.lstrip()
    return not stripped or (stripped.startswith("#") and len(line) - len(stripped) <= column)


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
    yaml = _yaml()
    document = yaml.load(text)
    at = _place(document, after)
    old = text.splitlines(keepends=True)
    lines = _lines(document, at, len(old))
    line, column = document["steps"].lc.item(at)
    end = lines.stop
    while end > lines.start + 1 and _leads(old[end - 1], column):
        end -= 1
    old[end - 1] = old[end - 1].removesuffix("\n") + "\n"
    # Written as the file writes its steps, so the new step lines up with the others.
    yaml.indent(mapping=2, sequence=column, offset=old[line].index("-"))
    dumped = io.StringIO()
    yaml.dump({"steps": [dict(entry)]}, dumped)
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
    last = next(reversed(held), None)
    for key in [key for key in held if key not in entry]:
        del held[key]
    for key, value in entry.items():
        old = held.get(key)
        if isinstance(old, CommentedMap) and isinstance(value, Mapping):
            _merge(old, value)
        elif key not in held:
            # Before the last key, which carries the comments between this entry and the next.
            held.insert(max(len(held) - 1, 0), key, value)
        elif old != value:
            styled = isinstance(old, ScalarString) and isinstance(value, str)
            held[key] = type(old)(value) if styled else value
    if held and last not in held and last in held.ca.items:
        held.ca.items[list(held)[-1]] = held.ca.items.pop(last)
