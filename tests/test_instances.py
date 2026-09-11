"""The instances under instances/ load, and their routines filter deliveries."""

import shutil
from pathlib import Path

import pytest

from kinby.instance import load_instance
from kinby.plugins.routines import load_routines
from kinby.plugins.skills import load_skills

INSTANCES = Path(__file__).parents[1] / "instances"
CODER = INSTANCES / "coder"


def _coder_copy(tmp_path: Path) -> Path:
    instance = tmp_path / "coder"
    shutil.copytree(CODER, instance, ignore=shutil.ignore_patterns(".state", ".env", "workspace"))
    (instance / "workspace").mkdir()
    return instance


def test_coder_instance_loads_its_routine_and_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance = load_instance(CODER)

    routines, routine_warnings = load_routines(instance)
    skills, skill_warnings = load_skills(instance)

    assert routine_warnings == ()
    assert skill_warnings == ()
    assert instance.manifest.models.main == "anthropic:claude-sonnet-5"
    assert instance.manifest.models.recap == "anthropic:claude-sonnet-5"
    assert instance.manifest.models.main in instance.manifest.prices
    assert instance.manifest.budgets.seconds == 7200
    assert [routine.name for routine in routines] == ["implement-ready-issue"]
    assert routines[0].enabled is True
    assert routines[0].schedule == "0 * * * *"
    assert routines[0].arguments == {
        "implementer_model": "gpt-5.6-sol",
        "implementer_effort": "high",
        "reviewer_model": "claude-fable-5-1",
        "review_round_limit": 3,
        "implement_timeout_seconds": 1800,
        "review_timeout_seconds": 900,
        "fix_timeout_seconds": 900,
    }
    assert "Comment a short summary on its issue" in routines[0].prompt
    assert "then stop" in routines[0].prompt
    assert routines[0].code_step is not None
    assert routines[0].code_step.name == "implement_ready_issue"
    assert {path.name for path in (CODER / "skills").iterdir()} == {"unslop"}
    assert {skill.name for skill in skills} == {"unslop", "write-routine"}


def test_coder_loads_routine_skills_from_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance_path = _coder_copy(tmp_path)
    workspace_skills = instance_path / "workspace" / ".claude" / "skills"
    shutil.copytree(INSTANCES.parent / ".claude" / "skills", workspace_skills)

    skills, warnings = load_skills(load_instance(instance_path))
    by_name = {str(skill.name): skill for skill in skills}

    assert warnings == ()
    for name in ("implement-ticket", "open-pr"):
        assert by_name[name].source == workspace_skills / name / "SKILL.md"
        assert by_name[name].body
    assert by_name["unslop"].source == instance_path / "skills" / "unslop" / "SKILL.md"
