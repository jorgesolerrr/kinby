import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from kinby.cli import main
from kinby.contracts import (
    AcceptedResult,
    ApprovalRequested,
    ErrorCode,
    ErrorEnvelope,
    Event,
    MemoryRecapped,
    Scope,
    StatsBucket,
    StatsBucketSize,
    StatsGetResult,
    ThreadCreateResult,
    ToolCall,
    ToolResult,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnRated,
    TurnStarted,
    TurnVerdict,
)
from kinby.core.dispatcher import Dispatcher, TurnConfig, build_dispatcher
from kinby.core.events import EventLog
from kinby.core.turns import Emit, TurnOutcome, TurnRequest
from kinby.instance import init_instance, load_instance
from tests.helpers import (
    cannot_restore,
    does_not_park,
    fixed_permission_ceiling,
    fixed_turn_preparation,
)


class MetricsRunner:
    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
        for call_id in ("bash-1", "bash-2"):
            await emit(ToolCall(call_id=call_id, name="bash", arguments={}))
            await emit(
                ToolResult(
                    call_id=call_id,
                    name="bash",
                    output="done",
                    error=False,
                )
            )
        await emit(ToolCall(call_id="search-1", name="memory_search", arguments={}))
        await emit(
            ToolResult(
                call_id="search-1",
                name="memory_search",
                output="find",
                error=False,
            )
        )
        await emit(ToolCall(call_id="open-1", name="memory_open", arguments={}))
        await emit(
            ToolResult(
                call_id="open-1",
                name="memory_open",
                output="remember",
                error=False,
            )
        )
        await emit(
            ApprovalRequested(
                approval_id=uuid4(),
                name="bash",
                arguments={},
                rule="ask",
            )
        )
        return TurnOutcome(input_tokens=11, output_tokens=7)

    resume = does_not_park
    restore = cannot_restore


async def _record_metrics_turn(dispatcher: Dispatcher) -> AcceptedResult:
    created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
    assert isinstance(created, ThreadCreateResult)
    accepted = await dispatcher.dispatch(
        "thread.turn.start",
        {"thread_id": created.id, "message": "Measure this turn"},
        {Scope.THREAD_OPERATE},
    )
    assert isinstance(accepted, AcceptedResult)
    subscription = dispatcher.subscribe(
        "thread.subscribe",
        {"thread_id": created.id, "after_sequence": 0},
        {Scope.THREAD_READ},
    )
    events: list[Event] = []
    while True:
        event = await asyncio.wait_for(anext(subscription), timeout=1)
        assert isinstance(event, Event)
        events.append(event)
        if isinstance(event.payload, TurnCompleted):
            break
    await subscription.aclose()
    return accepted


def test_stats_get_reports_one_turn_record_from_the_event_log(tmp_path: Path) -> None:
    async def scenario() -> None:
        event_log = EventLog(tmp_path)
        dispatcher = build_dispatcher(
            tmp_path,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                MetricsRunner(),
            ),
        )
        accepted = await _record_metrics_turn(dispatcher)
        events = list(event_log.all_events())
        started = events[0]
        closed = events[-1]

        result = await dispatcher.dispatch("stats.get", {}, {Scope.INSTANCE_READ})

        assert isinstance(result, StatsGetResult)
        assert result.unpriced_models == []
        assert len(result.records) == 1
        record = result.records[0]
        assert record.thread_id == accepted.thread_id
        assert record.turn_id == accepted.turn_id
        assert record.model == "openai:gpt-5"
        assert record.closing_kind == "completed"
        assert record.started_at == started.timestamp
        assert record.closed_at == closed.timestamp
        assert record.duration_seconds == (closed.timestamp - started.timestamp).total_seconds()
        assert record.input_tokens == 11
        assert record.output_tokens == 7
        assert record.recap_input_tokens == 0
        assert record.recap_output_tokens == 0
        assert record.cost == 0.00008375
        assert record.tool_calls == {"bash": 2, "memory_search": 1, "memory_open": 1}
        assert record.memory_calls.model_dump() == {
            "search": 1,
            "open": 1,
            "remember": 0,
            "forget": 0,
        }
        assert record.memory_consulted is True
        assert record.approvals_requested == 1
        assert record.memory_tokens == 3
        assert record.rating is None
        assert result.buckets[0].cost == 0.00008375

    asyncio.run(scenario())


def test_stats_get_uses_a_manifest_price_override(tmp_path: Path) -> None:
    instance_path = tmp_path / "alice"
    init_instance(instance_path, model="openai:gpt-5")
    with (instance_path / "kinby.toml").open("a", encoding="utf-8") as manifest:
        manifest.write('\n[prices."openai:gpt-5"]\ninput = 2\noutput = 4\n')
    instance = load_instance(instance_path)
    thread_id = uuid4()
    turn_id = uuid4()
    now = datetime(2026, 9, 1, 10, tzinfo=UTC)
    events = [
        _event(
            1,
            thread_id,
            turn_id,
            now,
            TurnStarted(message="priced", model="openai:gpt-5"),
        ),
        _event(
            2,
            thread_id,
            turn_id,
            now,
            TurnCompleted(input_tokens=100, output_tokens=10),
        ),
    ]

    result = asyncio.run(
        build_dispatcher(
            instance.manifest.state_dir,
            event_log=StaticEventLog(instance.manifest.state_dir, events),
            price_overrides=instance.manifest.prices,
        ).dispatch("stats.get", {}, {Scope.INSTANCE_READ})
    )

    assert isinstance(result, StatsGetResult)
    assert result.records[0].cost == 0.00024
    assert result.buckets[0].cost == 0.00024


def test_stats_get_reports_unpriced_models_and_sums_only_priced_turns(
    tmp_path: Path,
) -> None:
    thread_id = uuid4()
    priced_id = uuid4()
    unpriced_id = uuid4()
    now = datetime(2026, 9, 1, 10, tzinfo=UTC)
    events = [
        _event(
            1,
            thread_id,
            priced_id,
            now,
            TurnStarted(message="priced", model="openai:gpt-5"),
        ),
        _event(
            2,
            thread_id,
            priced_id,
            now,
            TurnCompleted(input_tokens=100, output_tokens=10),
        ),
        _event(
            3,
            thread_id,
            unpriced_id,
            now,
            TurnStarted(message="unknown", model="other:model"),
        ),
        _event(
            4,
            thread_id,
            unpriced_id,
            now,
            TurnCompleted(input_tokens=100, output_tokens=10),
        ),
    ]

    result = asyncio.run(
        build_dispatcher(tmp_path, event_log=StaticEventLog(tmp_path, events)).dispatch(
            "stats.get",
            {},
            {Scope.INSTANCE_READ},
        )
    )

    assert isinstance(result, StatsGetResult)
    assert [record.cost for record in result.records] == [0.000225, None]
    assert result.unpriced_models == ["other:model"]
    assert result.buckets[0].cost == 0.000225


def test_stats_get_prices_recap_tokens_at_their_model_and_keeps_old_markers_unknown(
    tmp_path: Path,
) -> None:
    thread_id = uuid4()
    priced_id = uuid4()
    legacy_id = uuid4()
    now = datetime(2026, 9, 1, 10, tzinfo=UTC)
    events = [
        _event(
            1,
            thread_id,
            priced_id,
            now,
            TurnStarted(message="new marker", model="openai:gpt-5"),
        ),
        _event(
            2,
            thread_id,
            priced_id,
            now,
            TurnCompleted(input_tokens=100, output_tokens=10),
        ),
        _event(
            3,
            thread_id,
            priced_id,
            now,
            MemoryRecapped(
                node=None,
                input_tokens=50,
                output_tokens=5,
                model="anthropic:claude-sonnet-4-6",
            ),
        ),
        _event(
            4,
            thread_id,
            legacy_id,
            now,
            TurnStarted(message="old marker", model="openai:gpt-5"),
        ),
        _event(
            5,
            thread_id,
            legacy_id,
            now,
            TurnCompleted(input_tokens=100, output_tokens=10),
        ),
        _event(
            6,
            thread_id,
            legacy_id,
            now,
            MemoryRecapped(node=None, input_tokens=50, output_tokens=5),
        ),
    ]

    result = asyncio.run(
        build_dispatcher(tmp_path, event_log=StaticEventLog(tmp_path, events)).dispatch(
            "stats.get",
            {},
            {Scope.INSTANCE_READ},
        )
    )

    assert isinstance(result, StatsGetResult)
    assert result.records[0].cost == 0.00045
    assert result.records[1].cost is None
    assert result.buckets[0].cost == 0.00045


class StaticEventLog(EventLog):
    def __init__(self, state_dir: Path, events: list[Event]) -> None:
        super().__init__(state_dir)
        self._events = events

    def all_events(self):
        yield from self._events


def _event(
    sequence: int,
    thread_id: UUID,
    turn_id: UUID,
    timestamp: datetime,
    payload,
) -> Event:
    return Event(
        sequence=sequence,
        thread_id=thread_id,
        turn_id=turn_id,
        timestamp=timestamp,
        payload=payload,
    )


def test_stats_get_marks_missing_start_metadata_as_unknown(tmp_path: Path) -> None:
    thread_id = uuid4()
    turn_id = uuid4()
    closed_at = datetime(2026, 9, 1, 10, tzinfo=UTC)
    events = [
        _event(
            1,
            thread_id,
            turn_id,
            closed_at,
            TurnCompleted(input_tokens=11, output_tokens=7),
        )
    ]

    result = asyncio.run(
        build_dispatcher(tmp_path, event_log=StaticEventLog(tmp_path, events)).dispatch(
            "stats.get",
            {},
            {Scope.INSTANCE_READ},
        )
    )

    assert isinstance(result, StatsGetResult)
    assert len(result.records) == 1
    assert result.records[0].model is None
    assert result.records[0].started_at is None
    assert result.records[0].duration_seconds is None
    assert result.buckets[0].mean_duration_seconds is None


def test_stats_get_aggregates_closed_turns_by_utc_day(tmp_path: Path) -> None:
    thread_id = uuid4()
    completed_id = uuid4()
    failed_id = uuid4()
    monday = datetime(2026, 8, 31, 10, tzinfo=UTC)
    tuesday = datetime(2026, 9, 1, 11, tzinfo=UTC)
    events = [
        _event(1, thread_id, completed_id, monday, TurnStarted(message="one", model="model")),
        _event(
            2,
            thread_id,
            completed_id,
            monday + timedelta(seconds=10),
            ToolCall(call_id="bash-1", name="bash", arguments={}),
        ),
        _event(
            3,
            thread_id,
            completed_id,
            monday + timedelta(seconds=20),
            TurnCompleted(input_tokens=11, output_tokens=7),
        ),
        _event(4, thread_id, failed_id, tuesday, TurnStarted(message="two", model="model")),
        _event(
            5,
            thread_id,
            failed_id,
            tuesday + timedelta(seconds=30),
            TurnFailed(code="INTERNAL", message="failed"),
        ),
    ]

    result = asyncio.run(
        build_dispatcher(tmp_path, event_log=StaticEventLog(tmp_path, events)).dispatch(
            "stats.get",
            {},
            {Scope.INSTANCE_READ},
        )
    )

    assert isinstance(result, StatsGetResult)
    assert result.buckets == [
        StatsBucket(
            start=monday.date(),
            completed=1,
            failed=0,
            interrupted=0,
            input_tokens=11,
            output_tokens=7,
            recap_input_tokens=0,
            recap_output_tokens=0,
            cost=None,
            tool_calls={"bash": 1},
            memory_calls={},
            turns_without_memory=1,
            approvals_requested=0,
            mean_duration_seconds=20,
            good_ratings=0,
            bad_ratings=0,
        ),
        StatsBucket(
            start=tuesday.date(),
            completed=0,
            failed=1,
            interrupted=0,
            input_tokens=0,
            output_tokens=0,
            recap_input_tokens=0,
            recap_output_tokens=0,
            cost=None,
            tool_calls={},
            memory_calls={},
            turns_without_memory=1,
            approvals_requested=0,
            mean_duration_seconds=30,
            good_ratings=0,
            bad_ratings=0,
        ),
    ]


def test_stats_get_keeps_recap_latest_rating_and_every_closing_kind(tmp_path: Path) -> None:
    thread_id = uuid4()
    completed_id = uuid4()
    failed_id = uuid4()
    interrupted_id = uuid4()
    started_at = datetime(2026, 9, 1, 10, tzinfo=UTC)
    events = [
        _event(
            1,
            thread_id,
            completed_id,
            started_at,
            TurnStarted(message="complete", model="model"),
        ),
        _event(
            2,
            thread_id,
            completed_id,
            started_at + timedelta(seconds=1),
            TurnCompleted(input_tokens=11, output_tokens=7),
        ),
        _event(
            3,
            thread_id,
            completed_id,
            started_at + timedelta(seconds=2),
            MemoryRecapped(node=None, input_tokens=13, output_tokens=5),
        ),
        _event(
            4,
            thread_id,
            completed_id,
            started_at + timedelta(seconds=3),
            TurnRated(verdict=TurnVerdict.GOOD),
        ),
        _event(
            5,
            thread_id,
            completed_id,
            started_at + timedelta(seconds=4),
            TurnRated(verdict=TurnVerdict.BAD, reason="Changed my mind"),
        ),
        _event(
            6,
            thread_id,
            failed_id,
            started_at,
            TurnStarted(message="fail", model="model"),
        ),
        _event(
            7,
            thread_id,
            failed_id,
            started_at + timedelta(seconds=5),
            TurnFailed(code="INTERNAL", message="failed"),
        ),
        _event(
            8,
            thread_id,
            failed_id,
            started_at + timedelta(seconds=5),
            TurnRated(verdict=TurnVerdict.GOOD),
        ),
        _event(
            9,
            thread_id,
            interrupted_id,
            started_at,
            TurnStarted(message="interrupt", model="model"),
        ),
        _event(
            10,
            thread_id,
            interrupted_id,
            started_at + timedelta(seconds=6),
            TurnInterrupted(),
        ),
    ]

    result = asyncio.run(
        build_dispatcher(tmp_path, event_log=StaticEventLog(tmp_path, events)).dispatch(
            "stats.get",
            {},
            {Scope.INSTANCE_READ},
        )
    )

    assert isinstance(result, StatsGetResult)
    assert [record.closing_kind for record in result.records] == [
        "completed",
        "failed",
        "interrupted",
    ]
    completed = result.records[0]
    assert completed.input_tokens == 24
    assert completed.output_tokens == 12
    assert completed.recap_input_tokens == 13
    assert completed.recap_output_tokens == 5
    assert completed.rating == TurnRated(
        verdict=TurnVerdict.BAD,
        reason="Changed my mind",
    )
    assert all(not record.memory_consulted for record in result.records)
    assert result.buckets[0].turns_without_memory == 3
    assert result.buckets[0].good_ratings == 1
    assert result.buckets[0].bad_ratings == 1


def test_stats_get_counts_write_memory_calls_without_marking_consulted(
    tmp_path: Path,
) -> None:
    thread_id = uuid4()
    turn_id = uuid4()
    started_at = datetime(2026, 9, 1, 10, tzinfo=UTC)
    events = [
        _event(
            1,
            thread_id,
            turn_id,
            started_at,
            TurnStarted(message="write memory", model="model"),
        ),
        _event(
            2,
            thread_id,
            turn_id,
            started_at,
            ToolCall(call_id="remember-1", name="remember", arguments={}),
        ),
        _event(
            3,
            thread_id,
            turn_id,
            started_at,
            ToolResult(
                call_id="remember-1",
                name="remember",
                output="saved",
                error=False,
            ),
        ),
        _event(
            4,
            thread_id,
            turn_id,
            started_at,
            ToolCall(call_id="forget-1", name="forget", arguments={}),
        ),
        _event(
            5,
            thread_id,
            turn_id,
            started_at,
            ToolResult(
                call_id="forget-1",
                name="forget",
                output="forgotten",
                error=False,
            ),
        ),
        _event(
            6,
            thread_id,
            turn_id,
            started_at + timedelta(seconds=1),
            TurnCompleted(input_tokens=1, output_tokens=1),
        ),
    ]

    result = asyncio.run(
        build_dispatcher(tmp_path, event_log=StaticEventLog(tmp_path, events)).dispatch(
            "stats.get",
            {},
            {Scope.INSTANCE_READ},
        )
    )

    assert isinstance(result, StatsGetResult)
    assert result.records[0].memory_calls.model_dump() == {
        "search": 0,
        "open": 0,
        "remember": 1,
        "forget": 1,
    }
    assert result.records[0].memory_consulted is False
    assert result.records[0].memory_tokens == 3.5
    assert result.buckets[0].turns_without_memory == 1


def test_stats_get_groups_monday_week_and_applies_inclusive_bounds(tmp_path: Path) -> None:
    thread_id = uuid4()
    monday_id = uuid4()
    tuesday_id = uuid4()
    monday = datetime(2026, 8, 31, 10, tzinfo=UTC)
    tuesday = datetime(2026, 9, 1, 10, tzinfo=UTC)
    events = [
        _event(
            1,
            thread_id,
            monday_id,
            monday,
            TurnStarted(message="one", model="outside:model"),
        ),
        _event(
            2,
            thread_id,
            monday_id,
            monday + timedelta(seconds=10),
            TurnCompleted(input_tokens=2, output_tokens=1),
        ),
        _event(
            3,
            thread_id,
            tuesday_id,
            tuesday,
            TurnStarted(message="two", model="inside:model"),
        ),
        _event(
            4,
            thread_id,
            tuesday_id,
            tuesday + timedelta(seconds=20),
            TurnCompleted(input_tokens=3, output_tokens=2),
        ),
    ]
    dispatcher = build_dispatcher(tmp_path, event_log=StaticEventLog(tmp_path, events))

    weekly = asyncio.run(dispatcher.dispatch("stats.get", {"by": "week"}, {Scope.INSTANCE_READ}))
    bounded = asyncio.run(
        dispatcher.dispatch(
            "stats.get",
            {
                "since": tuesday + timedelta(seconds=20),
                "until": tuesday + timedelta(seconds=20),
            },
            {Scope.INSTANCE_READ},
        )
    )

    assert isinstance(weekly, StatsGetResult)
    assert weekly.unpriced_models == ["inside:model", "outside:model"]
    assert len(weekly.buckets) == 1
    assert weekly.buckets[0].start == monday.date()
    assert weekly.buckets[0].completed == 2
    assert weekly.buckets[0].input_tokens == 5
    assert weekly.buckets[0].output_tokens == 3
    assert weekly.buckets[0].mean_duration_seconds == 15
    assert StatsBucketSize.WEEK == "week"
    assert isinstance(bounded, StatsGetResult)
    assert bounded.unpriced_models == ["inside:model"]
    assert [record.turn_id for record in bounded.records] == [tuesday_id]
    assert [bucket.start for bucket in bounded.buckets] == [tuesday.date()]


def test_stats_get_requires_instance_read_before_validating_payload(tmp_path: Path) -> None:
    result = asyncio.run(
        build_dispatcher(tmp_path).dispatch(
            "stats.get",
            {"unexpected": True},
            set(),
        )
    )

    assert isinstance(result, ErrorEnvelope)
    assert result.code is ErrorCode.PERMISSION_DENIED


def test_cli_stats_prints_buckets_and_totals_and_writes_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    instance_path = tmp_path / "alice"
    init_instance(instance_path)
    instance = load_instance(instance_path)
    event_log = EventLog(instance.manifest.state_dir)
    dispatcher = build_dispatcher(
        instance.manifest.state_dir,
        event_log=event_log,
        turns=TurnConfig(
            fixed_turn_preparation,
            fixed_permission_ceiling,
            MetricsRunner(),
        ),
    )
    asyncio.run(_record_metrics_turn(dispatcher))
    closed = list(event_log.all_events())[-1]
    report_path = instance.manifest.state_dir / "stats.json"
    report_path.write_text("old report", encoding="utf-8")

    exit_code = main(["stats", str(instance_path)])

    output = capsys.readouterr()
    assert exit_code == 0
    assert output.err == ""
    lines = output.out.splitlines()
    assert lines[0].split("\t") == [
        "bucket",
        "completed",
        "failed",
        "interrupted",
        "input",
        "output",
        "recap input",
        "recap output",
        "cost",
        "tool calls",
        "memory search",
        "memory open",
        "remember",
        "forget",
        "without memory",
        "approvals",
        "mean seconds",
        "good",
        "bad",
    ]
    bucket_fields = lines[1].split("\t")
    assert bucket_fields[:16] == [
        closed.timestamp.date().isoformat(),
        "1",
        "0",
        "0",
        "11",
        "7",
        "0",
        "0",
        "0.00008375",
        "bash=2,memory_open=1,memory_search=1",
        "1",
        "1",
        "0",
        "0",
        "0",
        "1",
    ]
    assert float(bucket_fields[16]) >= 0
    assert bucket_fields[17:] == ["0", "0"]
    total_fields = lines[2].split("\t")
    assert total_fields[0] == "total"
    assert total_fields[1:] == bucket_fields[1:]
    report = StatsGetResult.model_validate_json(report_path.read_text())
    assert len(report.records) == 1
    assert report.records[0].turn_id == closed.turn_id
    assert report.buckets[0].start == closed.timestamp.date()


def test_cli_stats_warns_once_with_every_unpriced_model(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    instance_path = tmp_path / "alice"
    init_instance(instance_path)
    instance = load_instance(instance_path)
    event_log = EventLog(instance.manifest.state_dir)
    thread_id = uuid4()

    async def append_turn(model: str) -> None:
        turn_id = uuid4()
        await event_log.append(
            thread_id,
            turn_id,
            TurnStarted(message="unknown", model=model),
        )
        await event_log.append(
            thread_id,
            turn_id,
            TurnCompleted(input_tokens=100, output_tokens=10),
        )

    asyncio.run(append_turn("other:zeta"))
    asyncio.run(append_turn("other:alpha"))

    exit_code = main(["stats", str(instance_path)])

    output = capsys.readouterr()
    assert exit_code == 0
    assert output.err == "warning: unpriced models: other:alpha, other:zeta\n"
    lines = output.out.splitlines()
    assert "\tcost\t" in lines[0]
    cost_column = lines[0].split("\t").index("cost")
    assert lines[1].split("\t")[cost_column] == "unknown"
    assert lines[2].split("\t")[cost_column] == "unknown"


def test_cli_stats_rejects_a_range_without_timezone(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    instance_path = tmp_path / "alice"
    init_instance(instance_path)

    exit_code = main(["stats", str(instance_path), "--since", "2026-09-01T12:00:00"])

    output = capsys.readouterr()
    assert exit_code == 1
    assert output.out == ""
    assert output.err == (
        "--since and --until must be ISO 8601 times with a timezone, "
        "for example 2026-08-28T12:00:00Z\n"
    )
