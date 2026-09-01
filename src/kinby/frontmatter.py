"""Parse the frontmatter and body from a markdown document."""

import json

type FrontmatterValue = str | list[str]


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
    for line in lines[1:closing]:
        key, separator, value = line.partition(":")
        if separator:
            values[key.strip()] = _parse_value(value)
    body = "\n".join(lines[closing + 1 :]).strip("\r\n")
    return values, body
