"""Assemble the system prompt from one loaded instance."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path

from kinby.instance import Instance
from kinby.instance.layout import MEMORY_DIR, PROFILE_NAME, SYSTEM_NAME

KINBY_PREAMBLE = "You are a personal AI teammate running on kinby."


class PromptSectionName(StrEnum):
    PREAMBLE = "preamble"
    BEHAVIOR = "behavior prompt"
    CONVENTIONS = "workspace conventions"
    SKILLS = "skills catalogue"
    PROFILE = "profile"
    ENVIRONMENT = "environment"


class PromptSectionSource(StrEnum):
    KINBY = "kinby"
    RUNTIME = "runtime"


@dataclass(frozen=True)
class PromptFileSource:
    path: Path

    def __str__(self) -> str:
        return str(self.path)


@dataclass(frozen=True)
class PromptSection:
    """One named, attributable part of the system prompt."""

    name: PromptSectionName
    source: PromptFileSource | PromptSectionSource
    text: str


def _file_section(name: PromptSectionName, path: Path) -> PromptSection | None:
    if not path.is_file():
        return None
    return PromptSection(
        name=name,
        source=PromptFileSource(path),
        text=path.read_text(encoding="utf-8").strip("\r\n"),
    )


def _environment(instance: Instance, today: date) -> PromptSection:
    manifest = instance.manifest
    persona = f"persona name: {manifest.persona_name}\n" if manifest.persona_name else ""
    return PromptSection(
        name=PromptSectionName.ENVIRONMENT,
        source=PromptSectionSource.RUNTIME,
        text=(
            "# Environment\n"
            f"instance id: {manifest.id}\n"
            f"{persona}"
            f"workspace path: {manifest.workspace.path}\n"
            f"main model: {manifest.models.main}\n"
            f"date: {today.isoformat()}"
        ),
    )


def assemble_system_prompt(instance: Instance, today: date) -> tuple[PromptSection, ...]:
    """Return the existing prompt sections in their fixed cache-friendly order."""
    sections = [
        PromptSection(
            name=PromptSectionName.PREAMBLE,
            source=PromptSectionSource.KINBY,
            text=KINBY_PREAMBLE,
        )
    ]
    behavior = _file_section(PromptSectionName.BEHAVIOR, instance.path / SYSTEM_NAME)
    if behavior is not None:
        sections.append(behavior)
    sections.extend(
        section
        for path in instance.manifest.workspace.conventions.instructions
        if (section := _file_section(PromptSectionName.CONVENTIONS, path)) is not None
    )
    profile = _file_section(
        PromptSectionName.PROFILE,
        instance.path / MEMORY_DIR / PROFILE_NAME,
    )
    if profile is not None:
        sections.append(profile)
    sections.append(_environment(instance, today))
    return tuple(sections)


def render_system_prompt(sections: Sequence[PromptSection]) -> str:
    return "\n\n".join(section.text for section in sections)
