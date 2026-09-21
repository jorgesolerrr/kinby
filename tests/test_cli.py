import logging
from importlib import import_module
from pathlib import Path
from uuid import UUID

import pytest

from kinby.cli import main
from kinby.contracts import AccessToken
from kinby.hub import (
    HubAccess,
    HubRegistry,
    LifecycleRecovery,
    RecoveredInstance,
    RecoveredState,
)
from kinby.instance import Serve


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
        listen: Serve,
        web_app: Path | None,
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


def test_hub_command_carries_its_listen_address_and_web_app(tmp_path, monkeypatch):
    cli_module = import_module("kinby.cli.main")
    received: list[tuple[Serve, Path | None]] = []

    async def run_hub(
        directory: Path,
        source: Path,
        docker_host_directory: Path,
        network: str,
        listen: Serve,
        web_app: Path | None,
    ) -> int:
        received.append((listen, web_app))
        return 0

    monkeypatch.setattr(cli_module, "_run_hub", run_hub)
    app = tmp_path / "app"

    exit_code = main(
        ["hub", str(tmp_path / "hub"), "--listen", "127.0.0.1:9000", "--web-app", str(app)]
    )
    defaults = main(["hub", str(tmp_path / "hub")])

    assert (exit_code, defaults) == (0, 0)
    assert received == [(Serve("127.0.0.1", 9000), app), (Serve("0.0.0.0", 8080), None)]


def test_a_starting_hub_reports_what_lifecycle_recovery_found(capsys):
    cli_module = import_module("kinby.cli.main")
    instance_id = UUID("11111111-1111-1111-1111-111111111111")

    cli_module._announce_recovery(
        LifecycleRecovery(
            instances=(
                RecoveredInstance(
                    instance_id=instance_id,
                    state=RecoveredState.MISSING,
                    detail="The container is gone. Recreate it to bring it back.",
                ),
            ),
            unknown_containers=("a-stranger",),
        )
    )

    printed = capsys.readouterr().out.splitlines()
    assert printed == [
        f"recovered: {instance_id} missing: The container is gone. Recreate it to bring it back.",
        "unowned container: a-stranger",
    ]


def test_hub_token_rotate_replaces_the_token_and_ends_sessions(tmp_path, capsys):
    directory = tmp_path / "hub"
    access = HubAccess(HubRegistry(directory))
    first = access.issue()
    assert first is not None
    session = access.open_session()

    exit_code = main(["hub", str(directory), "token", "rotate"])

    printed = capsys.readouterr().out.strip().splitlines()[-1]
    assert exit_code == 0
    assert printed != first
    assert access.accepts(first) is False
    assert access.accepts(AccessToken(printed.split()[-1])) is True
    assert access.session_open(session) is False
