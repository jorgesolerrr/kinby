import pytest

from kinby.frontmatter import FrontmatterFieldError, parse_frontmatter, required_string


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
