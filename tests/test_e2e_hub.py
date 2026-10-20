"""The hub calls the e2e pass makes through `.claude/skills/e2e-pass/hub.py`."""

import asyncio
import importlib.util
from pathlib import Path

import pytest

from kinby.contracts import (
    INSTANCE_LIST,
    FactoryRunStatus,
    InstanceListCommand,
    InstanceListResult,
    StepEnding,
    StepResult,
)
from tests.test_factory_runs import FILES, detail, handed_in, settled, started_coder
from tests.test_factory_runs import factory_hub as routed_hub
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


TWO_STEPS = """\
name: checks
instances:
  coder: {}
intake: { instance: coder, routine: scan }
work_item: { issue: int }
steps:
  - id: lint
    kind: command
    in: coder
    run: ["uv run ruff check ."]
  - id: test
    kind: command
    in: coder
    run: ["uv run pytest"]
"""


@pytest.mark.parametrize(
    ("command", "arguments", "status", "steps_run"),
    [
        ("retry", [], FactoryRunStatus.DONE, ["test"]),
        ("send_back", ["lint"], FactoryRunStatus.DONE, ["lint", "test"]),
        ("cancel", [], FactoryRunStatus.CANCELLED, []),
    ],
)
@pytest.mark.parametrize("by_issue", [True, False], ids=["issue", "run-id"])
def test_a_run_that_needs_a_human_moves_on_by_its_issue_or_its_run_id(
    tmp_path, capsys: pytest.CaptureFixture[str], command, arguments, status, steps_run, by_issue
):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.CLEAN),
        StepResult(ending=StepEnding.FAILED),
    ]
    runtime = FakeRuntime()
    hub = routed_hub(tmp_path / "hub", control, runtime, FILES | {"factory.yaml": TWO_STEPS})

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        await settled(hub, run.run_id)
        await settled(hub, (await handed_in(hub, coder, 6)).run_id)
        stopped = len(control.steps)

        reference = 7 if by_issue else run.run_id
        assert await getattr(e2e_hub, command)(hub_client(hub), reference, *arguments) == 0

        finished = await settled(hub, run.run_id)
        assert finished.run.status is status
        assert [sent.origin.step for _, sent in control.steps[stopped:]] == steps_run
        assert str(run.run_id) in capsys.readouterr().out

    asyncio.run(scenario())


def test_an_issue_with_no_unfinished_run_fails_with_a_clear_message(
    tmp_path, capsys: pytest.CaptureFixture[str]
):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = routed_hub(tmp_path / "hub", control, runtime, FILES | {"factory.yaml": TWO_STEPS})

    async def scenario() -> None:
        coder = await started_coder(hub, runtime)
        run = await handed_in(hub, coder, 7)
        assert (await settled(hub, run.run_id)).run.status is FactoryRunStatus.DONE

        assert await e2e_hub.retry(hub_client(hub), 7) == 1

        assert (await detail(hub, run.run_id)).run.status is FactoryRunStatus.DONE
        assert capsys.readouterr().err == "Issue #7 has no unfinished factory run.\n"

    asyncio.run(scenario())
