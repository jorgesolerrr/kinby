import os

import pytest

from kinby.cli import main


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
def test_instance_show_names_a_malformed_model(
    tmp_path, capsys, model_setting, offending_key
):
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
            (
                'id = "alice"\n\n[models]\nmain = "openai:gpt-5"\n\n'
                '[workspace]\nbranch = "main"\n'
            ),
            "workspace.branch",
        ),
        (
            (
                'id = "alice"\n\n[models]\nmain = "openai:gpt-5"\n\n'
                "[memory]\nenabled = true\n"
            ),
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
