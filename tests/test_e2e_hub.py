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
from kinby.hub import ImageSelection
from tests.test_factory_runs import (
    FILES,
    detail,
    factory_hub,
    handed_in,
    settled,
    started_home,
    started_line,
)
from tests.test_hub import (
    FakeControl,
    FakeImages,
    FakeRuntime,
    created_instance,
    hub_at,
    hub_client,
    instance_environment,
)
from tests.test_hub_notices import BUILT, ROUTE_MISSING, _rewrite_environment
from tests.test_hub_workbenches import started

HUB_SCRIPT = Path(__file__).parents[1] / ".claude" / "skills" / "e2e-pass" / "hub.py"
spec = importlib.util.spec_from_file_location("e2e_hub", HUB_SCRIPT)
assert spec is not None and spec.loader is not None
e2e_hub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e_hub)


def test_an_update_gives_an_instance_on_the_revision_its_missing_hub_route(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr("kinby.cli.hub_update.POLL_SECONDS", 0)
    runtime = FakeRuntime()
    hub = factory_hub(tmp_path / "hub", FakeControl(), runtime)

    async def scenario() -> None:
        client = hub_client(hub)
        alice = (await created_instance(client)).instance_id
        bob = (await created_instance(client, name="bob")).instance_id
        environment = instance_environment(hub, alice)
        del environment["KINBY_HUB_URL"]
        _rewrite_environment(hub, alice, environment)
        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert isinstance(listed, InstanceListResult)
        assert {i.instance_id: i.notices for i in listed.instances} == {
            alice: [ROUTE_MISSING],
            bob: [],
        }

        assert await e2e_hub.update(client, BUILT, []) == 0

        listed = await client.call(INSTANCE_LIST, InstanceListCommand())
        assert isinstance(listed, InstanceListResult)
        assert [i.notices for i in listed.instances] == [[], []]
        out = capsys.readouterr().out
        assert f"{alice} alice: updating to {BUILT[:12]}" in out
        assert f"{bob} bob: already on {BUILT[:12]}" in out

    asyncio.run(scenario())


def test_an_update_moves_every_workbench_onto_the_revision(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr("kinby.cli.hub_update.POLL_SECONDS", 0)
    images = FakeImages()
    runtime = FakeRuntime()
    hub = hub_at(tmp_path / "hub", images=images, runtime=runtime, control=FakeControl())

    async def scenario() -> None:
        client = hub_client(hub)
        workbench_id = await started(client, runtime)

        assert await e2e_hub.update(client, "v2", []) == 0

        assert images.selections[-1] == ImageSelection("v2", recipe="coder")
        assert f"{workbench_id} cec-backend: updating to v2" in capsys.readouterr().out

    asyncio.run(scenario())


TWO_STEPS = """\
name: checks
requires: { image: coder }
work_item: { issue: int }
steps:
  - { id: lint, kind: command, run: ["uv run ruff check ."] }
  - { id: test, kind: command, run: ["uv run pytest"] }
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
    hub = factory_hub(tmp_path / "hub", control, runtime, FILES | {"factory.yaml": TWO_STEPS})

    async def scenario() -> None:
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        await settled(hub, run.run_id)
        await settled(hub, (await handed_in(hub, line, 6)).run_id)
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
    hub = factory_hub(tmp_path / "hub", control, runtime, FILES | {"factory.yaml": TWO_STEPS})

    async def scenario() -> None:
        await started_home(hub, runtime)
        line = await started_line(hub, runtime)
        run = await handed_in(hub, line, 7)
        assert (await settled(hub, run.run_id)).run.status is FactoryRunStatus.DONE

        assert await e2e_hub.retry(hub_client(hub), 7) == 1

        assert (await detail(hub, run.run_id)).run.status is FactoryRunStatus.DONE
        assert capsys.readouterr().err == "Issue #7 has no unfinished factory run.\n"

    asyncio.run(scenario())
