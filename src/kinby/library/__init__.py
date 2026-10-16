"""The library kinby ships. Its factories ship apart, in ``kinby.factories``."""

from pathlib import Path

from kinby.contracts import SkillName

#: The skills kinby ships, one Agent Skills folder each. A hub copies them into its library.
SHIPPED_SKILLS = Path(__file__).parent / "skills"
#: The shipped skills that teach the home instance its hub tools. The hub picks them for it.
HOME_SKILLS: tuple[SkillName, ...] = (
    SkillName("kinby-hub"),
    SkillName("create-instance"),
    SkillName("write-routine"),
    SkillName("write-factory"),
    SkillName("hub-report"),
)

__all__ = ["HOME_SKILLS", "SHIPPED_SKILLS"]
