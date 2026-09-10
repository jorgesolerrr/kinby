"""The instances under instances/ load, and their routines filter deliveries."""

import json
import shutil
from pathlib import Path

import pytest

from kinby.cli import main
from kinby.instance import load_instance
from kinby.plugins.routines import load_routines
from kinby.plugins.skills import load_skills

INSTANCES = Path(__file__).parents[1] / "instances"
CODER = INSTANCES / "coder"


def _issue(*labels: str) -> dict[str, object]:
    return {
        "number": 7,
        "title": "Something",
        "html_url": "https://github.com/jorgesolerrr/kinby/issues/7",
        "state": "open",
        "labels": [{"name": label} for label in labels],
    }


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
    assert [routine.name for routine in routines] == ["implement-ready-issue"]
    assert {"implement-ticket", "open-pr", "tdd", "unslop"} <= {skill.name for skill in skills}


@pytest.mark.parametrize(
    "delivery",
    [
        {"action": "labeled", "label": {"name": "bug"}, "issue": _issue("bug")},
        {"action": "opened", "issue": _issue("bug")},
        {"action": "closed", "issue": {**_issue("ready-for-agent"), "state": "closed"}},
        {"action": "created", "comment": {"body": "hello"}},
    ],
)
def test_coder_routine_finishes_without_work_unless_an_issue_is_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    delivery: dict[str, object],
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
    instance = _coder_copy(tmp_path)
    payload = tmp_path / "delivery.json"
    payload.write_text(json.dumps(delivery), encoding="utf-8")

    exit_code = main(
        [
            "routine",
            "run",
            "implement-ready-issue",
            "--payload",
            str(payload),
            "--instance",
            str(instance),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[tool.result] select_ready_issue (ok): None" in captured.out
