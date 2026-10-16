"""The hub calls the e2e pass makes through `.claude/skills/e2e-pass/hub.py`."""

import asyncio
import importlib.util
from pathlib import Path

import pytest

from kinby.contracts import INSTANCE_LIST, InstanceListCommand, InstanceListResult
from tests.test_factory_runs import factory_hub as routed_hub
from tests.test_factory_runs import started_coder
from tests.test_factory_templates import created_roles, factory_hub
from tests.test_hub import (
    FakeControl,
    FakeRuntime,
    created_instance,
    hub_client,
    instance_environment,
)
from tests.test_hub_notices import BUILT, CODER_BEHIND, ROUTE_MISSING, _edit, _rewrite_environment

HUB_SCRIPT = Path(__file__).parents[1] / ".claude" / "skills" / "e2e-pass" / "hub.py"
spec = importlib.util.spec_from_file_location("e2e_hub", HUB_SCRIPT)
assert spec is not None and spec.loader is not None
e2e_hub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e_hub)


def test_an_update_brings_an_instance_on_the_revision_up_to_its_factorys_template(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr("kinby.cli.hub_update.POLL_SECONDS", 0)

    async def scenario() -> None:
        client = hub_client(factory_hub(tmp_path))
        created = await created_roles(client)
        await _edit(client, {"instances/coder/SYSTEM.md": "You implement issues well.\n"})
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert isinstance(listed, InstanceListResult)
        assert {i.instance_id: i.notices for i in listed.instances}[created["coder"]] == [
            CODER_BEHIND
        ]

        assert await e2e_hub.update(client, BUILT, []) == 0

        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert isinstance(listed, InstanceListResult)
        assert [i.notices for i in listed.instances] == [[], []]
        out = capsys.readouterr().out
        assert f"{created['coder']} tickets-coder: updating to {BUILT[:12]}" in out
        assert f"{created['reviewer']} tickets-reviewer: already on {BUILT[:12]}" in out

    asyncio.run(scenario())


def test_an_update_gives_an_instance_on_the_revision_its_missing_hub_route(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr("kinby.cli.hub_update.POLL_SECONDS", 0)
    runtime = FakeRuntime()
    hub = routed_hub(tmp_path / "hub", FakeControl(), runtime)

    async def scenario() -> None:
        client = hub_client(hub)
        coder = await started_coder(hub, runtime)
        alice = (await created_instance(client)).instance_id
        environment = instance_environment(hub, coder)
        del environment["KINBY_HUB_URL"]
        _rewrite_environment(hub, coder, environment)
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert isinstance(listed, InstanceListResult)
        assert {i.instance_id: i.notices for i in listed.instances} == {
            coder: [ROUTE_MISSING],
            alice: [],
        }

        assert await e2e_hub.update(client, BUILT, []) == 0

        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert isinstance(listed, InstanceListResult)
        assert [i.notices for i in listed.instances] == [[], []]
        out = capsys.readouterr().out
        assert f"{coder} checks-coder: updating to {BUILT[:12]}" in out
        assert f"{alice} alice: already on {BUILT[:12]}" in out

    asyncio.run(scenario())
