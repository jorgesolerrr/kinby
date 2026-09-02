from kinby.cli import main


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
    assert (target / "routines" / "README.md").read_text(encoding="utf-8").startswith("<!--")


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
