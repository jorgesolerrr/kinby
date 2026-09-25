"""`kinby hub update`: the command CI runs to update one instance through a hub."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from kinby.cli import main
from kinby.cli.client import ContractClient
from kinby.cli.contract_socket import CONNECTION_LOST, TOKEN_VARIABLE, contract_client
from kinby.contracts import (
    OPERATION_GET,
    ContractModel,
    ErrorCode,
    ErrorEnvelope,
    Method,
    UpdateToken,
)
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


class _LoseOnePoll:
    """Answer the first ``operation.get`` the way a socket drop does, then the hub."""

    def __init__(self, client: ContractClient) -> None:
        self._client = client
        self._lost = False

    async def call[Command: ContractModel, Result: ContractModel](
        self,
        method: Method[Command, Result],
        command: Command,
    ) -> Result | ErrorEnvelope:
        if method is OPERATION_GET and not self._lost:
            self._lost = True
            return CONNECTION_LOST
        return await self._client.call(method, command)


def test_hub_update_keeps_following_when_a_poll_loses_the_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    hub = hub_at(tmp_path / "hub", images=CandidateImages(), control=FakeControl())
    instance_id = _started(hub)
    monkeypatch.setenv(TOKEN_VARIABLE, hub.access.rotate_update_token())
    monkeypatch.setattr("kinby.cli.hub_update.POLL_SECONDS", 0)
    monkeypatch.setattr("kinby.cli.hub_update.contract_client", _drop_one_poll)

    exit_code = _update(hub, instance_id, "--revision", "v0.2.0")

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "CONNECTION_LOST" not in captured.err
    assert captured.out.splitlines()[-1] == "succeeded: Instance updated."


class _RefusePoll:
    """Answer ``operation.get`` with an error the hub actually sent."""

    def __init__(self, client: ContractClient) -> None:
        self._client = client

    async def call[Command: ContractModel, Result: ContractModel](
        self,
        method: Method[Command, Result],
        command: Command,
    ) -> Result | ErrorEnvelope:
        if method is OPERATION_GET:
            return ErrorEnvelope(
                code=ErrorCode.NOT_FOUND,
                message="No such operation.",
                retryable=False,
            )
        return await self._client.call(method, command)


def test_hub_update_stops_when_the_hub_rejects_the_poll(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    hub = hub_at(tmp_path / "hub", images=CandidateImages(), control=FakeControl())
    instance_id = _started(hub)
    monkeypatch.setenv(TOKEN_VARIABLE, hub.access.rotate_update_token())
    monkeypatch.setattr("kinby.cli.hub_update.contract_client", _refuse_poll)

    exit_code = _update(hub, instance_id, "--revision", "v0.2.0")

    assert exit_code == 1
    assert "NOT_FOUND: No such operation." in capsys.readouterr().err.splitlines()


@asynccontextmanager
async def _drop_one_poll(url: str, token: UpdateToken) -> AsyncIterator[_LoseOnePoll]:
    async with contract_client(url, token) as client:
        yield _LoseOnePoll(client)


@asynccontextmanager
async def _refuse_poll(url: str, token: UpdateToken) -> AsyncIterator[_RefusePoll]:
    async with contract_client(url, token) as client:
        yield _RefusePoll(client)


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


def test_hub_update_sends_the_image_recipe_of_the_pinned_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    images = CandidateImages(package=writer_package())
    hub = hub_at(tmp_path / "hub", images=images, control=FakeControl())
    created = asyncio.run(started_writer(hub_client(hub), hub))
    monkeypatch.setenv(TOKEN_VARIABLE, hub.access.rotate_update_token())
    recipe = tmp_path / "recipe.Dockerfile"
    recipe.write_text("RUN install-bun\n", encoding="utf-8")

    exit_code = _update(
        hub,
        str(created.instance_id),
        "--revision",
        "v0.2.0",
        "--package",
        "writer",
        "--package-commit",
        NEXT_COMMIT,
        "--image-recipe",
        str(recipe),
    )

    assert exit_code == 0
    package = images.selections[-1].package
    assert package is not None
    assert package.image_recipe == "RUN install-bun\n"


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


def test_hub_update_takes_an_image_recipe_only_with_a_package_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(TOKEN_VARIABLE, "token")
    recipe = tmp_path / "recipe.Dockerfile"
    recipe.write_text("RUN install-bun\n", encoding="utf-8")

    with pytest.raises(SystemExit) as unpinned:
        main(
            [
                *("hub", "update", "--connect", "http://127.0.0.1:1/ws", "0" * 32),
                *("--revision", "v0.2.0", "--image-recipe", str(recipe)),
            ]
        )

    assert unpinned.value.code == 2
    assert "--image-recipe" in capsys.readouterr().err
