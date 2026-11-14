"""A line carries the model its agent steps take when they name none, and a daily budget its runs'
usage counts toward, through the hub contract."""

import asyncio
from pathlib import Path
from uuid import uuid4

from kinby.cli.client import ContractClient
from kinby.contracts import (
    FACTORY_RUN_GET,
    LINE_GET,
    LINE_SETUP,
    LINE_UPDATE,
    STATS_RUNS,
    ApiUse,
    ClientStepRun,
    DelegatedRun,
    DelegatedRunOutcome,
    ErrorCode,
    ErrorEnvelope,
    FactoryRunDetail,
    FactoryRunGetCommand,
    FactoryRunStatus,
    Line,
    LineGetCommand,
    LineSetupCommand,
    LineSetupResult,
    LineUpdateCommand,
    StatsRunsCommand,
    StatsRunsResult,
    StepEnding,
    StepResult,
    StepUsage,
    SubscriptionUse,
    UsageSource,
)
from kinby.hub import ControlUnreachable, Hub
from tests.test_factory_runs import settled
from tests.test_hub import FakeControl, FakeRuntime, hub_at, hub_client
from tests.test_hub_factories import write_factory
from tests.test_hub_line_runs import _handed_in, _line
from tests.test_hub_lines import _home

MODELS = {
    "factory.yaml": """\
name: tickets
requires: { image: coder }
work_item: { issue: int }
steps:
  - id: draft
    kind: agent
    engine: claude
    prompt: prompts/step.md
  - id: review
    kind: agent
    engine: codex
    prompt: prompts/step.md
  - id: polish
    kind: agent
    engine: claude
    prompt: prompts/step.md
    model: claude-opus-5-5
""",
    "prompts/step.md": "Do the step.\n",
}
#: Two agent steps, the second continuing the first's session.
DRAFTS = {
    "factory.yaml": """\
name: {name}
requires: {{ image: coder }}
work_item: {{ issue: int }}
steps:
  - id: draft
    kind: agent
    engine: claude
    prompt: prompts/step.md
  - id: review
    kind: agent
    engine: claude
    prompt: prompts/step.md
    resume: draft
""",
    "prompts/step.md": "Do the step.\n",
}


def _hub(tmp_path: Path, control: FakeControl, runtime: FakeRuntime, *factories: str) -> Hub:
    for name in factories:
        drafts = DRAFTS | {"factory.yaml": DRAFTS["factory.yaml"].format(name=name)}
        write_factory(
            tmp_path / "hub" / "factories" / name, MODELS if name == "tickets" else drafts
        )
    return hub_at(
        tmp_path / "hub", runtime=runtime, control=control, shipped_factories=tmp_path / "shipped"
    )


async def _also(
    client: ContractClient, line: Line, factory: str, usd_per_day: float | None = None
) -> Line:
    """Another line of *factory* on the workbench of *line*."""
    command = LineSetupCommand(
        factory=factory, workbench_id=line.workbench_id, usd_per_day=usd_per_day
    )
    added = await client.call(LINE_SETUP, command)
    assert isinstance(added, LineSetupResult), added
    return added.line


async def _updated(
    client: ContractClient, line: Line, model: str | None = None, usd_per_day: float | None = None
) -> Line:
    command = LineUpdateCommand(line_id=line.line_id, model=model, usd_per_day=usd_per_day)
    updated = await client.call(LINE_UPDATE, command)
    assert isinstance(updated, Line), updated
    return updated


def _usage(cost: float | None = None, session: str | None = None, tokens: int = 0) -> StepUsage:
    """What an attempt spent: its model calls on the API at *cost*, and a Claude run of *tokens*
    input tokens in *session*."""
    ran = DelegatedRun(
        usage_source=UsageSource.CLAUDE_SUBSCRIPTION,
        client="claude-code",
        models=["claude-sonnet-5"],
        input_tokens=tokens,
        output_tokens=tokens // 10,
        duration_ms=1000,
        client_turns=2,
        outcome=DelegatedRunOutcome.COMPLETED,
        session=session,
    )
    return StepUsage(
        api=ApiUse(input_tokens=100, output_tokens=10, cost=cost),
        delegated_run=ran if tokens else None,
    )


def test_an_agent_step_naming_no_model_takes_the_lines_when_its_engine_runs_that_provider(
    tmp_path,
):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, "tickets")
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _updated(
            client, await _line(client, runtime), model="anthropic:claude-sonnet-5"
        )

        await settled(hub, (await _handed_in(client, line, 12)).run_id)

        steps = [command.step for _, command in control.steps]
        assert [step.model for step in steps if isinstance(step, ClientStepRun)] == [
            "claude-sonnet-5",
            None,
            "claude-opus-5-5",
        ]

    asyncio.run(scenario())


def test_a_line_update_changes_its_model_and_budget(tmp_path):
    control = FakeControl()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, "tickets")
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)

        updated = await _updated(client, line, model="openai:gpt-5.5", usd_per_day=12.5)
        cleared = await _updated(client, line)
        missing = await client.call(LINE_UPDATE, LineUpdateCommand(line_id=uuid4()))

        assert updated == line.model_copy(update={"model": "openai:gpt-5.5", "usd_per_day": 12.5})
        assert cleared == line
        assert await client.call(LINE_GET, LineGetCommand(line_id=line.line_id)) == line
        assert isinstance(missing, ErrorEnvelope)
        assert missing.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_the_usage_of_a_runs_steps_is_reported_per_line(tmp_path):
    control = FakeControl()
    control.step_results = [
        StepResult(
            ending=StepEnding.CLEAN, session="claude-1", usage=_usage(0.5, "claude-1", 1000)
        ),
        StepResult(ending=StepEnding.CLEAN, usage=_usage(None, "claude-1", 1500)),
        StepResult(ending=StepEnding.CLEAN, usage=_usage(0.25)),
    ]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, "tickets", "chores", "errands")
    client = hub_client(hub)

    async def scenario() -> None:
        home = await _home(hub)
        control.usage[f"http://kinby-{home}:8787"] = ControlUnreachable("Home is busy.")
        line = await _line(client, runtime)
        chores = await _also(client, line, "chores")
        errands = await _also(client, line, "errands")
        run = await _handed_in(client, chores, 12)
        await settled(hub, run.run_id)
        await settled(hub, (await _handed_in(client, errands, 12)).run_id)

        usage = await client.call(STATS_RUNS, StatsRunsCommand(line_id=chores.line_id))
        detail = await client.call(FACTORY_RUN_GET, FactoryRunGetCommand(run_id=run.run_id))

        assert isinstance(usage, StatsRunsResult), usage
        assert isinstance(detail, FactoryRunDetail), detail
        [reported] = usage.runs
        assert reported.run.run_id == run.run_id
        assert reported.api == ApiUse(input_tokens=200, output_tokens=20, cost=0.5)
        assert reported.subscriptions == [
            SubscriptionUse(
                usage_source=UsageSource.CLAUDE_SUBSCRIPTION,
                runs=2,
                input_tokens=1500,
                output_tokens=150,
                duration_ms=2000,
            ),
            SubscriptionUse(usage_source=UsageSource.CHATGPT_SUBSCRIPTION),
        ]
        assert reported.tokens == 1870
        review = detail.attempts[-1].usage
        assert review is not None and review.delegated_run is not None
        assert (review.delegated_run.input_tokens, review.delegated_run.output_tokens) == (500, 50)

    asyncio.run(scenario())


def test_once_a_lines_cost_of_the_day_reaches_its_budget_its_next_agent_step_stops(tmp_path):
    control = FakeControl()
    control.step_results = [
        StepResult(ending=StepEnding.CLEAN, usage=_usage(2.0)),
        StepResult(ending=StepEnding.CLEAN, session="claude-2"),
    ]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, "tickets", "chores", "errands")
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)
        chores = await _also(client, line, "chores", usd_per_day=1.5)
        errands = await _also(client, line, "errands", usd_per_day=1.5)

        stopped = await settled(hub, (await _handed_in(client, chores, 12)).run_id)
        after = await settled(hub, (await _handed_in(client, chores, 13)).run_id)
        other = await settled(hub, (await _handed_in(client, errands, 12)).run_id)

        assert (stopped.run.status, stopped.run.step) == (FactoryRunStatus.NEEDS_HUMAN, "review")
        refused = "The daily cost reached the usd_per_day budget of 1.5."
        assert [(a.step, a.ending, a.summary) for a in stopped.attempts] == [
            ("draft", StepEnding.CLEAN, ""),
            ("review", StepEnding.FAILED, refused),
            ("review", StepEnding.FAILED, refused),
        ]
        assert (after.run.status, after.run.step) == (FactoryRunStatus.NEEDS_HUMAN, "draft")
        assert other.run.status is FactoryRunStatus.DONE
        assert [command.origin.run_id for _, command in control.steps] == [
            stopped.run.run_id,
            other.run.run_id,
            other.run.run_id,
        ]

    asyncio.run(scenario())
