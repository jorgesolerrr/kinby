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
    yaml = _yaml()
    document = yaml.load(text)
    at = _place(document, entry.get("id"))
    old = text.splitlines(keepends=True)
    before = _lines(document, at, len(old))
    line, column = document["steps"].lc.item(at)
    # Written as the file writes its steps, so the spliced step lines up with the others.
    yaml.indent(mapping=2, sequence=column, offset=old[line].index("-"))
    _merge(document["steps"][at], entry)
    dumped = io.StringIO()
    yaml.dump(document, dumped)
    new = dumped.getvalue().splitlines(keepends=True)
    after = _lines(yaml.load(dumped.getvalue()), at, len(new))
    return "".join(old[: before.start] + new[after.start : after.stop] + old[before.stop :])


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
