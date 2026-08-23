import os
from pathlib import Path

import pytest

from kinby.cli import main


def _write_instance(instance: Path, instance_id: str) -> None:
    instance.mkdir(parents=True, exist_ok=True)
    (instance / "kinby.toml").write_text(
        f'id = "{instance_id}"\n\n[models]\nmain = "openai:gpt-5"\n',
        encoding="utf-8",
    )


def _control_discovery(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("KINBY_INSTANCE", raising=False)
    return home, cwd


def test_instance_show_reports_the_resolved_manifest(tmp_path, capsys):
    instance = tmp_path / "alice"
    workspace = instance / "project"
    workspace.mkdir(parents=True)
    (instance / "kinby.toml").write_text(
        """\
id = "alice"
persona_name = "Ada"
state_dir = "runtime"

[models]
main = "openai:gpt-5"
embed = "openai:text-embedding-3-small"

[workspace]
path = "project"
source = "https://example.com/alice/project.git"

[memory]
""",
        encoding="utf-8",
    )

    exit_code = main(["instance", "show", str(instance)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "id: alice" in captured.out
    assert "persona name: Ada" in captured.out
    assert f"path: {instance.resolve()}" in captured.out
    assert "resolved by: explicit directory" in captured.out
    assert "main: openai:gpt-5" in captured.out
    assert "recap: openai:gpt-5" in captured.out
    assert "embed: openai:text-embedding-3-small" in captured.out
    assert f"workspace: {workspace.resolve()} (present)" in captured.out
    assert "source: https://example.com/alice/project.git" in captured.out
    assert f"state dir: {(instance / 'runtime').resolve()}" in captured.out
    assert captured.err == ""


def test_instance_show_names_missing_models_main(tmp_path, capsys):
    instance = tmp_path / "alice"
    instance.mkdir()
    (instance / "kinby.toml").write_text(
        'id = "alice"\n\n[models]\n',
        encoding="utf-8",
    )

    exit_code = main(["instance", "show", str(instance)])

    captured = capsys.readouterr()
    assert exit_code != 0
    assert captured.out == ""
    assert "models.main" in captured.err


@pytest.mark.parametrize(
    ("model_setting", "offending_key"),
    [
        ('main = "gpt-5"', "models.main"),
        ('main = "openai:gpt-5"\nrecap = "gpt-5"', "models.recap"),
        ('main = "openai:gpt-5"\nembed = "text-embedding-3-small"', "models.embed"),
    ],
)
def test_instance_show_names_a_malformed_model(tmp_path, capsys, model_setting, offending_key):
    instance = tmp_path / "alice"
    instance.mkdir()
    (instance / "kinby.toml").write_text(
        f'id = "alice"\n\n[models]\n{model_setting}\n',
        encoding="utf-8",
    )

    exit_code = main(["instance", "show", str(instance)])

    captured = capsys.readouterr()
    assert exit_code != 0
    assert captured.out == ""
    assert offending_key in captured.err
    assert "provider:model" in captured.err


@pytest.mark.parametrize(
    ("manifest", "offending_key"),
    [
        (
            'id = "alice"\napi_key = "secret"\n\n[models]\nmain = "openai:gpt-5"\n',
            "api_key",
        ),
        (
            'id = "alice"\n\n[models]\nmain = "openai:gpt-5"\ntimeout = 30\n',
            "models.timeout",
        ),
        (
            ('id = "alice"\n\n[models]\nmain = "openai:gpt-5"\n\n[workspace]\nbranch = "main"\n'),
            "workspace.branch",
        ),
        (
            ('id = "alice"\n\n[models]\nmain = "openai:gpt-5"\n\n[memory]\nenabled = true\n'),
            "memory.enabled",
        ),
    ],
)
def test_instance_show_names_unknown_keys(tmp_path, capsys, manifest, offending_key):
    instance = tmp_path / "alice"
    instance.mkdir()
    (instance / "kinby.toml").write_text(
        manifest,
        encoding="utf-8",
    )

    exit_code = main(["instance", "show", str(instance)])

    captured = capsys.readouterr()
    assert exit_code != 0
    assert captured.out == ""
    assert offending_key in captured.err


def test_instance_show_reports_a_missing_default_workspace(tmp_path, capsys):
    instance = tmp_path / "alice"
    instance.mkdir()
    (instance / "kinby.toml").write_text(
        'id = "alice"\n\n[models]\nmain = "anthropic:claude-sonnet-4-6"\n',
        encoding="utf-8",
    )

    exit_code = main(["instance", "show", str(instance)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert f"workspace: {(instance / 'workspace').resolve()} (missing)" in captured.out
    assert f"state dir: {(instance / '.state').resolve()}" in captured.out
    assert "embed: not configured" in captured.out


def test_instance_show_preserves_absolute_paths(tmp_path, capsys):
    instance = tmp_path / "alice"
    instance.mkdir()
    state_dir = tmp_path / "runtime"
    workspace = tmp_path / "project"
    (instance / "kinby.toml").write_text(
        (
            'id = "alice"\n'
            f'state_dir = "{state_dir.as_posix()}"\n\n'
            '[models]\nmain = "openai:gpt-5"\n\n'
            f'[workspace]\npath = "{workspace.as_posix()}"\n'
        ),
        encoding="utf-8",
    )

    exit_code = main(["instance", "show", str(instance)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert f"workspace: {workspace} (missing)" in captured.out
    assert f"state dir: {state_dir}" in captured.out


def test_instance_show_loads_dotenv_without_overriding_the_environment(
    tmp_path, capsys, monkeypatch
):
    instance = tmp_path / "alice"
    instance.mkdir()
    (instance / "kinby.toml").write_text(
        'id = "alice"\n\n[models]\nmain = "openai:gpt-5"\n',
        encoding="utf-8",
    )
    (instance / ".env").write_text(
        "KINBY_TEST_FROM_DOTENV=loaded\nKINBY_TEST_FROM_OPERATOR=dotenv\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("KINBY_TEST_FROM_DOTENV", raising=False)
    monkeypatch.setenv("KINBY_TEST_FROM_OPERATOR", "operator")

    exit_code = main(["instance", "show", str(instance)])

    capsys.readouterr()
    assert exit_code == 0
    assert os.environ["KINBY_TEST_FROM_DOTENV"] == "loaded"
    assert os.environ["KINBY_TEST_FROM_OPERATOR"] == "operator"


def test_instance_show_uses_kinby_instance(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _control_discovery(monkeypatch, tmp_path)
    instance = tmp_path / "from-env"
    _write_instance(instance, "from-env")
    monkeypatch.setenv("KINBY_INSTANCE", str(instance))

    exit_code = main(["instance", "show"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "id: from-env" in captured.out
    assert "resolved by: KINBY_INSTANCE" in captured.out
    assert captured.err == ""


def test_instance_show_walks_up_from_the_workspace(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _control_discovery(monkeypatch, tmp_path)
    instance = tmp_path / "walked-up"
    _write_instance(instance, "walked-up")
    nested_directory = instance / "workspace" / "sub" / "dir"
    nested_directory.mkdir(parents=True)
    monkeypatch.chdir(nested_directory)

    exit_code = main(["instance", "show"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "id: walked-up" in captured.out
    assert "resolved by: walk-up" in captured.out
    assert captured.err == ""


def test_instance_show_uses_the_home_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, _ = _control_discovery(monkeypatch, tmp_path)
    instance = home / ".kinby" / "default"
    _write_instance(instance, "home-default")

    exit_code = main(["instance", "show"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "id: home-default" in captured.out
    assert "resolved by: home default" in captured.out
    assert captured.err == ""


def test_instance_show_suggests_init_when_no_instance_exists(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, _ = _control_discovery(monkeypatch, tmp_path)

    exit_code = main(["instance", "show"])

    captured = capsys.readouterr()
    assert exit_code != 0
    assert captured.out == ""
    assert "kinby init" in captured.err
    assert not (home / ".kinby").exists()


def test_instance_option_beats_other_discovery_rules(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, cwd = _control_discovery(monkeypatch, tmp_path)
    explicit_instance = tmp_path / "explicit"
    environment_instance = tmp_path / "environment"
    _write_instance(explicit_instance, "explicit")
    _write_instance(environment_instance, "environment")
    _write_instance(cwd, "walk-up")
    _write_instance(home / ".kinby" / "default", "home-default")
    monkeypatch.setenv("KINBY_INSTANCE", str(environment_instance))

    exit_code = main(["instance", "show", "--instance", str(explicit_instance)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "id: explicit" in captured.out
    assert "resolved by: explicit directory" in captured.out
    assert captured.err == ""


def test_kinby_instance_beats_walk_up_and_home_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, cwd = _control_discovery(monkeypatch, tmp_path)
    environment_instance = tmp_path / "environment"
    _write_instance(environment_instance, "environment")
    _write_instance(cwd, "walk-up")
    _write_instance(home / ".kinby" / "default", "home-default")
    monkeypatch.setenv("KINBY_INSTANCE", str(environment_instance))

    exit_code = main(["instance", "show"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "id: environment" in captured.out
    assert "resolved by: KINBY_INSTANCE" in captured.out
    assert captured.err == ""


def test_walk_up_beats_the_home_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, cwd = _control_discovery(monkeypatch, tmp_path)
    _write_instance(cwd, "walk-up")
    _write_instance(home / ".kinby" / "default", "home-default")

    exit_code = main(["instance", "show"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "id: walk-up" in captured.out
    assert "resolved by: walk-up" in captured.out
    assert captured.err == ""
