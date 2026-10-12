"""Parse the frontmatter and body from a markdown document."""

import json
from textwrap import dedent

type FrontmatterValue = str | list[str] | dict[str, str]


class FrontmatterError(ValueError):
    """A markdown document has invalid frontmatter."""


class FrontmatterFieldError(FrontmatterError):
    """A required frontmatter field is missing or has the wrong type."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f'Frontmatter must contain a non-empty "{key}" string.')


def required_string(values: dict[str, FrontmatterValue], key: str) -> str:
    """Read a required, non-empty string from parsed frontmatter."""
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise FrontmatterFieldError(key)
    return value


def _parse_value(value: str) -> FrontmatterValue:
    value = value.strip()
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        decoded = None
    if isinstance(decoded, str):
        return decoded
    if isinstance(decoded, list) and all(isinstance(item, str) for item in decoded):
        return decoded
    if value.startswith("[") and value.endswith("]"):
        return [item.strip() for item in value[1:-1].split(",") if item.strip()]
    return value


def render_frontmatter_value(value: str | tuple[str, ...]) -> str:
    """Render a string or string list without changing the document structure."""
    return json.dumps(value, ensure_ascii=False)


def _joined_block_values(lines: list[str]) -> list[str]:
    """Each `key: >` or `key: |` field and its indented lines, as one `key: "text"` line.

    A folded value joins its lines with spaces and its paragraphs with newlines. A literal value
    keeps its lines. Neither keeps a trailing newline.
    """
    joined: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        key, _, value = line.partition(":")
        style = value.strip().rstrip("+-")
        if line[:1].isspace() or style not in (">", "|"):
            joined.append(line)
            continue
        block: list[str] = []
        while index < len(lines) and (not lines[index].strip() or lines[index][:1].isspace()):
            block.append(lines[index])
            index += 1
        text = dedent("\n".join(block)).strip("\n")
        if style == ">":
            text = "\n".join(" ".join(part.split("\n")) for part in text.split("\n\n"))
        joined.append(f"{key}: {json.dumps(text)}")
    return joined


def parse_frontmatter(document: str) -> tuple[dict[str, FrontmatterValue], str]:
    """Parse `key: value` frontmatter and return it with the markdown body."""
    lines = document.splitlines()
    if not lines or lines[0] != "---":
        raise FrontmatterError("Frontmatter is missing.")
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise FrontmatterError("Frontmatter is missing.") from exc
    values: dict[str, FrontmatterValue] = {}
    table: dict[str, str] | None = None
    for line in _joined_block_values(lines[1:closing]):
        stripped = line.lstrip()
        indented = bool(stripped) and line[: len(line) - len(stripped)] != ""
        key, separator, value = stripped.partition(":")
        if not separator:
            continue
        parsed = _parse_value(value)
        if indented:
            if table is None:
                raise FrontmatterError("Indented field is outside a table.")
            if not isinstance(parsed, str):
                raise FrontmatterError("Table fields must be strings.")
            table[key.strip()] = parsed
            continue
        if parsed == "":
            table = {}
            values[key.strip()] = table
        else:
            table = None
            values[key.strip()] = parsed
    body = "\n".join(lines[closing + 1 :]).strip("\r\n")
    return values, body
