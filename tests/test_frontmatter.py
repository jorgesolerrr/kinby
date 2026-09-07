import pytest

from kinby.frontmatter import (
    FrontmatterError,
    FrontmatterFieldError,
    parse_frontmatter,
    required_string,
)


def test_parse_frontmatter_reads_plain_values_and_body() -> None:
    document = """---
name: planning
description: Plan work before changing files.
---

Planning instructions.
"""

    frontmatter, body = parse_frontmatter(document)

    assert frontmatter == {
        "name": "planning",
        "description": "Plan work before changing files.",
    }
    assert body == "Planning instructions."


def test_parse_frontmatter_reads_bracketed_list_values() -> None:
    document = """---
description: Chose Inspect AI for the eval harness.
subjects: [eval harness, Inspect AI]
---
Decision details.
"""

    frontmatter, _ = parse_frontmatter(document)

    assert frontmatter["subjects"] == ["eval harness", "Inspect AI"]


def test_required_string_rejects_a_non_string_field() -> None:
    with pytest.raises(FrontmatterFieldError, match='must contain a non-empty "name"'):
        required_string({"name": ["planning"]}, "name")


def test_parse_frontmatter_reads_a_string_table() -> None:
    document = """---
description: GitHub issues
signal:
  secret: GITHUB_WEBHOOK_SECRET
  auth: token
---
Handle the delivery.
"""

    frontmatter, body = parse_frontmatter(document)

    assert frontmatter["signal"] == {
        "secret": "GITHUB_WEBHOOK_SECRET",
        "auth": "token",
    }
    assert body == "Handle the delivery."


def test_parse_frontmatter_rejects_indented_fields_outside_a_table() -> None:
    document = """---
description: News
  secret: GITHUB_WEBHOOK_SECRET
---
Body.
"""

    with pytest.raises(FrontmatterError, match="outside a table"):
        parse_frontmatter(document)


def test_parse_frontmatter_rejects_non_string_table_fields() -> None:
    document = """---
signal:
  secret: [GITHUB_WEBHOOK_SECRET]
---
Body.
"""

    with pytest.raises(FrontmatterError, match="must be strings"):
        parse_frontmatter(document)
