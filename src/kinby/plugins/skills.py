"""Load skill instructions and expose them to one model turn."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NewType

from kinby.contracts import SkillName, SkillTier, Warning
from kinby.frontmatter import (
    FrontmatterError,
    FrontmatterFieldError,
    parse_frontmatter,
    required_string,
)
from kinby.instance import Instance
from kinby.instance.layout import SKILL_FILE, SKILLS_DIR
from kinby.plugins.tools import Tool, tool

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


@dataclass(frozen=True)
class TieredSkill:
    """A skill and the tier it comes from."""

    skill: Skill
    tier: SkillTier


def load_skills(instance: Instance) -> tuple[tuple[Skill, ...], tuple[Warning, ...]]:
    """Load the skill the model reads for each name: instance, kinby, then workspace."""
    tiered, warnings = load_skill_tiers(instance)
    skills: dict[SkillName, Skill] = {}
    for each in tiered:
        skills.setdefault(each.skill.name, each.skill)
    return tuple(skills.values()), warnings


def load_skill_tiers(instance: Instance) -> tuple[tuple[TieredSkill, ...], tuple[Warning, ...]]:
    """Load every skill of every tier, shadowed ones included, highest tier first."""
    # The defaults import kinby.plugins to declare their tools, so they load on first use.
    from kinby.plugins import defaults

    roots = (
        (SkillTier.INSTANCE, (instance.path / SKILLS_DIR,)),
        (SkillTier.KINBY, (defaults.SKILLS,) if instance.manifest.tools.defaults else ()),
        (SkillTier.WORKSPACE, instance.manifest.workspace.conventions.skills),
    )
    tiered: list[TieredSkill] = []
    warnings: list[Warning] = []
    for tier, tier_roots in roots:
        skills, tier_warnings = _load_skill_roots(tier_roots)
        tiered.extend(TieredSkill(skill, tier) for skill in skills.values())
        warnings.extend(tier_warnings)
    return tuple(tiered), tuple(warnings)


def lower_tier_skill(instance: Instance, name: SkillName) -> TieredSkill | None:
    """The kinby or workspace skill by *name* that an instance skill hides, or would."""
    tiered, _ = load_skill_tiers(instance)
    return next(
        (
            each
            for each in tiered
            if each.skill.name == name and each.tier is not SkillTier.INSTANCE
        ),
        None,
    )


def describe_skill_shadow(instance: Instance, name: SkillName) -> str | None:
    """Describe the kinby or workspace skill hidden by an instance skill."""
    hidden = lower_tier_skill(instance, name)
    if hidden is None:
        return None
    return f"{hidden.tier} skill at {hidden.skill.source}"


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
                candidate = load_skill_file(path)
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


def load_skill_file(path: Path) -> Skill:
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
