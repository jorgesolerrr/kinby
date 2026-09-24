"""kinby's own CI: the four checks, then an update of the coder after a green push to main."""

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _jobs() -> dict[str, dict[str, object]]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]


def _runs(job: dict[str, object]) -> list[str]:
    steps = job["steps"]
    assert isinstance(steps, list)
    return [" ".join(str(step["run"]).split()) for step in steps if "run" in step]


def test_ci_runs_the_four_checks():
    runs = _runs(_jobs()["checks"])

    assert runs[-4:] == [
        "uv run ruff check .",
        "uv run ruff format --check .",
        "uv run ty check",
        "uv run pytest",
    ]


def test_a_green_push_to_main_updates_the_coder_to_that_commit():
    job = _jobs()["update-coder"]
    steps = job["steps"]
    assert isinstance(steps, list)

    assert job["needs"] == "checks"
    assert job["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    assert _runs(job)[-1] == (
        'uv run --locked --no-dev kinby hub update --connect "$KINBY_HUB_URL" '
        '"$KINBY_CODER_INSTANCE_ID" --revision "$GITHUB_SHA"'
    )
    assert steps[-1]["env"] == {
        "KINBY_TOKEN": "${{ secrets.KINBY_HUB_UPDATE_TOKEN }}",
        "KINBY_CODER_INSTANCE_ID": "${{ secrets.KINBY_CODER_INSTANCE_ID }}",
    }


def test_the_update_skips_with_a_notice_until_the_hub_url_is_set():
    job = _jobs()["update-coder"]
    steps = job["steps"]
    assert isinstance(steps, list)

    assert job["env"] == {"KINBY_HUB_URL": "${{ secrets.KINBY_HUB_URL }}"}
    assert steps[0]["if"] == "env.KINBY_HUB_URL == ''"
    assert steps[0]["run"].startswith('echo "::notice::')
    assert {step["if"] for step in steps[1:]} == {"env.KINBY_HUB_URL != ''"}
