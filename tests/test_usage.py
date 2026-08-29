import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest

from kinby.cli import main
from kinby.contracts import (
    AcceptedResult,
    ErrorCode,
    ErrorEnvelope,
    Event,
    Scope,
    ThreadCreateResult,
    ThreadUsage,
    TokenTotals,
    TurnCompleted,
    TurnUsage,
    UsageGetResult,
)
from kinby.core.dispatcher import Dispatcher, TurnConfig, build_dispatcher
from kinby.core.turns import Emit, TurnOutcome, TurnRequest
from kinby.instance import init_instance, load_instance
from tests.helpers import does_not_park, fixed_model_name


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
            )
            for turn in turns
        ],
    )


def test_usage_get_matches_two_recorded_turns_on_two_threads(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_model_name, UsageRunner()),
        )
        first = await _record_turn(dispatcher, "First")
        second = await _record_turn(dispatcher, "Second")

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
            turns=TurnConfig(fixed_model_name, UsageRunner()),
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
        dispatcher = build_dispatcher(
            loaded.manifest.state_dir,
            turns=TurnConfig(fixed_model_name, UsageRunner()),
        )
        return await _record_turn(dispatcher, "First")

    recorded = asyncio.run(record_usage())

    exit_code = main(["usage", str(instance)])

    output = capsys.readouterr()
    assert exit_code == 0
    assert output.err == ""
    assert output.out.splitlines() == [
        f"thread {recorded.thread_id}: input=11 output=7 total=18",
        f"  turn {recorded.turn_id}: input=11 output=7 total=18",
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
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_model_name, UsageRunner()),
        )
        earlier = await _record_turn(dispatcher, "Earlier")
        await asyncio.sleep(0.001)
        included = await _record_turn(dispatcher, "Included", earlier.thread_id)

        result = await dispatcher.dispatch(
            "usage.get",
            {
                "since": included.completed_at,
                "until": included.completed_at,
            },
            {Scope.INSTANCE_READ},
        )

        assert result == UsageGetResult(threads=[_thread_usage(included)])

    asyncio.run(scenario())
