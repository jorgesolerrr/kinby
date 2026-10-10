"""Coding clients find the process skills in any repository checkout."""

import hashlib
import json
import re
import string
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).parents[1]
SKILLS = REPOSITORY / ".claude" / "skills"
LOCK = json.loads((REPOSITORY / "skills-lock.json").read_text(encoding="utf-8"))["skills"]
#: The software factory's coder carries registry skills too, locked apart from the repository's.
CODER_SKILLS = REPOSITORY / "src/kinby/factories/shipped/software/instances/coder/skills"
CODER_LOCK = json.loads((REPOSITORY / "skills-lock.coder.json").read_text(encoding="utf-8"))[
    "skills"
]
REGISTRY = "jorgesolerrr/skills"
# The order JavaScript's localeCompare puts these characters in. The `skills` CLI sorts a
# skill's files with it before hashing them into `computedHash`.
COLLATION = " _-,;:!?.'\"()[]{}@*/\\&#%`^+<=>|~$" + string.digits + string.ascii_lowercase


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


def _collation_key(path: str) -> tuple[list[int], list[bool]]:
    return [COLLATION.index(char.lower()) for char in path], [char.isupper() for char in path]


def _folder_hash(folder: Path) -> str:
    """Hash a skill folder as the `skills` CLI does: each file's relative path, then its bytes."""
    files = (path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file())
    digest = hashlib.sha256()
    for file in sorted(files, key=_collation_key):
        digest.update(file.encode())
        digest.update((folder / file).read_bytes())
    return digest.hexdigest()


def test_every_vendored_skill_is_locked_except_the_repository_own_skills() -> None:
    vendored = {folder.name for folder in SKILLS.iterdir() if folder.is_dir()}

    assert vendored - LOCK.keys() == {"open-pr", "e2e-pass"}


def test_every_coder_skill_is_locked_except_the_factorys_own_implement_ticket() -> None:
    vendored = {folder.name for folder in CODER_SKILLS.iterdir() if folder.is_dir()}

    assert vendored - CODER_LOCK.keys() == {"implement-ticket"}


@pytest.mark.parametrize(
    ("skills", "name", "locked"),
    [
        (folder, name, entry["computedHash"])
        for folder, lock in ((SKILLS, LOCK), (CODER_SKILLS, CODER_LOCK))
        for name, entry in lock.items()
        if entry["source"] == REGISTRY
    ],
)
def test_vendored_registry_skill_matches_its_locked_hash(
    skills: Path, name: str, locked: str
) -> None:
    assert _folder_hash(skills / name) == locked, (
        f"{name} was edited in place. Edit it in {REGISTRY}, then run `bun run skills:sync`."
    )
