import json
import os
import subprocess
import sys

import pytest

from kinby.cli import main
from kinby.contracts import PackageDescription, SetupFieldKind, SetupFieldType
from kinby.packages.__main__ import main as candidate_check
from tests.fake_package import install_fake_package


def _executable(directory, name):
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _install(tmp_path, monkeypatch, **options):
    package = install_fake_package(tmp_path / "site", **options)
    monkeypatch.syspath_prepend(str(package.site))
    return package


def test_a_package_that_installs_passes_the_check(tmp_path, monkeypatch, capsys):
    _executable(tmp_path / "bin", "kinby-fake-editor")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    _install(tmp_path, monkeypatch, executables=("kinby-fake-editor",))

    status = main(["package", "check", "writer"])

    output = capsys.readouterr()
    assert (status, output.err) == (0, "")
    assert output.out == 'Package "writer" passed the check.\n'


def test_a_missing_executable_fails_the_check(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    _install(tmp_path, monkeypatch, executables=("kinby-fake-editor",))

    status = main(["package", "check", "writer"])

    assert status == 1
    assert capsys.readouterr().err == 'Executable "kinby-fake-editor" is not on PATH.\n'


def test_the_check_reports_every_failure_together(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    package = _install(
        tmp_path,
        monkeypatch,
        config="tone: plain\ntoken: GITHUB_TOKEN\n",
        executables=("kinby-fake-editor",),
    )
    (package.template / "notes.env").write_text("EDITOR_TOKEN=hunter2\n", encoding="utf-8")
    (package.template / "routines" / "draft" / "ROUTINE.md").write_text(
        "---\nenabled: false\n---\nDraft it.\n", encoding="utf-8"
    )
    (package.root / "skills" / "drafting" / "style.md").unlink()

    status = main(["package", "check", "writer"])

    assert status == 1
    errors = capsys.readouterr().err.splitlines()
    expected = [
        f"Template file notes.env is not part of distribution {package.module}.",
        'Template file notes.env holds a value for the secret "EDITOR_TOKEN".',
        'Executable "kinby-fake-editor" is not on PATH.',
        'routines/draft/ROUTINE.md: Frontmatter must contain a non-empty "description" string.',
        '/package.yaml: token: "GITHUB_TOKEN" is not a required secret this package declares.',
        f'Skill "drafting" links to style.md, which is not in {package.root}/skills/drafting.',
    ]
    assert len(errors) == len(expected)
    for failure in expected:
        assert any(error.endswith(failure) for error in errors), failure


def test_a_signal_routine_loads_without_the_secret_value_it_declares(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("EDITOR_TOKEN", raising=False)
    package = _install(tmp_path, monkeypatch)
    inbox = package.template / "routines" / "inbox"
    inbox.mkdir()
    (inbox / "ROUTINE.md").write_text(
        "---\ndescription: Read editor mail.\nsignal:\n  secret: EDITOR_TOKEN\n---\nRead it.\n",
        encoding="utf-8",
    )
    record = next(package.site.glob("*.dist-info")) / "RECORD"
    record.write_text(
        record.read_text(encoding="utf-8")
        + f"{(inbox / 'ROUTINE.md').relative_to(package.site).as_posix()},,\n",
        encoding="utf-8",
    )

    status = main(["package", "check", "writer"])

    assert (status, capsys.readouterr().err) == (0, "")
    assert "EDITOR_TOKEN" not in os.environ


def test_a_template_left_out_of_the_distribution_fails_the_check(tmp_path, monkeypatch, capsys):
    package = _install(tmp_path, monkeypatch, record_template=False)

    status = main(["package", "check", "writer"])

    assert status == 1
    errors = capsys.readouterr().err.splitlines()
    assert f"Template file SYSTEM.md is not part of distribution {package.module}." in errors


def test_malformed_secret_declarations_fail_the_check(tmp_path, monkeypatch, capsys):
    package = _install(tmp_path, monkeypatch)
    source = package.root / "__init__.py"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            'RequiredSecret("EDITOR_TOKEN", "Editor token", "Authenticates editing."),',
            'RequiredSecret("EDITOR_TOKEN", "Editor token", "Authenticates editing."),'
            'RequiredSecret("EDITOR_TOKEN", "Again", "Twice."),'
            'RequiredSecret("EDITOR-KEY", "", "Signs."),',
        ),
        encoding="utf-8",
    )

    status = main(["package", "check", "writer"])

    assert status == 1
    assert capsys.readouterr().err.splitlines() == [
        'Required secret "EDITOR_TOKEN" is declared more than once.',
        'Required secret "EDITOR-KEY" is not an environment variable name.',
        'Required secret "EDITOR-KEY" has no label.',
    ]


def test_a_bad_permissions_file_is_reported_with_the_other_failures(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    package = _install(tmp_path, monkeypatch, executables=("kinby-fake-editor",))
    (package.template / "permissions.toml").write_text("mode = [\n", encoding="utf-8")

    status = main(["package", "check", "writer"])

    assert status == 1
    errors = capsys.readouterr().err.splitlines()
    assert 'Executable "kinby-fake-editor" is not on PATH.' in errors
    assert any(error.startswith("permissions.toml:") for error in errors)


def test_a_skill_entry_that_fails_to_load_is_reported_with_the_other_failures(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    package = _install(tmp_path, monkeypatch, executables=("kinby-fake-editor",))
    entry_points = next(package.site.glob("*.dist-info")) / "entry_points.txt"
    entry_points.write_text(
        entry_points.read_text(encoding="utf-8").replace(
            f"writer = {package.module}:SKILLS",
            f"writer = {package.module}:MISSING",
        ),
        encoding="utf-8",
    )

    status = main(["package", "check", "writer"])

    assert status == 1
    errors = capsys.readouterr().err.splitlines()
    assert 'Executable "kinby-fake-editor" is not on PATH.' in errors
    assert any(
        error.startswith(f'Skill entry point "{package.module}:MISSING" failed to load:')
        for error in errors
    )


def test_an_unknown_package_fails_the_check(capsys):
    status = main(["package", "check", "no-such-package"])

    assert status == 1
    assert capsys.readouterr().err == 'Package "no-such-package" is not installed.\n'


def test_the_candidate_check_prints_the_package_only_when_it_passes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    _install(tmp_path, monkeypatch)

    assert candidate_check(["writer"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["descriptor"]["id"] == "writer"
    assert printed["files"]["package.yaml"] == "tone: plain\ntoken: EDITOR_TOKEN\n"


def test_the_candidate_check_without_a_package_describes_a_vanilla_instance(capsys):
    assert candidate_check([]) == 0

    printed = PackageDescription.model_validate_json(capsys.readouterr().out)
    assert (printed.display_name, printed.icon) == ("Vanilla", "sparkles")
    assert [
        (field.name, field.label, field.kind, field.type, field.required)
        for field in printed.setup_fields
    ] == [
        ("model", "Model", SetupFieldKind.CONFIG, SetupFieldType.TEXT, True),
        ("api_key", "API key", SetupFieldKind.SECRET, SetupFieldType.TEXT, True),
        (
            "behavior_prompt",
            "Behavior prompt",
            SetupFieldKind.CONFIG,
            SetupFieldType.MULTILINE,
            False,
        ),
    ]


def test_the_candidate_check_validates_an_existing_instance_config(tmp_path, monkeypatch, capsys):
    _install(tmp_path, monkeypatch)
    instance = tmp_path / "instance"
    assert main(["init", str(instance), "--package", "writer"]) == 0
    (instance / "package.yaml").write_text("tone: plain\n", encoding="utf-8")
    capsys.readouterr()

    status = candidate_check(["writer", str(instance)])

    output = capsys.readouterr()
    assert status == 1
    assert output.out == ""
    assert output.err == f"{instance / 'package.yaml'}: token: Field required\n"


@pytest.mark.parametrize("passing", [True, False])
def test_the_candidate_check_runs_as_the_hub_calls_it(tmp_path, passing):
    package = install_fake_package(tmp_path / "site", executables=("kinby-fake-editor",))
    if passing:
        _executable(tmp_path / "bin", "kinby-fake-editor")
    environment = {
        **os.environ,
        "PATH": f"{tmp_path / 'bin'}:{os.path.dirname(sys.executable)}",
        "PYTHONPATH": str(package.site),
    }

    result = subprocess.run(
        [sys.executable, "-m", "kinby.packages", "writer"],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert (result.returncode == 0) is passing
    if not passing:
        assert result.stderr == 'Executable "kinby-fake-editor" is not on PATH.\n'
