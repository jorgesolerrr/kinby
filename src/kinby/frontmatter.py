"""Parse the frontmatter and body from a markdown document."""

type FrontmatterValue = str | list[str]


class FrontmatterError(ValueError):
    """A markdown document has no complete frontmatter block."""


def _parse_value(value: str) -> FrontmatterValue:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        return [item.strip() for item in value[1:-1].split(",") if item.strip()]
    return value


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
