import logging
from importlib import import_module
from pathlib import Path

import pytest

from kinby.cli import main


def test_kinby_version_prints_the_package_version(capsys):
    exit_code = main(["--version"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "0.1.0" in captured.out


@pytest.mark.parametrize("command", ["repl", "serve"])
def test_verbose_commands_enable_debug_logging(tmp_path, capsys, command):
    exit_code = main([command, "--verbose", "--instance", str(tmp_path / "missing")])
    logging.getLogger("kinby.test").debug("debug logging enabled")

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "DEBUG kinby.test debug logging enabled" in captured.err.splitlines()


def test_hub_command_passes_explicit_docker_host_mapping(tmp_path, monkeypatch):
    cli_module = import_module("kinby.cli.main")
    received: list[tuple[Path, Path, Path, str]] = []

    async def run_hub(
        directory: Path,
        source: Path,
        docker_host_directory: Path,
        network: str,
    ) -> int:
        received.append((directory, source, docker_host_directory, network))
        return 0

    monkeypatch.setattr(cli_module, "_run_hub", run_hub)
    hub = tmp_path / "hub"
    source = tmp_path / "source"
    host = Path("/srv/kinby")

    exit_code = main(
        [
            "hub",
            str(hub),
            "--source",
            str(source),
            "--docker-host-directory",
            str(host),
            "--network",
            "private-network",
        ]
    )

    assert exit_code == 0
    assert received == [(hub, source, host, "private-network")]
