"""Coding clients find the process skills in any repository checkout."""

import re
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).parents[1]
SKILLS = REPOSITORY / ".claude" / "skills"


@pytest.mark.parametrize(
    ("name", "references"),
    [
        ("implement-ticket", ()),
        ("tdd", ("tests.md", "mocking.md")),
        ("adversarial-review", ("references/smells.md",)),
        ("open-pr", ()),
        ("unslop", ()),
        ("domain-modeling", ("CONTEXT-FORMAT.md", "ADR-FORMAT.md")),
        ("writing-for-agents", ("SKILL-MECHANICS.md",)),
    ],
)
def test_repository_carries_process_skill_and_references(
    name: str, references: tuple[str, ...]
) -> None:
    for filename in ("SKILL.md", *references):
        assert (SKILLS / name / filename).is_file()


def test_agents_slash_commands_resolve_to_repository_skills() -> None:
    instructions = (REPOSITORY / "AGENTS.md").read_text(encoding="utf-8")
    commands = re.findall(r"(?<![\w./])/([a-z][a-z0-9-]*)\b", instructions)

    assert commands
    for command in commands:
        assert (SKILLS / command / "SKILL.md").is_file()
