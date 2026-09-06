import re
from datetime import date
from importlib import import_module
from importlib.metadata import entry_points
from types import SimpleNamespace

import pytest

from kinby.cli import main
from kinby.core.prompt import PromptSectionName, assemble_system_prompt
from kinby.instance import init_instance, load_instance
from kinby.plugins.routines import load_routines
from kinby.plugins.skills import load_skills


def test_fresh_instance_catalogues_and_shows_packaged_write_routine(tmp_path, capsys):
    path = init_instance(tmp_path / "instance")
    instance = load_instance(path)

    skills, warnings = load_skills(instance)

    assert not warnings
    assert [skill.name for skill in skills] == ["write-routine"]
    assert skills[0].description == "Draft kinby routine files for recurring or manual work."
    sections = assemble_system_prompt(instance, skills, date(2026, 9, 5))
    catalogue = next(
        section.text for section in sections if section.name == PromptSectionName.SKILLS
    )
    assert "- write-routine: Draft kinby routine files for recurring or manual work." in catalogue
    assert not list((path / "skills").glob("*/SKILL.md"))
    assert main(["instance", "show", str(path)]) == 0
    assert f"skills:\n  write-routine: {skills[0].source}\n" in capsys.readouterr().out


def test_disabling_defaults_omits_packaged_write_routine(tmp_path, capsys):
    path = init_instance(tmp_path / "instance")
    with (path / "kinby.toml").open("a", encoding="utf-8") as manifest:
        manifest.write("\n[tools]\ndefaults = false\n")

    skills, warnings = load_skills(load_instance(path))

    assert not warnings
    assert not skills
    assert main(["instance", "show", str(path)]) == 0
    assert "skills:\nprompt sections:\n" in capsys.readouterr().out


def _write_skill(root, name, body):
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {body}\n---\n{body}\n", encoding="utf-8")
    return path


def test_instance_then_packaged_then_workspace_skill_precedence(tmp_path):
    path = init_instance(tmp_path / "instance")
    workspace = path / "workspace" / ".agents" / "skills"
    _write_skill(workspace, "write-routine", "Workspace routine instructions.")
    _write_skill(workspace, "workspace-only", "Workspace instructions.")
    _write_skill(path / "skills", "instance-only", "Instance instructions.")
    with (path / "kinby.toml").open("a", encoding="utf-8") as manifest:
        manifest.write("\n[workspace.conventions]\nenabled = true\n")
    instance = load_instance(path)

    skills, warnings = load_skills(instance)

    assert not warnings
    assert [skill.name for skill in skills] == ["instance-only", "write-routine", "workspace-only"]
    assert skills[1].description == "Draft kinby routine files for recurring or manual work."

    override = _write_skill(path / "skills", "write-routine", "Instance routine instructions.")
    skills, warnings = load_skills(instance)

    assert not warnings
    assert [skill.name for skill in skills] == ["instance-only", "write-routine", "workspace-only"]
    assert skills[1].source == override
    assert skills[1].body == "Instance routine instructions."

    with (path / "kinby.toml").open("a", encoding="utf-8") as manifest:
        manifest.write("\n[tools]\ndefaults = false\n")
    remaining, warnings = load_skills(load_instance(path))
    assert not warnings
    assert remaining == skills


@pytest.mark.parametrize("defaults", [True, False])
def test_other_packages_survive_defaults_flag_and_keep_first_duplicate(
    tmp_path, monkeypatch, defaults
):
    path = init_instance(tmp_path / "instance")
    with (path / "kinby.toml").open("a", encoding="utf-8") as manifest:
        manifest.write(f"\n[tools]\ndefaults = {str(defaults).lower()}\n")
    first = _write_skill(tmp_path / "first", "shared", "First package.")
    second = _write_skill(tmp_path / "second", "shared", "Second package.")
    installed = entry_points(group="kinby.skills")

    def discover(*, group):
        assert group == "kinby.skills"
        return (
            *installed,
            SimpleNamespace(
                name="defaults",
                dist=SimpleNamespace(name="other-package"),
                value="other:SKILLS",
                load=lambda: first.parent.parent,
            ),
            SimpleNamespace(
                name="second", value="second:SKILLS", load=lambda: second.parent.parent
            ),
        )

    monkeypatch.setattr(import_module("kinby.plugins.skills"), "entry_points", discover)

    skills, warnings = load_skills(load_instance(path))

    assert [skill.name for skill in skills] == (
        ["write-routine", "shared"] if defaults else ["shared"]
    )
    assert skills[-1].body == "First package."
    assert len(warnings) == 1
    assert warnings[0].sources == (str(first), str(second))


@pytest.mark.parametrize("broken_export", ["wrong-type", "import-error"])
def test_broken_packaged_skill_export_warns_and_preserves_other_skills(
    tmp_path, monkeypatch, broken_export
):
    path = init_instance(tmp_path / "instance")
    installed = entry_points(group="kinby.skills")

    def load():
        if broken_export == "import-error":
            raise ImportError("Skill package is broken.")
        return "not a Path"

    def discover(*, group):
        assert group == "kinby.skills"
        return (*installed, SimpleNamespace(name="broken", value="broken:SKILLS", load=load))

    monkeypatch.setattr(import_module("kinby.plugins.skills"), "entry_points", discover)

    skills, warnings = load_skills(load_instance(path))

    assert [skill.name for skill in skills] == ["write-routine"]
    assert len(warnings) == 1
    assert warnings[0].sources == ("broken:SKILLS",)
    assert warnings[0].message == (
        "ImportError: Skill package is broken."
        if broken_export == "import-error"
        else 'TypeError: Entry point "broken:SKILLS" does not export a skill directory Path.'
    )


def test_write_routine_documents_every_frontmatter_key_from_spec(tmp_path):
    path = init_instance(tmp_path / "instance")
    skills, _ = load_skills(load_instance(path))

    document = skills[0].source.read_text(encoding="utf-8")

    assert set(re.findall(r"^\| `([a-z_]+)` \|", document, re.MULTILINE)) == {
        "description",
        "schedule",
        "enabled",
        "mode",
        "catch_up",
        "run",
        "arguments",
        "steps",
        "tokens",
        "seconds",
    }


def test_write_routine_example_loads_as_one_firing_with_documented_defaults(tmp_path):
    path = init_instance(tmp_path / "instance")
    instance = load_instance(path)
    skills, _ = load_skills(instance)
    examples = re.findall(r"```markdown\n(.*?)\n```", skills[0].body, re.DOTALL)
    assert len(examples) == 1
    routine_path = path / "routines" / "daily-summary" / "ROUTINE.md"
    routine_path.parent.mkdir()
    routine_path.write_text(examples[0], encoding="utf-8")

    routines, warnings = load_routines(instance)

    assert not warnings
    assert len(routines) == 1
    routine = routines[0]
    assert routine.name == "daily-summary"
    assert routine.description == "Summarize today's notes."
    assert routine.schedule == "0 18 * * *"
    assert (
        routine.prompt
        == "Read today's notes. Summarize decisions and unfinished work in this turn."
    )
    assert routine.enabled
    assert routine.mode == "ask"
    assert routine.catch_up
    assert routine.arguments == {}
    assert routine.code_step is None
    assert routine.budgets.steps is None
    assert routine.budgets.tokens is None
    assert routine.budgets.seconds is None


def test_write_routine_teaches_execution_and_recovery_without_future_operations(tmp_path):
    path = init_instance(tmp_path / "instance")
    skills, _ = load_skills(load_instance(path))
    body = " ".join(skills[0].body.split())

    for guidance in (
        "exactly one public function decorated with `@tool(write=...)`",
        "move the tool into the instance's `tools/`",
        "Returning `None` completes a recorded no-work turn",
        "Both the main model and the recap model are skipped",
        "preserves run history",
        "Prefer push",
        "minutes-level schedule",
        "no hard minimum interval or enabled-routine cap",
        "ten consecutive failed firings",
        "successful manual run resets the failure count",
        "manual run never re-enables",
        "explicitly set `enabled: true`",
        "Validated, gated create, edit, enable, disable, and delete operations belong to #61",
        "One-shot reminders belong to #119",
        "Do not approximate a one-shot reminder with repeating cron",
    ):
        assert guidance in body
