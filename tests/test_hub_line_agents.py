"""A line's agent step runs on a coding client engine on its workbench, and the hub reads each
attempt's transcript from there, through the hub contract."""

import asyncio
from datetime import timedelta
from pathlib import Path

from kinby.contracts import (
    FACTORY_RUN_TRANSCRIPT,
    ClientStepRun,
    CodingClient,
    ErrorCode,
    ErrorEnvelope,
    FactoryRunStatus,
    FactoryRunTranscript,
    FactoryRunTranscriptCommand,
    ReasoningEffort,
    RunFolder,
    StepEnding,
    StepResult,
)
from kinby.hub import Hub
from tests.test_factory_runs import settled
from tests.test_factory_waits import limited
from tests.test_hub import FakeControl, FakeRuntime, hub_at, hub_client
from tests.test_hub_factories import write_factory
from tests.test_hub_line_runs import _handed_in, _line
from tests.test_hub_lines import _home

AGENTS = {
    "factory.yaml": """\
name: tickets
requires: { image: coder }
work_item: { issue: int }
steps:
  - id: implement
    kind: agent
    engine: claude
    prompt: prompts/implement.md
    model: claude-opus-5-5
    effort: high
    timeout: 90m
  - id: fix
    kind: agent
    engine: claude
    prompt: prompts/fix.md
    resume: implement
""",
    "prompts/implement.md": "Implement the issue.\n",
    "prompts/fix.md": "Fix what the review found.\n",
}


def _hub(tmp_path: Path, control: FakeControl, runtime: FakeRuntime, files=AGENTS) -> Hub:
    write_factory(tmp_path / "hub" / "factories" / "tickets", files)
    return hub_at(
        tmp_path / "hub", runtime=runtime, control=control, shipped_factories=tmp_path / "shipped"
    )


def test_an_agent_step_runs_its_engine_on_the_workbench_and_a_later_one_resumes_its_session(
    tmp_path,
):
    control = FakeControl()
    control.step_results = [StepResult(ending=StepEnding.CLEAN, session="claude-1")]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)

        run = await _handed_in(client, line, 12)
        done = await settled(hub, run.run_id)

        assert done.run.status is FactoryRunStatus.DONE
        [(_, implement), (_, fix)] = control.steps
        assert implement.step == ClientStepRun(
            client=CodingClient.CLAUDE,
            prompt="Implement the issue.\n",
            timeout_seconds=5400,
            model="claude-opus-5-5",
            effort=ReasoningEffort.HIGH,
        )
        assert implement.folder == RunFolder(branch=f"tickets/issue-12-{run.run_id.hex[:8]}")
        assert fix.step == ClientStepRun(
            client=CodingClient.CLAUDE,
            prompt="Fix what the review found.\n",
            resume="claude-1",
            timeout_seconds=3600,
        )
        assert [(attempt.step, attempt.session) for attempt in done.attempts] == [
            ("implement", "claude-1"),
            ("fix", None),
        ]

    asyncio.run(scenario())


def test_a_limited_agent_step_parks_its_run_until_the_reset_and_spends_no_retry(tmp_path):
    control = FakeControl()
    refused = limited(timedelta(seconds=1))
    control.step_results = [
        refused,
        StepResult(ending=StepEnding.FAILED),
        StepResult(ending=StepEnding.CLEAN, session="claude-1"),
    ]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)

        run = await _handed_in(client, line, 12)
        async with asyncio.timeout(5):
            while (await settled(hub, run.run_id)).run.status is FactoryRunStatus.PARKED:
                await asyncio.sleep(0.01)
        done = await settled(hub, run.run_id)

        assert done.run.status is FactoryRunStatus.DONE
        assert [(a.step, a.attempt, a.ending) for a in done.attempts] == [
            ("implement", 1, StepEnding.LIMITED),
            ("implement", 2, StepEnding.FAILED),
            ("implement", 3, StepEnding.CLEAN),
            ("fix", 1, StepEnding.CLEAN),
        ]
        assert refused.resets_at is not None
        assert done.attempts[1].started_at >= refused.resets_at

    asyncio.run(scenario())


def test_an_agent_step_on_the_kinby_engine_fails_since_no_workbench_runs_it_yet(tmp_path):
    files = AGENTS | {
        "factory.yaml": AGENTS["factory.yaml"]
        .replace("engine: claude", "engine: kinby", 1)
        .replace("    resume: implement\n", "")
    }
    control = FakeControl()
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime, files)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)

        run = await _handed_in(client, line, 12)
        stopped = await settled(hub, run.run_id)

        assert (stopped.run.status, stopped.run.step) == (FactoryRunStatus.NEEDS_HUMAN, "implement")
        assert stopped.attempts[-1].summary == (
            'Step "implement" runs on the kinby engine, which no workbench runs yet.'
        )
        assert control.steps == []

    asyncio.run(scenario())


def test_the_hub_reads_an_attempts_transcript_from_the_runs_workbench(tmp_path):
    control = FakeControl()
    control.transcript = FactoryRunTranscript(events=[{"type": "result"}], cursor=None)
    control.step_results = [StepResult(ending=StepEnding.CLEAN, session="claude-1")]
    runtime = FakeRuntime()
    hub = _hub(tmp_path, control, runtime)
    client = hub_client(hub)

    async def scenario() -> None:
        await _home(hub)
        line = await _line(client, runtime)
        run = await _handed_in(client, line, 12)
        await settled(hub, run.run_id)
        asked = FactoryRunTranscriptCommand(run_id=run.run_id, step="fix", attempt=1)

        read = await client.call(FACTORY_RUN_TRANSCRIPT, asked)
        missing = await client.call(FACTORY_RUN_TRANSCRIPT, asked.model_copy(update={"attempt": 2}))

        assert read == control.transcript
        [(endpoint, command)] = control.transcripts_read
        assert endpoint.address == f"http://kinby-{line.workbench_id}:8787"
        assert command == asked
        assert isinstance(missing, ErrorEnvelope)
        assert missing.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())
