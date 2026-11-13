"""A line's step names a check its workbench runs once the step ends, and a failed step runs again
with what failed, through the hub contract."""

import asyncio
from pathlib import Path

from kinby.contracts import FactoryRunStatus, StepEnding, StepResult
from kinby.hub import Hub
from tests.test_factory_runs import settled
from tests.test_hub import FakeControl, FakeRuntime, hub_at, hub_client
from tests.test_hub_factories import write_factory
from tests.test_hub_line_runs import _handed_in, _line
from tests.test_hub_lines import _home

DRAFTS = {
    "factory.yaml": """\
name: tickets
requires: { image: coder }
work_item: { issue: int }
steps:
  - id: draft
    kind: command
    run: ["make draft"]
    check: drafted
    results: { pages: int }
    outcomes: { published: done }
    failed: { retry: 2 }
  - id: publish
    kind: command
    run: ["make publish"]
""",
    "hooks/drafted.py": """\
from kinby.plugins.hooks import HookResult, StepEnd, hook


@hook
def drafted(end: StepEnd) -> HookResult:
    return HookResult(values={"pages": 3})
""",
}


def _hub(tmp_path: Path, control: FakeControl, runtime: FakeRuntime) -> Hub:
    write_factory(tmp_path / "hub" / "factories" / "tickets", DRAFTS)
    return hub_at(
        tmp_path / "hub",
        runtime=runtime,
        control=control,
        shipped_factories=tmp_path / "shipped",
    )


def _failed(summary: str) -> StepResult:
    return StepResult(ending=StepEnding.FAILED, summary=summary)


def test_a_step_names_its_check_and_the_run_follows_the_outcome_it_records(tmp_path):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.CLEAN, values={"pages": 3}, outcome="published")
    ]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)

        run = await _handed_in(client, line, 12)
        done = await settled(hub, run.run_id)

        assert done.run.status is FactoryRunStatus.DONE
        assert [(command.origin.step, command.hook) for _, command in control.steps] == [
            ("draft", "drafted")
        ]
        assert [attempt.values for attempt in done.attempts] == [{"pages": 3}]

    asyncio.run(scenario())


def test_a_failed_step_runs_again_reading_what_failed_as_failure(tmp_path):
    control = FakeControl()
    control.step_results = [_failed("The draft has no title."), StepResult(ending=StepEnding.CLEAN)]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)

        run = await _handed_in(client, line, 12)
        done = await settled(hub, run.run_id)

        assert done.run.status is FactoryRunStatus.DONE
        assert [(command.origin.step, command.results) for _, command in control.steps][:2] == [
            ("draft", {}),
            ("draft", {"failure": "The draft has no title."}),
        ]
        assert control.steps[2][1].origin.step == "publish"

    asyncio.run(scenario())


def test_a_failed_step_runs_at_most_its_retries_more_then_needs_a_human(tmp_path):
    control = FakeControl()
    control.step_results = [_failed("First."), _failed("Second."), _failed("Third.")]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)

        run = await _handed_in(client, line, 12)
        stopped = await settled(hub, run.run_id)

        assert (stopped.run.status, stopped.run.step) == (FactoryRunStatus.NEEDS_HUMAN, "draft")
        assert [command.results for _, command in control.steps] == [
            {},
            {"failure": "First."},
            {"failure": "Second."},
        ]

    asyncio.run(scenario())


def test_a_timed_out_step_never_runs_again_under_failed(tmp_path):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.TIMED_OUT, summary="Too slow.")]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)

        run = await _handed_in(client, line, 12)
        stopped = await settled(hub, run.run_id)

        assert stopped.run.status is FactoryRunStatus.NEEDS_HUMAN
        assert len(control.steps) == 1

    asyncio.run(scenario())
