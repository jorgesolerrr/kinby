import logging

import pytest

from kinby.cli import main


def test_kinby_version_prints_the_package_version(capsys):
    exit_code = main(["--version"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "0.1.0" in captured.out


@pytest.mark.parametrize("command", ["run", "serve"])
def test_verbose_commands_enable_debug_logging(tmp_path, capsys, command):
    exit_code = main([command, "--verbose", "--instance", str(tmp_path / "missing")])
    logging.getLogger("kinby.test").debug("debug logging enabled")

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "DEBUG kinby.test debug logging enabled" in captured.err.splitlines()
