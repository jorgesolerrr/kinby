import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk
from pydantic import ValidationError

from kinby.cli import main
from kinby.contracts import (
    AcceptedResult,
    ContractModel,
    DelegatedRun,
    DelegatedRunOutcome,
    Event,
    RunDelegated,
    Scope,
    StatsGetResult,
    ThreadCreateResult,
    TurnCompleted,
    UsageGetResult,
    UsageSource,
    is_turn_closing,
)
from kinby.core.clock import utc_now
from kinby.core.dispatcher import Dispatcher, ScheduledTurnConfig, TurnConfig, build_dispatcher
from kinby.core.errors import NoActiveTurn
from kinby.core.events import EventLog
from kinby.core.scheduler import SchedulerConfig
from kinby.core.turn_runner import LangGraphRunner
from kinby.instance import Instance, init_instance, load_instance
from kinby.plugins import ToolContext
from tests.helpers import thread_events
from tests.test_gate import ScriptedModel
from tests.test_routines import RoutineModel, instance_at, routine_file

#: Tool contexts a code step hands out, to report through once its turn has closed.
LEAKED_CONTEXTS: list[ToolContext] = []

COMPLETED_RUN = DelegatedRun(
    usage_source=UsageSource.CLAUDE_SUBSCRIPTION,
    client="claude-code",
    models=["claude-opus-5-5", "claude-haiku-4-5"],
    input_tokens=1200,
    output_tokens=300,
    cache_read_tokens=800,
    cache_creation_tokens=100,
    duration_ms=42_000,
    client_turns=7,
    outcome=DelegatedRunOutcome.COMPLETED,
)


FAILED_RUN = DelegatedRun(
    usage_source=UsageSource.CHATGPT_SUBSCRIPTION,
    client="codex",
    models=["gpt-5-codex"],
    input_tokens=500,
    output_tokens=20,
    duration_ms=3_000,
    client_turns=1,
    outcome=DelegatedRunOutcome.FAILED,
)
LIMITED_RUN = DelegatedRun(
    usage_source=UsageSource.CLAUDE_SUBSCRIPTION,
    client="claude-code",
    models=["claude-opus-5-5"],
    input_tokens=90,
    output_tokens=0,
    duration_ms=800,
    client_turns=0,
    outcome=DelegatedRunOutcome.LIMITED,
    resets_at=datetime(2026, 9, 25, 18, 40, tzinfo=UTC),
)


def code_step(
    instance: Instance,
    *runs: DelegatedRun,
    result: str = "None",
    frontmatter: str = "description: News",
) -> None:
    """Write routine ``news`` whose code step reports *runs* and returns *result*."""
    path = routine_file(instance, frontmatter)
    reports = "".join(
        f"    await context.report_run(DelegatedRun.model_validate_json({run.model_dump_json()!r}))"
        "\n"
        for run in runs
    )
    (path.parent / "run.py").write_text(
        "from kinby.contracts import DelegatedRun\n"
        "from kinby.plugins import ToolContext, tool\n"
        "@tool(write=False)\n"
        "async def code(context: ToolContext) -> str | None:\n"
        '    """Delegate the news to an outside agent."""\n'
        f"{reports}"
        f"    return {result}\n"
    )


def runtime(
    instance: Instance,
    model: RoutineModel | ScriptedModel | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> Dispatcher:
    log = EventLog(instance.manifest.state_dir, clock=clock)
    runner = LangGraphRunner(
        instance, event_log=log, model_factory=lambda _: model or RoutineModel()
    )
    return build_dispatcher(
        instance.manifest.state_dir,
        event_log=log,
        turns=ScheduledTurnConfig(
            TurnConfig(runner.prepare_for_turn, runner.permission_ceiling, runner),
            SchedulerConfig(instance),
        ),
    )


async def call(dispatcher: Dispatcher, method: str, **payload: object) -> ContractModel:
    return await dispatcher.dispatch(method, payload, set(Scope))


async def fire_routine(dispatcher: Dispatcher) -> list[Event]:
    accepted = await call(dispatcher, "routine.run", name="news")
    assert isinstance(accepted, AcceptedResult)
    events: list[Event] = []
    stream = await thread_events(dispatcher, {"thread_id": accepted.thread_id}, set(Scope))
    async with asyncio.timeout(5):
        async for event in stream:
            assert isinstance(event, Event)
            events.append(event)
            if is_turn_closing(event.payload):
                break
    await stream.aclose()
    return events


def test_a_code_step_reports_a_completed_run_that_usage_get_lists_under_its_turn(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        code_step(instance, COMPLETED_RUN)
        dispatcher = runtime(instance)

        events = await fire_routine(dispatcher)
        usage = await call(dispatcher, "usage.get")

        assert isinstance(usage, UsageGetResult)
        [thread] = usage.threads
        [turn] = thread.turns
        assert turn.turn_id == events[0].turn_id
        assert [reported.run for reported in turn.delegated_runs] == [COMPLETED_RUN]
        assert (turn.input_tokens, turn.output_tokens) == (0, 0)
        assert (thread.input_tokens, thread.output_tokens) == (0, 0)

    asyncio.run(scenario())


def test_completed_failed_and_limited_runs_are_events_in_the_turn_and_listed_in_order(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        code_step(instance, COMPLETED_RUN, FAILED_RUN, LIMITED_RUN)
        dispatcher = runtime(instance)

        events = await fire_routine(dispatcher)
        usage = await call(dispatcher, "usage.get")

        reported = [event for event in events if isinstance(event.payload, RunDelegated)]
        assert [event.payload for event in reported] == [
            RunDelegated(run=COMPLETED_RUN),
            RunDelegated(run=FAILED_RUN),
            RunDelegated(run=LIMITED_RUN),
        ]
        assert {event.turn_id for event in reported} == {events[0].turn_id}
        assert isinstance(usage, UsageGetResult)
        listed = usage.threads[0].turns[0].delegated_runs
        assert [run.timestamp for run in listed] == [event.timestamp for event in reported]
        assert [run.run for run in listed] == [COMPLETED_RUN, FAILED_RUN, LIMITED_RUN]

    asyncio.run(scenario())


class TickingClock:
    """Stamp each event one minute after the last."""

    def __init__(self) -> None:
        self.now = datetime(2026, 9, 25, 9, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(minutes=1)
        return self.now


def test_usage_get_places_a_run_by_its_own_timestamp_not_its_turns_close(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        code_step(instance, COMPLETED_RUN, FAILED_RUN)
        dispatcher = runtime(instance, clock=TickingClock())

        events = await fire_routine(dispatcher)
        first, second = (event for event in events if isinstance(event.payload, RunDelegated))
        closed_at = events[-1].timestamp
        usage = await call(dispatcher, "usage.get", since=second.timestamp, until=closed_at)

        assert first.timestamp < second.timestamp < closed_at
        assert isinstance(usage, UsageGetResult)
        [turn] = usage.threads[0].turns
        assert [listed.run for listed in turn.delegated_runs] == [FAILED_RUN]

    asyncio.run(scenario())


def test_a_tool_context_without_a_turn_refuses_a_report(tmp_path: Path) -> None:
    context = ToolContext(instance=instance_at(tmp_path), thread_id=uuid4())

    with pytest.raises(NoActiveTurn):
        asyncio.run(context.report_run(COMPLETED_RUN))


def test_a_report_after_an_interrupt_is_refused_and_records_nothing(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        path = routine_file(instance, "description: News")
        (path.parent / "run.py").write_text(
            "import asyncio\n"
            "from kinby.plugins import ToolContext, tool\n"
            "from tests.test_delegated_runs import LEAKED_CONTEXTS\n"
            "@tool(write=False)\n"
            "async def code(context: ToolContext) -> None:\n"
            '    """Keep the context past an interrupt."""\n'
            "    LEAKED_CONTEXTS.append(context)\n"
            "    await asyncio.Event().wait()\n"
        )
        dispatcher = runtime(instance)
        accepted = await call(dispatcher, "routine.run", name="news")
        assert isinstance(accepted, AcceptedResult)
        async with asyncio.timeout(5):
            while not LEAKED_CONTEXTS:
                await asyncio.sleep(0)
        leaked = LEAKED_CONTEXTS.pop()

        interrupted = await call(dispatcher, "thread.turn.interrupt", thread_id=accepted.thread_id)
        assert isinstance(interrupted, AcceptedResult)

        with pytest.raises(NoActiveTurn):
            await leaked.report_run(COMPLETED_RUN)
        await asyncio.sleep(0)
        with pytest.raises(NoActiveTurn):
            await leaked.report_run(COMPLETED_RUN)

        stream = await thread_events(dispatcher, {"thread_id": accepted.thread_id}, set(Scope))
        events: list[Event] = []
        async with asyncio.timeout(5):
            async for event in stream:
                assert isinstance(event, Event)
                events.append(event)
                if is_turn_closing(event.payload):
                    break
        await stream.aclose()
        assert not any(isinstance(event.payload, RunDelegated) for event in events)

    asyncio.run(scenario())


def test_a_report_after_the_turn_closed_is_refused_and_records_nothing(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        path = routine_file(instance, "description: News")
        (path.parent / "run.py").write_text(
            "from kinby.plugins import ToolContext, tool\n"
            "from tests.test_delegated_runs import LEAKED_CONTEXTS\n"
            "@tool(write=False)\n"
            "def code(context: ToolContext) -> None:\n"
            '    """Keep the context past the turn."""\n'
            "    LEAKED_CONTEXTS.append(context)\n"
        )
        dispatcher = runtime(instance)
        events = await fire_routine(dispatcher)
        leaked = LEAKED_CONTEXTS.pop()

        with pytest.raises(NoActiveTurn):
            await leaked.report_run(COMPLETED_RUN)
        await asyncio.sleep(0)
        with pytest.raises(NoActiveTurn):
            await leaked.report_run(COMPLETED_RUN)

        usage = await call(dispatcher, "usage.get")
        assert isinstance(usage, UsageGetResult)
        [turn] = usage.threads[0].turns
        assert turn.turn_id == events[0].turn_id
        assert turn.delegated_runs == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("outcome", "resets_at"),
    [
        (DelegatedRunOutcome.LIMITED, None),
        (DelegatedRunOutcome.COMPLETED, datetime(2026, 9, 25, 18, 40, tzinfo=UTC)),
        (DelegatedRunOutcome.FAILED, datetime(2026, 9, 25, 18, 40, tzinfo=UTC)),
    ],
)
def test_only_a_limited_run_records_when_its_plan_window_resets(
    outcome: DelegatedRunOutcome, resets_at: datetime | None
) -> None:
    with pytest.raises(ValidationError, match="resets_at"):
        DelegatedRun.model_validate(
            {**COMPLETED_RUN.model_dump(), "outcome": outcome, "resets_at": resets_at}
        )


def test_a_run_record_has_no_cost_or_account() -> None:
    for extra in ({"cost": 1.5}, {"account_id": "me@example.com"}):
        with pytest.raises(ValidationError):
            DelegatedRun.model_validate({**COMPLETED_RUN.model_dump(), **extra})


def test_a_model_facing_tool_reports_a_run_in_the_users_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        (instance.path / "tools").mkdir()
        (instance.path / "tools" / "review.py").write_text(
            "from kinby.contracts import DelegatedRun\n"
            "from kinby.plugins import ToolContext, tool\n"
            "@tool(write=False)\n"
            "async def review(context: ToolContext) -> str:\n"
            '    """Ask an outside agent to review the change."""\n'
            "    await context.report_run(\n"
            f"        DelegatedRun.model_validate_json({COMPLETED_RUN.model_dump_json()!r})\n"
            "    )\n"
            '    return "Looks good"\n'
        )
        model = ScriptedModel(
            [
                AIMessageChunk(
                    content="",
                    tool_calls=[
                        {"name": "review", "args": {}, "id": "review-1", "type": "tool_call"}
                    ],
                ),
                AIMessageChunk(content="Reviewed"),
            ]
        )
        dispatcher = runtime(instance, model)
        created = await call(dispatcher, "thread.create")
        assert isinstance(created, ThreadCreateResult)

        accepted = await call(
            dispatcher, "thread.turn.start", thread_id=created.id, message="Review it"
        )
        assert isinstance(accepted, AcceptedResult)
        stream = await thread_events(dispatcher, {"thread_id": created.id}, set(Scope))
        async with asyncio.timeout(5):
            async for event in stream:
                assert isinstance(event, Event)
                if is_turn_closing(event.payload):
                    assert isinstance(event.payload, TurnCompleted)
                    break
        await stream.aclose()
        usage = await call(dispatcher, "usage.get")

        assert isinstance(usage, UsageGetResult)
        [turn] = usage.threads[0].turns
        assert turn.turn_id == accepted.turn_id
        assert [listed.run for listed in turn.delegated_runs] == [COMPLETED_RUN]

    asyncio.run(scenario())


def test_turn_tokens_the_mismatch_check_and_budgets_ignore_delegated_runs(
    tmp_path: Path,
) -> None:
    api_run = COMPLETED_RUN.model_copy(
        update={"usage_source": UsageSource.API, "input_tokens": 10_000_000}
    )

    async def scenario() -> None:
        instance_at(tmp_path)
        with (tmp_path / "kinby.toml").open("a") as manifest:
            manifest.write("[budgets]\nusd_per_day = 0.001\n")
        instance = load_instance(tmp_path)
        code_step(
            instance, api_run, result='"Headlines"', frontmatter="description: News\ntokens: 10"
        )
        dispatcher = runtime(instance)

        first = await fire_routine(dispatcher)
        second = await fire_routine(dispatcher)
        stats = await call(dispatcher, "stats.get")

        for events in (first, second):
            completed = events[-1].payload
            assert isinstance(completed, TurnCompleted)
            assert (completed.input_tokens, completed.output_tokens) == (4, 2)
        assert isinstance(stats, StatsGetResult)
        assert stats.warnings == []

    asyncio.run(scenario())


def test_cli_usage_shows_delegated_runs_under_their_turn(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "alice"
    init_instance(path)
    instance = load_instance(path)
    code_step(instance, COMPLETED_RUN, LIMITED_RUN)
    events = asyncio.run(fire_routine(runtime(instance, clock=TickingClock())))
    first, second = (event for event in events if isinstance(event.payload, RunDelegated))

    exit_code = main(["usage", str(path)])

    output = capsys.readouterr()
    assert exit_code == 0
    assert output.err == ""
    assert output.out.splitlines() == [
        f"thread {events[0].thread_id}: input=0 output=0 total=0",
        (
            f"  turn {events[0].turn_id}: input=0 output=0 cache_read=0 cache_creation=0 "
            "recap_input=0 recap_output=0 total=0"
        ),
        (
            f"    delegated run {first.timestamp.isoformat()}: source=claude-subscription "
            "client=claude-code models=claude-opus-5-5,claude-haiku-4-5 outcome=completed "
            "input=1200 output=300 cache_read=800 cache_creation=100 total=1500 "
            "duration_ms=42000 client_turns=7"
        ),
        (
            f"    delegated run {second.timestamp.isoformat()}: source=claude-subscription "
            "client=claude-code models=claude-opus-5-5 outcome=limited "
            "input=90 output=0 cache_read=0 cache_creation=0 total=90 "
            "duration_ms=800 client_turns=0 resets_at=2026-09-25T18:40:00+00:00"
        ),
    ]
