"""`kinby hub adopt`: preview and run an adoption from a shell, the way the operator does."""

import asyncio
import json
from pathlib import Path

import pytest

from kinby.cli import main
from kinby.cli.contract_socket import TOKEN_VARIABLE
from kinby.contracts import IntendedState
from kinby.hub import Hub
from tests.test_hub import FakeControl, FakeImages, FakeRuntime, hub_at
from tests.test_hub_adoption import CODER_CONTAINER, FACTORY, declare_package, existing_coder
from tests.test_hub_server import served, url


def _adopt(hub: Hub, *arguments: str) -> int:
    async def scenario() -> int:
        async with served(hub) as address:
            return await asyncio.to_thread(
                main,
                ["hub", "adopt", "--connect", url(address, "/ws"), *arguments],
            )

    return asyncio.run(scenario())


def test_hub_adopt_previews_then_adopts_with_the_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = FakeRuntime()
    hub = hub_at(tmp_path / "hub", runtime=runtime, images=FakeImages(), control=FakeControl())
    directory = existing_coder(runtime, tmp_path / "box" / "coder")
    declare_package(directory)
    package = tmp_path / "coder-package.json"
    package.write_text(FACTORY.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv(TOKEN_VARIABLE, hub.access.rotate())
    arguments = (str(directory), CODER_CONTAINER, "--package", str(package))

    blocked = _adopt(hub, *arguments, "--preview")
    blocked_preview = json.loads(capsys.readouterr().out)
    previewed = _adopt(hub, *arguments, "--relinquished", "--preview")
    preview = json.loads(capsys.readouterr().out)
    adopted = _adopt(hub, *arguments, "--relinquished", "--claim-signals")
    printed = capsys.readouterr().out.splitlines()

    assert blocked == 1
    assert [finding["kind"] for finding in blocked_preview["findings"]] == ["previous-manager"]
    assert previewed == 0
    assert preview["manifest_id"] == "coder"
    assert adopted == 0
    assert printed[-1] == "succeeded: The hub owns this instance."
    (record,) = hub.registry.managed_instances()
    assert record.package == FACTORY
    assert record.intended_state is IntendedState.RUNNING
    assert hub.registry.signal_alias() == record.instance_id


def test_hub_adopt_needs_the_access_token(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(TOKEN_VARIABLE, raising=False)

    exit_code = main(
        ["hub", "adopt", "--connect", "http://127.0.0.1:1/ws", "/instance", CODER_CONTAINER]
    )

    assert exit_code == 1
    assert TOKEN_VARIABLE in capsys.readouterr().err
