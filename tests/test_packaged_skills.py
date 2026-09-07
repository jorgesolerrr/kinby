import asyncio
import re
from datetime import date
from importlib import import_module
from importlib.metadata import entry_points
from types import SimpleNamespace
from uuid import uuid4

import pytest

from kinby.cli import main
from kinby.contracts import PermissionMode
from kinby.core.prompt import PromptSectionName, assemble_system_prompt
from kinby.instance import init_instance, load_instance
from kinby.plugins import Tool, ToolContext
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


@pytest.mark.parametrize(
    "broken_export", ["wrong-type", "missing-path", "file-path", "import-error"]
)
def test_broken_packaged_skill_export_warns_and_preserves_other_skills(
    tmp_path, monkeypatch, broken_export
):
    path = init_instance(tmp_path / "instance")
    installed = entry_points(group="kinby.skills")
    invalid_path = tmp_path / broken_export
    if broken_export == "file-path":
        invalid_path.write_text("not a skill directory", encoding="utf-8")

    def load():
        if broken_export == "import-error":
            raise ImportError("Skill package is broken.")
        if broken_export == "wrong-type":
            return "not a Path"
        return invalid_path

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
    frontmatter_section = document.partition("## Frontmatter")[2].partition("\n## ")[0]

    assert set(re.findall(r"^\| `([a-z_]+)` \|", frontmatter_section, re.MULTILINE)) == {
        "description",
        "schedule",
        "enabled",
        "mode",
        "catch_up",
        "run",
        "signal",
        "arguments",
        "steps",
        "tokens",
        "seconds",
    }


def test_write_routine_documents_signal_configuration(tmp_path):
    path = init_instance(tmp_path / "instance")
    skills, _ = load_skills(load_instance(path))
    body = skills[0].body
    signal_section = body.partition("## Receive signals")[2].partition("\n## ")[0]

    assert set(re.findall(r"^\| `([a-z_]+)` \|", signal_section, re.MULTILINE)) == {
        "auth",
        "secret",
        "signature_header",
        "delivery_header",
    }
    for guidance in (
        "`token`",
        "`Authorization: Bearer <secret>`",
        "`hmac-sha256`",
        "raw request body",
        "GitHub",
        "instance `.env`",
        "existing process value first",
        "delivery id",
        "deduplicate",
        "origin",
    ):
        assert guidance in signal_section


def test_write_routine_github_example_loads_and_filters_deliveries(tmp_path, monkeypatch):
    path = init_instance(tmp_path / "instance")
    instance = load_instance(path)
    skills, _ = load_skills(instance)
    example = skills[0].body.partition("## Example: GitHub `ready-for-agent`")[2]
    example_text = " ".join(example.split())

    for content in (
        "`routines/ready-for-agent/ROUTINE.md`",
        "mode: ask",
        "auth: hmac-sha256",
        "secret: GITHUB_WEBHOOK_SECRET",
        "signature_header: X-Hub-Signature-256",
        "delivery_header: X-GitHub-Delivery",
        "`routines/ready-for-agent/run.py`",
        "requiring approval for each workspace write",
        "development-loop",
    ):
        assert content in example_text

    (routine_document,) = re.findall(r"```markdown\n(.*?)\n```", example, re.DOTALL)
    (code_step,) = re.findall(r"```python\n(.*?)\n```", example, re.DOTALL)
    routine_path = path / "routines" / "ready-for-agent" / "ROUTINE.md"
    routine_path.parent.mkdir(parents=True)
    routine_path.write_text(routine_document, encoding="utf-8")
    (routine_path.parent / "run.py").write_text(code_step, encoding="utf-8")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")

    routines, warnings = load_routines(instance)

    assert not warnings
    assert len(routines) == 1
    routine = routines[0]
    assert routine.mode is PermissionMode.ASK
    code_step = routine.code_step
    assert isinstance(code_step, Tool)
    context = ToolContext(instance, uuid4())

    def invoke(signal):
        return asyncio.run(code_step.ainvoke_raw({"signal": signal}, context))

    matching = {
        "body": {
            "action": "labeled",
            "label": {"name": "ready-for-agent"},
            "issue": {
                "number": 131,
                "title": "Teach write-routine to handle signals",
                "html_url": "https://github.com/jorgesolerrr/kinby/issues/131",
            },
        }
    }
    assert invoke(matching) == (
        "ReadyIssue(number=131, title='Teach write-routine to handle signals', "
        "url='https://github.com/jorgesolerrr/kinby/issues/131')"
    )
    no_work_bodies = (
        {"action": "opened"},
        {"action": "labeled", "label": {"name": "needs-info"}},
        {
            "action": "labeled",
            "label": {"name": "ready-for-agent"},
            "issue": {"number": 131, "title": "Missing URL"},
        },
        {
            "action": "labeled",
            "label": {"name": "ready-for-agent"},
            "issue": {"number": True, "title": "Wrong number", "html_url": "url"},
        },
        {
            "action": "labeled",
            "label": {"name": "ready-for-agent"},
            "issue": {"number": 131, "title": 42, "html_url": "url"},
        },
        {
            "action": "labeled",
            "label": {"name": "ready-for-agent"},
            "issue": {"number": 131, "title": "Wrong URL", "html_url": None},
        },
    )
    for body in no_work_bodies:
        assert invoke({"body": body}) is None


def test_write_routine_teaches_when_and_how_to_use_signals(tmp_path):
    path = init_instance(tmp_path / "instance")
    skills, _ = load_skills(load_instance(path))
    body = " ".join(skills[0].body.split())

    for guidance in (
        "Prefer a signal over a schedule in that case",
        "declare both `signal` and `schedule`",
        "schedule as a fallback",
        "chatty signal",
        "returns `None` for most deliveries",
        "`kinby routine run <name> --payload <file>`",
        "saved delivery",
    ):
        assert guidance in body
    assert "The signal receiver is separate work" not in body


def test_write_routine_daily_example_loads_with_documented_defaults(tmp_path):
    path = init_instance(tmp_path / "instance")
    instance = load_instance(path)
    skills, _ = load_skills(instance)
    examples = re.findall(r"```markdown\n(.*?)\n```", skills[0].body, re.DOTALL)
    example = next(
        example for example in examples if "description: Summarize today's notes." in example
    )
    routine_path = path / "routines" / "daily-summary" / "ROUTINE.md"
    routine_path.parent.mkdir()
    routine_path.write_text(example, encoding="utf-8")

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
