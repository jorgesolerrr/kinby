from kinby.frontmatter import parse_frontmatter


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
