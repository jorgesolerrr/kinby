import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from kinby.cli import main
from kinby.contracts import (
    AcceptedResult,
    ErrorCode,
    ErrorEnvelope,
    Event,
    MemoryRecapped,
    MessageDelta,
    ModelCompleted,
    Scope,
    ThreadCompacted,
    ThreadCreateResult,
    ThreadUsage,
    TokenTotals,
    TurnCompleted,
    TurnUsage,
    UsageGetResult,
)
from kinby.core.dispatcher import Dispatcher, TurnConfig, build_dispatcher
from kinby.core.events import EventLog
from kinby.core.turns import PreparedTurnRequest, TurnContext, TurnOutcome
from kinby.instance import init_instance, load_instance
from tests.helpers import (
    cannot_restore,
    does_not_park,
    fixed_permission_ceiling,
    fixed_turn_preparation,
    no_routines,
    thread_events,
)


class UsageRunner:
    async def run(self, turn: PreparedTurnRequest, context: TurnContext) -> TurnOutcome:
        usage = {
            "First": TokenTotals(input_tokens=11, output_tokens=7),
            "Next": TokenTotals(input_tokens=4, output_tokens=2),
            "Second": TokenTotals(input_tokens=5, output_tokens=3),
            "Earlier": TokenTotals(input_tokens=30, output_tokens=20),
            "Included": TokenTotals(input_tokens=3, output_tokens=2),
        }[turn.message]
        return TurnOutcome(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )

    resume = does_not_park
    restore = cannot_restore
    routine_mode = no_routines


@dataclass(frozen=True)
class RecordedTurn:
    thread_id: UUID
    turn_id: UUID
    completed_at: datetime
    usage: TokenTotals


async def _record_turn(
    dispatcher: Dispatcher,
    message: str,
    thread_id: UUID | None = None,
) -> RecordedTurn:
    if thread_id is None:
        created = await dispatcher.dispatch(
            "thread.create",
            {},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(created, ThreadCreateResult)
        thread_id = created.id
    accepted = await dispatcher.dispatch(
        "thread.turn.start",
        {"thread_id": thread_id, "message": message},
        {Scope.THREAD_OPERATE},
    )
    assert isinstance(accepted, AcceptedResult)
    subscription = await thread_events(
        dispatcher, {"thread_id": thread_id, "after_sequence": accepted.sequence}
    )
    completed = await asyncio.wait_for(anext(subscription), timeout=1)
    while isinstance(completed, Event) and isinstance(completed.payload, ModelCompleted):
        completed = await asyncio.wait_for(anext(subscription), timeout=1)
    await subscription.aclose()
    assert isinstance(completed, Event)
    assert isinstance(completed.payload, TurnCompleted)
    return RecordedTurn(
        thread_id=thread_id,
        turn_id=accepted.turn_id,
        completed_at=completed.timestamp,
        usage=TokenTotals(
            input_tokens=completed.payload.input_tokens,
            output_tokens=completed.payload.output_tokens,
        ),
    )


def _thread_usage(*turns: RecordedTurn) -> ThreadUsage:
    thread_id = turns[0].thread_id
    assert all(turn.thread_id == thread_id for turn in turns)
    return ThreadUsage(
        thread_id=thread_id,
        input_tokens=sum(turn.usage.input_tokens for turn in turns),
        output_tokens=sum(turn.usage.output_tokens for turn in turns),
        turns=[
            TurnUsage(
                turn_id=turn.turn_id,
                input_tokens=turn.usage.input_tokens,
                output_tokens=turn.usage.output_tokens,
                recap_input_tokens=0,
                recap_output_tokens=0,
            )
            for turn in turns
        ],
    )


def test_usage_get_reports_zero_recap_tokens_with_zero_or_no_marker(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        event_log = EventLog(tmp_path)
        dispatcher = build_dispatcher(
            tmp_path,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                UsageRunner(),
            ),
        )
        first = await _record_turn(dispatcher, "First")
        second = await _record_turn(dispatcher, "Second")
        await event_log.append(
            first.thread_id,
            first.turn_id,
            MemoryRecapped(node=None, input_tokens=0, output_tokens=0),
        )

        result = await dispatcher.dispatch(
            "usage.get",
            {},
            {Scope.INSTANCE_READ},
        )

        assert result == UsageGetResult(
            threads=[
                _thread_usage(first),
                _thread_usage(second),
            ]
        )

    asyncio.run(scenario())


def test_usage_get_sums_completed_turns_per_thread(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                UsageRunner(),
            ),
        )
        first = await _record_turn(dispatcher, "First")
        next_turn = await _record_turn(dispatcher, "Next", first.thread_id)
        second = await _record_turn(dispatcher, "Second")

        result = await dispatcher.dispatch(
            "usage.get",
            {},
            {Scope.INSTANCE_READ},
        )

        assert result == UsageGetResult(
            threads=[
                _thread_usage(first, next_turn),
                _thread_usage(second),
            ]
        )

    asyncio.run(scenario())


def test_usage_get_includes_recap_tokens_in_turn_and_thread_totals(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        event_log = EventLog(tmp_path)
        dispatcher = build_dispatcher(
            tmp_path,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                UsageRunner(),
            ),
        )
        recorded = await _record_turn(dispatcher, "First")
        await event_log.append(
            recorded.thread_id,
            recorded.turn_id,
            MemoryRecapped(
                node=None,
                input_tokens=13,
                output_tokens=5,
                cache_read_tokens=8,
                cache_creation_tokens=2,
            ),
        )

        result = await dispatcher.dispatch(
            "usage.get",
            {},
            {Scope.INSTANCE_READ},
        )

        assert result == UsageGetResult(
            threads=[
                ThreadUsage(
                    thread_id=recorded.thread_id,
                    input_tokens=24,
                    output_tokens=12,
                    cache_read_tokens=8,
                    cache_creation_tokens=2,
                    turns=[
                        TurnUsage(
                            turn_id=recorded.turn_id,
                            input_tokens=24,
                            output_tokens=12,
                            cache_read_tokens=8,
                            cache_creation_tokens=2,
                            recap_input_tokens=13,
                            recap_output_tokens=5,
                        )
                    ],
                )
            ]
        )

    asyncio.run(scenario())


def test_usage_get_reads_past_irrelevant_events(tmp_path: Path) -> None:
    thread_id = uuid4()
    turn_id = uuid4()

    async def scenario() -> None:
        event_log = EventLog(tmp_path)
        await event_log.append(thread_id, turn_id, MessageDelta(text="discarded"))
        await event_log.append(thread_id, turn_id, MessageDelta(text="also discarded"))
        await event_log.append(thread_id, turn_id, TurnCompleted(input_tokens=11, output_tokens=7))
        result = await build_dispatcher(tmp_path, event_log=event_log).dispatch(
            "usage.get", {}, {Scope.INSTANCE_READ}
        )

        assert result == UsageGetResult(
            threads=[
                ThreadUsage(
                    thread_id=thread_id,
                    input_tokens=11,
                    output_tokens=7,
                    turns=[
                        TurnUsage(
                            turn_id=turn_id,
                            input_tokens=11,
                            output_tokens=7,
                            recap_input_tokens=0,
                            recap_output_tokens=0,
                        )
                    ],
                )
            ]
        )

    asyncio.run(scenario())


def test_usage_get_requires_instance_read_before_validating_payload(tmp_path: Path) -> None:
    result = asyncio.run(
        build_dispatcher(tmp_path).dispatch(
            "usage.get",
            {"unexpected": True},
            set(),
        )
    )

    assert isinstance(result, ErrorEnvelope)
    assert result.code is ErrorCode.PERMISSION_DENIED


def test_cli_shows_token_totals_per_thread_and_turn(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    instance = tmp_path / "alice"
    init_instance(instance)

    async def record_usage() -> RecordedTurn:
        loaded = load_instance(instance)
        event_log = EventLog(loaded.manifest.state_dir)
        dispatcher = build_dispatcher(
            loaded.manifest.state_dir,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                UsageRunner(),
            ),
        )
        recorded = await _record_turn(dispatcher, "First")
        await event_log.append(
            recorded.thread_id,
            recorded.turn_id,
            MemoryRecapped(
                node=None,
                input_tokens=13,
                output_tokens=5,
                cache_read_tokens=8,
                cache_creation_tokens=2,
            ),
        )
        return recorded

    recorded = asyncio.run(record_usage())

    exit_code = main(["usage", str(instance)])

    output = capsys.readouterr()
    assert exit_code == 0
    assert output.err == ""
    assert output.out.splitlines() == [
        f"thread {recorded.thread_id}: input=24 output=12 token_usage=36",
        (
            f"  turn {recorded.turn_id}: input=24 output=12 cache_read=8 cache_creation=2 "
            "recap_input=13 recap_output=5 compaction_input=0 compaction_output=0 token_usage=36"
        ),
    ]


def test_cli_shows_a_compactions_tokens_apart_from_the_turns(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    instance = tmp_path / "alice"
    init_instance(instance)

    async def record_usage() -> RecordedTurn:
        loaded = load_instance(instance)
        event_log = EventLog(loaded.manifest.state_dir)
        dispatcher = build_dispatcher(
            loaded.manifest.state_dir,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                UsageRunner(),
            ),
        )
        recorded = await _record_turn(dispatcher, "First")
        await event_log.append(
            recorded.thread_id,
            recorded.turn_id,
            ThreadCompacted(
                summary="Summary.", boundary=2, input_tokens=20, output_tokens=4, model="test"
            ),
        )
        return recorded

    recorded = asyncio.run(record_usage())

    exit_code = main(["usage", str(instance)])

    output = capsys.readouterr()
    assert exit_code == 0
    assert output.out.splitlines()[1] == (
        f"  turn {recorded.turn_id}: input=31 output=11 cache_read=0 cache_creation=0 "
        "recap_input=0 recap_output=0 compaction_input=20 compaction_output=4 token_usage=42"
    )


def test_cli_rejects_a_usage_range_without_timezone(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    instance = tmp_path / "alice"
    init_instance(instance)

    exit_code = main(["usage", str(instance), "--since", "2026-08-28T12:00:00"])

    output = capsys.readouterr()
    assert exit_code == 1
    assert output.out == ""
    assert output.err == (
        "--since and --until must be ISO 8601 times with a timezone, "
        "for example 2026-08-28T12:00:00Z\n"
    )


def test_usage_get_limits_totals_to_the_requested_event_range(tmp_path: Path) -> None:
    async def scenario() -> None:
        event_log = EventLog(tmp_path)
        dispatcher = build_dispatcher(
            tmp_path,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                UsageRunner(),
            ),
        )
        earlier = await _record_turn(dispatcher, "Earlier")
        await asyncio.sleep(0.001)
        included = await _record_turn(dispatcher, "Included", earlier.thread_id)
        await event_log.append(
            included.thread_id,
            included.turn_id,
            MemoryRecapped(node=None, input_tokens=7, output_tokens=4),
        )

        result = await dispatcher.dispatch(
            "usage.get",
            {
                "since": included.completed_at,
                "until": included.completed_at,
            },
            {Scope.INSTANCE_READ},
        )

        assert result == UsageGetResult(
            threads=[
                ThreadUsage(
                    thread_id=included.thread_id,
                    input_tokens=10,
                    output_tokens=6,
                    turns=[
                        TurnUsage(
                            turn_id=included.turn_id,
                            input_tokens=10,
                            output_tokens=6,
                            recap_input_tokens=7,
                            recap_output_tokens=4,
                        )
                    ],
                )
            ]
        )

    asyncio.run(scenario())


class ModelCallRunner:
    """Emits each turn's model calls by its message, and returns their summed usage."""

    def __init__(self, calls: dict[str, list[ModelCompleted]]) -> None:
        self._calls = calls

    async def run(self, turn: PreparedTurnRequest, context: TurnContext) -> TurnOutcome:
        calls = self._calls.get(turn.message, [])
        for call in calls:
            await context.emit(call)
        return TurnOutcome(
            input_tokens=sum(call.input_tokens for call in calls),
            output_tokens=sum(call.output_tokens for call in calls),
        )

    resume = does_not_park
    restore = cannot_restore
    routine_mode = no_routines


def _model_call(
    model: str, input_tokens: int, cache_read: int, cache_creation: int
) -> ModelCompleted:
    return ModelCompleted(
        model=model,
        input_tokens=input_tokens,
        output_tokens=50,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        duration_ms=1,
    )


def test_usage_get_returns_each_threads_context_size_and_its_models_window(
    tmp_path: Path,
) -> None:
    opus = "anthropic:claude-opus-5-5"

    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ModelCallRunner(
                    {
                        "First": [
                            _model_call(opus, 9_000, 6_000, 2_000),
                            _model_call(opus, 30_000, 25_000, 4_000),
                        ],
                        "Next": [_model_call(opus, 12_000, 10_000, 1_500)],
                    }
                ),
            ),
        )
        first = await _record_turn(dispatcher, "First")
        await _record_turn(dispatcher, "Next", first.thread_id)
        silent = await _record_turn(dispatcher, "Silent")

        result = await dispatcher.dispatch("usage.get", {}, {Scope.INSTANCE_READ})

        assert isinstance(result, UsageGetResult)
        sizes = {
            thread.thread_id: (thread.context_size, thread.window) for thread in result.threads
        }
        assert sizes == {first.thread_id: (12_000, 1_000_000), silent.thread_id: (None, None)}

    asyncio.run(scenario())


def test_cli_shows_each_threads_context_size_beside_its_window_when_the_model_has_one(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    instance = tmp_path / "alice"
    init_instance(instance)
    with (instance / "kinby.toml").open("a", encoding="utf-8") as manifest:
        manifest.write(
            '[prices."custom:windowed"]\ninput = 1\noutput = 2\nwindow = 64000\n'
            '[prices."custom:unwindowed"]\ninput = 1\noutput = 2\n'
        )

    async def record_usage() -> tuple[RecordedTurn, RecordedTurn]:
        loaded = load_instance(instance)
        dispatcher = build_dispatcher(
            loaded.manifest.state_dir,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ModelCallRunner(
                    {
                        "Windowed": [_model_call("custom:windowed", 4_000, 3_000, 500)],
                        "Unwindowed": [_model_call("custom:unwindowed", 7_000, 0, 0)],
                    }
                ),
            ),
        )
        return (
            await _record_turn(dispatcher, "Windowed"),
            await _record_turn(dispatcher, "Unwindowed"),
        )

    windowed, unwindowed = asyncio.run(record_usage())

    exit_code = main(["usage", str(instance)])

    output = capsys.readouterr()
    assert exit_code == 0
    assert output.err == ""
    threads = [line for line in output.out.splitlines() if line.startswith("thread ")]
    assert threads == [
        f"thread {windowed.thread_id}: input=4000 output=50 token_usage=4050 "
        "context_size=4000 window=64000",
        f"thread {unwindowed.thread_id}: input=7000 output=50 token_usage=7050 context_size=7000",
    ]


def test_usage_get_keeps_a_threads_context_size_when_its_last_call_is_before_since(
    tmp_path: Path,
) -> None:
    opus = "anthropic:claude-opus-5-5"

    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ModelCallRunner({"First": [_model_call(opus, 9_000, 6_000, 2_000)]}),
            ),
        )
        first = await _record_turn(dispatcher, "First")
        await asyncio.sleep(0.001)
        no_call = await _record_turn(dispatcher, "No call", first.thread_id)

        result = await dispatcher.dispatch(
            "usage.get", {"since": no_call.completed_at}, {Scope.INSTANCE_READ}
        )

        assert isinstance(result, UsageGetResult)
        assert [(thread.context_size, thread.window) for thread in result.threads] == [
            (9_000, 1_000_000)
        ]

    asyncio.run(scenario())


def test_usage_get_leaves_a_recap_call_out_of_the_context_size(tmp_path: Path) -> None:
    opus = "anthropic:claude-opus-5-5"

    async def scenario() -> None:
        event_log = EventLog(tmp_path)
        dispatcher = build_dispatcher(
            tmp_path,
            event_log=event_log,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ModelCallRunner({"First": [_model_call(opus, 9_000, 6_000, 2_000)]}),
            ),
        )
        recorded = await _record_turn(dispatcher, "First")
        await event_log.append(
            recorded.thread_id,
            recorded.turn_id,
            _model_call("anthropic:claude-haiku-4-5", 700, 0, 0),
        )

        result = await dispatcher.dispatch("usage.get", {}, {Scope.INSTANCE_READ})

        assert isinstance(result, UsageGetResult)
        assert [(thread.context_size, thread.window) for thread in result.threads] == [
            (9_000, 1_000_000)
        ]

    asyncio.run(scenario())
