import importlib

import pytest

from kinby.cli import main
from kinby.packages import InstalledPackage, PackageDescriptor

cli_module = importlib.import_module("kinby.cli.main")


def test_init_writes_the_starter_instance_tree(tmp_path):
    target = tmp_path / "alice"

    exit_code = main(["init", str(target)])

    assert exit_code == 0
    assert (target / "kinby.toml").is_file()
    assert (target / "SYSTEM.md").is_file()
    assert (target / "RECAP.md").is_file()
    assert (target / "permissions.toml").is_file()
    assert (target / "tools" / "README.md").is_file()
    assert (target / "skills" / "README.md").is_file()
    assert (target / "routines" / "README.md").is_file()
    assert (target / "memory" / "profile.md").is_file()
    assert (target / "memory" / "graph").is_dir()
    assert not any((target / "memory" / "graph").iterdir())
    assert (target / "workspace").is_dir()
    assert not any((target / "workspace").iterdir())
    assert (target / ".state").is_dir()
    assert not any((target / ".state").iterdir())
    gitignore = (target / ".gitignore").read_text(encoding="utf-8")
    assert ".state/" in gitignore
    assert ".env" in gitignore
    manifest = (target / "kinby.toml").read_text(encoding="utf-8")
    assert 'main = "provider:model"' in manifest
    assert '[memory]\nrecap = "every-turn"' in manifest
    assert '[feedback]\nask = "every-turn"' in manifest
    assert (
        "# [budgets]\n"
        "# One step is one node execution.\n"
        "# A budget of 7 steps allows four model calls and three tool rounds.\n"
        "# steps = 7\n"
        "# tokens = 50000\n"
        "# seconds = 300\n"
        "# usd_per_day = 5.0\n"
    ) in manifest
    assert ('# [serve]\n# listen = "127.0.0.1:8484"\n') in manifest
    assert manifest.startswith("#")
    assert (target / "SYSTEM.md").read_text(encoding="utf-8").startswith("<!--")
    recap_prompt = (target / "RECAP.md").read_text(encoding="utf-8")
    assert recap_prompt.startswith("<!--")
    assert (
        "Describe the turn's concrete outcome and decisions. "
        "Name one honest way the work could have gone differently."
    ) in recap_prompt
    assert (target / "permissions.toml").read_text(encoding="utf-8").startswith("#")
    assert (target / "memory" / "profile.md").read_text(encoding="utf-8").startswith("<!--")
    assert (target / ".gitignore").read_text(encoding="utf-8").startswith("#")
    assert (target / "tools" / "README.md").read_text(encoding="utf-8").startswith("<!--")
    assert (target / "skills" / "README.md").read_text(encoding="utf-8").startswith("<!--")
    routines_readme = (target / "routines" / "README.md").read_text(encoding="utf-8")
    assert routines_readme.startswith("<!--")
    assert "routines/<name>/ROUTINE.md" in routines_readme
    assert "run.py" in routines_readme
    assert "When kinby's packaged defaults are enabled" in routines_readme


def test_init_writes_the_commented_permissions_template(tmp_path):
    target = tmp_path / "alice"

    exit_code = main(["init", str(target)])

    assert exit_code == 0
    assert (target / "permissions.toml").read_text(encoding="utf-8") == (
        "# Permission policy. Changes apply at the next turn boundary.\n"
        "# Modes: read-only denies writes, ask requests approval, and full-access allows writes.\n"
        "# auto allows writes with declared paths inside the workspace. It asks before\n"
        "# bash, undeclared write tools, and paths outside the workspace.\n"
        'mode = "ask"\n'
        'ceiling = "full-access"\n'
        "\n"
        "[tools]\n"
        "# Override any core or plugin tool without changing the mode.\n"
        '# bash = "deny"\n'
        '# edit = "allow"\n'
        "\n"
        "[bash]\n"
        "deny = [\n"
        "    # Delete the instance home.\n"
        "    '''(?:^|[;&|\\n]\\s*)rm\\s+-rf\\s+(?:/instance|\\$\\{?KINBY_INSTANCE"
        "\\}?)(?:/|\\s|$)''',\n"
        "    # Rewrite Git history.\n"
        "    '''\\bgit\\s+(?:reset\\s+--hard|rebase|filter-branch)\\b''',\n"
        "    # Force-push Git history.\n"
        "    '''\\bgit\\s+push\\b[^\\n]*(?:--force(?:-with-lease)?|-f(?:\\s|$))''',\n"
        "]\n"
        "ask = []\n"
    )


def test_init_refuses_an_existing_instance_and_changes_nothing(tmp_path, capsys):
    target = tmp_path / "alice"
    target.mkdir()
    (target / "kinby.toml").write_text('id = "keep-me"\n', encoding="utf-8")
    (target / "marker.txt").write_text("untouched\n", encoding="utf-8")

    exit_code = main(["init", str(target)])

    captured = capsys.readouterr()
    assert exit_code != 0
    assert (target / "kinby.toml").read_text(encoding="utf-8") == 'id = "keep-me"\n'
    assert (target / "marker.txt").read_text(encoding="utf-8") == "untouched\n"
    assert not (target / "SYSTEM.md").exists()
    assert captured.err


def test_init_writes_id_as_the_slug_of_the_directory_name(tmp_path):
    target = tmp_path / "My Agent"

    exit_code = main(["init", str(target)])

    assert exit_code == 0
    manifest = (target / "kinby.toml").read_text(encoding="utf-8")
    assert 'id = "my-agent"' in manifest


def test_init_writes_model_flag_into_the_manifest(tmp_path):
    target = tmp_path / "alice"

    exit_code = main(["init", str(target), "--model", "anthropic:claude-sonnet-4-6"])

    assert exit_code == 0
    manifest = (target / "kinby.toml").read_text(encoding="utf-8")
    assert 'main = "anthropic:claude-sonnet-4-6"' in manifest


def test_init_does_not_validate_the_model_placeholder(tmp_path):
    target = tmp_path / "alice"

    exit_code = main(["init", str(target), "--model", "not-a-real-model"])

    assert exit_code == 0
    manifest = (target / "kinby.toml").read_text(encoding="utf-8")
    assert 'main = "not-a-real-model"' in manifest


def test_init_from_an_installed_package_copies_owned_configuration(
    tmp_path,
    monkeypatch,
    capsys,
):
    package = InstalledPackage(
        descriptor=PackageDescriptor(
            id="writer",
            display_name="Writing teammate",
            description="Drafts articles.",
            icon="pen",
            distribution="kinby-writer",
            version="1.4.2",
        ),
        files={
            "kinby.toml": '[feedback]\nask = "off"\n',
            "SYSTEM.md": "Write clearly.\n",
            "factory.toml": 'style = "plain"\n',
            "routines/draft/run.py": "from kinby_writer import draft\n",
        },
    )
    monkeypatch.setattr(cli_module, "inspect_installed_package", lambda package_id: package)
    target = tmp_path / "writer"

    exit_code = main(["init", str(target), "--package", "writer", "--model", "openai:gpt-5"])

    assert exit_code == 0
    manifest = (target / "kinby.toml").read_text(encoding="utf-8")
    assert '[package]\nid = "writer"' in manifest
    assert 'distribution = "kinby-writer"' in manifest
    assert 'version = "1.4.2"' in manifest
    assert '[feedback]\nask = "off"' in manifest
    assert (target / "SYSTEM.md").read_text(encoding="utf-8") == "Write clearly.\n"
    assert (target / "factory.toml").read_text(encoding="utf-8") == 'style = "plain"\n'

    assert main(["instance", "show", str(target)]) == 0
    output = capsys.readouterr().out
    assert (
        "package:\n  id: writer\n  distribution: kinby-writer\n  template version: 1.4.2" in output
    )


def test_package_init_refuses_a_nonempty_destination_without_overwriting_it(
    tmp_path,
    monkeypatch,
    capsys,
):
    target = tmp_path / "writer"
    target.mkdir()
    marker = target / "notes.md"
    marker.write_text("keep this\n", encoding="utf-8")
    package = InstalledPackage(
        descriptor=PackageDescriptor(
            id="writer",
            display_name="Writing teammate",
            description="Drafts articles.",
            icon="pen",
            distribution="kinby-writer",
            version="1.4.2",
        ),
        files={"SYSTEM.md": "Write clearly.\n"},
    )
    monkeypatch.setattr(cli_module, "inspect_installed_package", lambda package_id: package)

    exit_code = main(["init", str(target), "--package", "writer"])

    assert exit_code == 1
    assert marker.read_text(encoding="utf-8") == "keep this\n"
    assert sorted(path.name for path in target.iterdir()) == ["notes.md"]
    assert "not empty" in capsys.readouterr().err


def test_failed_package_init_leaves_the_destination_unused_so_retry_can_succeed(
    tmp_path,
    monkeypatch,
    capsys,
):
    descriptor = PackageDescriptor(
        id="writer",
        display_name="Writing teammate",
        description="Drafts articles.",
        icon="pen",
        distribution="kinby-writer",
        version="1.4.2",
    )
    monkeypatch.setattr(
        cli_module,
        "inspect_installed_package",
        lambda package_id: InstalledPackage(
            descriptor=descriptor,
            files={"kinby.toml": 'id = "from-package"\n'},
        ),
    )
    target = tmp_path / "writer"

    exit_code = main(["init", str(target), "--package", "writer"])

    assert exit_code == 1
    assert not target.exists()
    assert 'cannot set "id"' in capsys.readouterr().err

    monkeypatch.setattr(
        cli_module,
        "inspect_installed_package",
        lambda package_id: InstalledPackage(
            descriptor=descriptor,
            files={"SYSTEM.md": "Write clearly.\n"},
        ),
    )

    exit_code = main(["init", str(target), "--package", "writer"])

    assert exit_code == 0
    assert (target / "SYSTEM.md").read_text(encoding="utf-8") == "Write clearly.\n"


@pytest.mark.parametrize(
    ("files", "error"),
    [
        ({"kinby.toml": "[\n"}, "Invalid"),
        ({"workspace/notes.md": "secret\n"}, 'cannot copy "workspace/notes.md"'),
        ({"routines": "not a directory\n"}, 'cannot copy "routines"'),
        ({"kinby.toml": "[[models.extra]]\n"}, "cannot be serialized"),
    ],
)
def test_invalid_package_files_are_rejected_before_creating_the_destination(
    tmp_path,
    monkeypatch,
    capsys,
    files,
    error,
):
    monkeypatch.setattr(
        cli_module,
        "inspect_installed_package",
        lambda package_id: InstalledPackage(
            descriptor=PackageDescriptor(
                id="writer",
                display_name="Writing teammate",
                description="Drafts articles.",
                icon="pen",
                distribution="kinby-writer",
                version="1.4.2",
            ),
            files=files,
        ),
    )
    target = tmp_path / "writer"

    exit_code = main(["init", str(target), "--package", "writer"])

    assert exit_code == 1
    assert not target.exists()
    assert error in capsys.readouterr().err
