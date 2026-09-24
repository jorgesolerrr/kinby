"""`kinby hub update`: the command CI runs to update one instance through a hub."""

import asyncio
from pathlib import Path

import pytest

from kinby.cli import main
from kinby.cli.contract_socket import TOKEN_VARIABLE
from kinby.hub import Hub
from tests.test_hub import FakeControl, hub_at, hub_client, started_instance
from tests.test_hub_server import served, url
from tests.test_hub_update import (
    FIRST_COMMIT,
    NEXT_COMMIT,
    CandidateImages,
    started_writer,
    writer_at,
    writer_package,
)


def _update(hub: Hub, *arguments: str) -> int:
    """Run the command against the hub, the way CI does, while the hub keeps serving."""

    async def scenario() -> int:
        async with served(hub) as address:
            return await asyncio.to_thread(
                main,
                ["hub", "update", "--connect", url(address, "/ws"), *arguments],
            )

    return asyncio.run(scenario())


def _started(hub: Hub) -> str:
    return str(asyncio.run(started_instance(hub_client(hub), hub)).instance_id)


def test_hub_update_follows_the_update_and_prints_its_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    hub = hub_at(tmp_path / "hub", images=CandidateImages(), control=FakeControl())
    instance_id = _started(hub)
    monkeypatch.setenv(TOKEN_VARIABLE, hub.access.rotate_update_token())

    exit_code = _update(hub, instance_id, "--revision", "v0.2.0")

    printed = capsys.readouterr().out.splitlines()
    assert exit_code == 0
    assert "image: Preparing the image v0.2.0 selects." in printed
    assert [line.split(":", 1)[0] for line in printed] == [
        "validate",
        "image",
        "replace",
        "probe",
        "drain",
        "result",
        "container",
        "remove",
        "create",
        "start",
        "ready",
        "succeeded",
    ]
    assert printed[-1] == "succeeded: Instance updated."


def test_hub_update_exits_non_zero_when_the_update_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    images = CandidateImages()
    hub = hub_at(tmp_path / "hub", images=images, control=FakeControl())
    instance_id = _started(hub)
    images.failure = "no such revision: v9.9.9"
    monkeypatch.setenv(TOKEN_VARIABLE, hub.access.rotate_update_token())

    exit_code = _update(hub, instance_id, "--revision", "v9.9.9")

    captured = capsys.readouterr()
    assert exit_code == 1
    assert [line.split(":", 1)[0] for line in captured.out.splitlines()] == ["validate", "image"]
    assert "failed: no such revision: v9.9.9" in captured.err.splitlines()


def test_hub_update_pins_the_package_to_a_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    images = CandidateImages(package=writer_package())
    hub = hub_at(tmp_path / "hub", images=images, control=FakeControl())
    created = asyncio.run(started_writer(hub_client(hub), hub))
    monkeypatch.setenv(TOKEN_VARIABLE, hub.access.rotate_update_token())

    exit_code = _update(
        hub,
        str(created.instance_id),
        "--revision",
        "v0.2.0",
        "--package",
        "writer",
        "--package-commit",
        NEXT_COMMIT,
    )

    assert exit_code == 0
    assert images.selections[-1].package == writer_at(NEXT_COMMIT)
    assert images.selections[0].package == writer_at(FIRST_COMMIT)


def test_hub_update_reports_a_refused_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    hub = hub_at(tmp_path / "hub", images=CandidateImages(), control=FakeControl())
    instance_id = _started(hub)
    monkeypatch.setenv(TOKEN_VARIABLE, hub.access.rotate_update_token())

    exit_code = _update(
        hub,
        instance_id,
        "--revision",
        "v0.2.0",
        "--package",
        "writer",
        "--package-commit",
        NEXT_COMMIT,
    )

    assert exit_code == 1
    refusals = capsys.readouterr().err.splitlines()
    assert any(line.startswith("INVALID_ARGUMENT: ") for line in refusals)


def test_hub_update_stops_when_the_hub_refuses_the_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    hub = hub_at(tmp_path / "hub", images=CandidateImages(), control=FakeControl())
    instance_id = _started(hub)
    monkeypatch.setenv(TOKEN_VARIABLE, "not-the-update-token")

    exit_code = _update(hub, instance_id, "--revision", "v0.2.0")

    assert exit_code == 1
    assert "rejected the token" in capsys.readouterr().err


def test_hub_update_needs_the_token_and_a_package_for_its_commit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = ["hub", "update", "--connect", "http://127.0.0.1:1/ws", "0" * 32]
    monkeypatch.delenv(TOKEN_VARIABLE, raising=False)

    tokenless = main([*arguments, "--revision", "v0.2.0"])
    missing_token = capsys.readouterr().err
    monkeypatch.setenv(TOKEN_VARIABLE, "token")
    with pytest.raises(SystemExit) as packageless:
        main([*arguments, "--revision", "v0.2.0", "--package-commit", NEXT_COMMIT])

    assert tokenless == 1
    assert TOKEN_VARIABLE in missing_token
    assert packageless.value.code == 2
    assert "--package" in capsys.readouterr().err
