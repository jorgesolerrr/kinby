import asyncio
import gc
import weakref
from dataclasses import dataclass
from datetime import UTC, datetime
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
    Scope,
    ThreadCreateResult,
    ThreadUsage,
    TokenTotals,
    TurnCompleted,
    TurnUsage,
    UsageGetResult,
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


class UsageRunner:
    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
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
    subscription = dispatcher.subscribe(
        "thread.subscribe",
        {"thread_id": thread_id, "after_sequence": accepted.sequence},
        {Scope.THREAD_READ},
    )
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
            MemoryRecapped(node=None, input_tokens=13, output_tokens=5),
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
                    turns=[
                        TurnUsage(
                            turn_id=recorded.turn_id,
                            input_tokens=24,
                            output_tokens=12,
                            recap_input_tokens=13,
                            recap_output_tokens=5,
                        )
                    ],
                )
            ]
        )

    asyncio.run(scenario())


def test_usage_get_releases_irrelevant_events_while_reading_log(tmp_path: Path) -> None:
    thread_id = uuid4()
    turn_id = uuid4()

    class StreamingEventLog(EventLog):
        def all_events(self):
            irrelevant = Event(
                sequence=1,
                thread_id=thread_id,
                turn_id=turn_id,
                payload=MessageDelta(text="discarded"),
                timestamp=datetime.now(UTC),
            )
            irrelevant_ref = weakref.ref(irrelevant)
            yield irrelevant
            del irrelevant
            yield Event(
                sequence=2,
                thread_id=thread_id,
                turn_id=turn_id,
                payload=MessageDelta(text="also discarded"),
                timestamp=datetime.now(UTC),
            )
            gc.collect()
            assert irrelevant_ref() is None
            yield Event(
                sequence=3,
                thread_id=thread_id,
                turn_id=turn_id,
                payload=TurnCompleted(input_tokens=11, output_tokens=7),
                timestamp=datetime.now(UTC),
            )

    async def scenario() -> None:
        result = await build_dispatcher(
            tmp_path,
            event_log=StreamingEventLog(tmp_path),
        ).dispatch("usage.get", {}, {Scope.INSTANCE_READ})

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
            MemoryRecapped(node=None, input_tokens=13, output_tokens=5),
        )
        return recorded

    recorded = asyncio.run(record_usage())

    exit_code = main(["usage", str(instance)])

    output = capsys.readouterr()
    assert exit_code == 0
    assert output.err == ""
    assert output.out.splitlines() == [
        f"thread {recorded.thread_id}: input=24 output=12 total=36",
        (f"  turn {recorded.turn_id}: input=24 output=12 recap_input=13 recap_output=5 total=36"),
    ]


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
