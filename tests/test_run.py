from pathlib import Path

import pytest

from kinby.cli import main
from kinby.instance import load_instance, reload_manifest


def test_run_shows_a_session_model_override_without_changing_the_manifest(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    instance = tmp_path / "alice"
    instance.mkdir()
    manifest_path = instance / "kinby.toml"
    original_manifest = 'id = "alice"\n\n[models]\nmain = "openai:gpt-5"\n'
    manifest_path.write_text(original_manifest, encoding="utf-8")

    exit_code = main(["run", str(instance), "--model", "anthropic:claude-sonnet-4-6"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "id: alice" in captured.out
    assert "main: anthropic:claude-sonnet-4-6" in captured.out
    assert "The agent loop is not yet available." in captured.out
    assert captured.err == ""
    assert manifest_path.read_text(encoding="utf-8") == original_manifest


def test_reload_manifest_reads_changes_and_reapplies_the_session_model_override(
    tmp_path: Path,
) -> None:
    instance_path = tmp_path / "alice"
    instance_path.mkdir()
    manifest_path = instance_path / "kinby.toml"
    manifest_path.write_text(
        'id = "alice"\n\n[models]\nmain = "openai:gpt-5"\n',
        encoding="utf-8",
    )
    instance = load_instance(instance_path)
    manifest_path.write_text(
        ('id = "alice"\npersona_name = "Ada"\n\n[models]\nmain = "google:gemini-2.5-pro"\n'),
        encoding="utf-8",
    )

    manifest = reload_manifest(
        instance,
        model_override="anthropic:claude-sonnet-4-6",
    )

    assert manifest.persona_name == "Ada"
    assert manifest.models.main == "anthropic:claude-sonnet-4-6"
    assert manifest.models.recap == "anthropic:claude-sonnet-4-6"
