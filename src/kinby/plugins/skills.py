"""Load skill instructions and expose them to one model turn."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import NewType

from kinby.contracts import Warning
from kinby.frontmatter import (
    FrontmatterError,
    FrontmatterFieldError,
    parse_frontmatter,
    required_string,
)
from kinby.instance import Instance
from kinby.instance.layout import SKILL_FILE, SKILLS_DIR
from kinby.plugins.errors import exception_message
from kinby.plugins.tools import Tool, tool

SkillName = NewType("SkillName", str)
SkillDescription = NewType("SkillDescription", str)
SkillBody = NewType("SkillBody", str)


class SkillFrontmatterError(ValueError):
    """A skill file has missing or invalid frontmatter."""


class SkillNotFoundError(LookupError):
    """A requested skill is not part of the current turn."""


@dataclass(frozen=True)
class Skill:
    name: SkillName
    description: SkillDescription
    source: Path
    body: SkillBody


def load_skills(instance: Instance) -> tuple[tuple[Skill, ...], tuple[Warning, ...]]:
    """Load instance, packaged, then workspace convention skills."""
    instance_skills, instance_warnings = _load_skill_roots((instance.path / SKILLS_DIR,))
    packaged_roots, package_warnings = _packaged_skill_roots(
        defaults=instance.manifest.tools.defaults
    )
    packaged_skills, packaged_warnings = _load_skill_roots(packaged_roots)
    workspace_skills, workspace_warnings = _load_skill_roots(
        instance.manifest.workspace.conventions.skills,
    )
    skills: dict[SkillName, Skill] = {}
    for tier in (instance_skills, packaged_skills, workspace_skills):
        for name, skill in tier.items():
            skills.setdefault(name, skill)
    return tuple(skills.values()), (
        *instance_warnings,
        *package_warnings,
        *packaged_warnings,
        *workspace_warnings,
    )


def _packaged_skill_roots(*, defaults: bool) -> tuple[tuple[Path, ...], tuple[Warning, ...]]:
    roots: list[Path] = []
    warnings: list[Warning] = []
    for entry_point in entry_points(group="kinby.skills"):
        if not defaults and entry_point.name == "defaults":
            distribution = entry_point.dist
            if distribution is not None and distribution.name == "kinby":
                continue
        try:
            root = entry_point.load()
            if not isinstance(root, Path) or not root.is_dir():
                raise TypeError(
                    f'Entry point "{entry_point.value}" does not export a skill directory Path.'
                )
            roots.append(root)
        except Exception as exc:
            warnings.append(Warning(sources=(entry_point.value,), message=exception_message(exc)))
    return tuple(roots), tuple(warnings)


def _load_skill_roots(
    roots: Sequence[Path],
) -> tuple[dict[SkillName, Skill], tuple[Warning, ...]]:
    loaded: dict[SkillName, Skill] = {}
    warnings: list[Warning] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob(f"*/{SKILL_FILE}"), key=lambda item: item.parent.name):
            try:
                candidate = _load_skill(path)
            except (SkillFrontmatterError, OSError, UnicodeDecodeError) as exc:
                warnings.append(Warning(sources=(str(path),), message=str(exc)))
                continue
            existing = loaded.get(candidate.name)
            if existing is not None:
                warnings.append(
                    Warning(
                        sources=(str(existing.source), str(candidate.source)),
                        message=f'Skill "{candidate.name}" is declared by both sources.',
                    )
                )
                continue
            loaded[candidate.name] = candidate
    return loaded, tuple(warnings)


def _load_skill(path: Path) -> Skill:
    """Read unquoted `key: value` frontmatter and the body from one skill file."""
    try:
        values, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except FrontmatterError as exc:
        raise SkillFrontmatterError("Skill frontmatter is missing.") from exc
    try:
        name = required_string(values, "name")
        description = required_string(values, "description")
    except FrontmatterFieldError as exc:
        raise SkillFrontmatterError(f'Skill frontmatter must contain "{exc.key}".') from exc
    return Skill(
        name=SkillName(name),
        description=SkillDescription(description),
        source=path,
        body=SkillBody(body),
    )


def skill_tool(skills: Sequence[Skill]) -> Tool:
    """Build the core tool that reads this turn's skill set."""
    by_name = {skill.name: skill for skill in skills}

    @tool(write=False)
    def skill(name: SkillName) -> SkillBody:
        """Read a skill's full instructions by name."""
        selected = by_name.get(name)
        if selected is None:
            raise SkillNotFoundError(f'Skill "{name}" is not available in this turn.')
        return selected.body

    return skill
