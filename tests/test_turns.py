import asyncio
import json
from collections.abc import Iterator
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from kinby.contracts import (
    AcceptedResult,
    ApprovalRequested,
    ErrorCode,
    ErrorEnvelope,
    Event,
    EventType,
    MessageDelta,
    ModePinned,
    Payload,
    PermissionMode,
    Scope,
    ThreadCreateResult,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnRated,
    TurnStarted,
    TurnVerdict,
)
from kinby.core.budgets import daily_cost
from kinby.core.dispatcher import Dispatcher, TurnConfig, build_dispatcher
from kinby.core.events import EventLog
from kinby.core.pricing import price_map
from kinby.core.turn_metrics import UnpricedModel
from kinby.core.turn_runner import LangGraphRunner
from kinby.core.turns import (
    ApprovalDecision,
    Emit,
    ParkedTurn,
    TurnOutcome,
    TurnPreparation,
    TurnRequest,
)
from kinby.instance import Budgets, init_instance, load_instance
from tests.helpers import (
    cannot_restore,
    does_not_park,
    fixed_permission_ceiling,
    fixed_turn_preparation,
)

_APPROVAL_ID = UUID("11111111-1111-1111-1111-111111111111")


def _daily_budget_preparation(
    event_log: EventLog,
    limit: float,
    *,
    model: str = "openai:gpt-5",
) -> TurnPreparation:
    prices = price_map()
    return fixed_turn_preparation(
        model=model,
        daily_cost=daily_cost(
            event_log.all_events(),
            prices,
            datetime.now(UTC).date(),
        ),
        unpriced_model=UnpricedModel(model) if model not in prices else None,
        budgets=Budgets(usd_per_day=limit),
    )


class ScriptedRunner:
    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
        assert turn.message == "Hello"
        await emit(MessageDelta(text="Hi"))
        await emit(MessageDelta(text=" there"))
        return TurnOutcome(input_tokens=4, output_tokens=2)

    resume = does_not_park
    restore = cannot_restore


class WaitingRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
        self.started.set()
        await self.release.wait()
        return TurnOutcome()

    resume = does_not_park
    restore = cannot_restore


class ModeRecordingRunner:
    def __init__(self) -> None:
        self.modes: list[PermissionMode] = []

    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
        self.modes.append(turn.permission_mode)
        return TurnOutcome()

    resume = does_not_park
    restore = cannot_restore


class FailingRunner:
    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
        raise RuntimeError("provider unavailable")

    resume = does_not_park
    restore = cannot_restore


class ParkingRunner:
    def __init__(self, parked: TurnRequest | None = None) -> None:
        self.parked = parked
        self.resumed_modes: list[PermissionMode] = []

    async def restore(self, thread_id: UUID, turn_id: UUID) -> TurnRequest | None:
        if (
            self.parked is not None
            and self.parked.thread_id == thread_id
            and self.parked.turn_id == turn_id
        ):
            return self.parked
        return None

    async def run(self, turn: TurnRequest, emit: Emit) -> ParkedTurn:
        self.parked = turn
        await emit(
            ApprovalRequested(
                approval_id=_APPROVAL_ID,
                name="continue_turn",
                arguments={},
                rule="scripted",
            )
        )
        return ParkedTurn()

    async def resume(
        self,
        turn: TurnRequest,
        decision: ApprovalDecision,
        emit: Emit,
    ) -> TurnOutcome:
        assert decision is ApprovalDecision.APPROVE
        self.resumed_modes.append(turn.permission_mode)
        await emit(MessageDelta(text="Approved"))
        return TurnOutcome(input_tokens=3, output_tokens=1)


class PausingRestoreRunner(ParkingRunner):
    def __init__(self, parked: TurnRequest) -> None:
        super().__init__(parked)
        self.restore_started = asyncio.Event()
        self.release_restore = asyncio.Event()
        self.restore_calls = 0
        self.resume_calls = 0

    async def restore(self, thread_id: UUID, turn_id: UUID) -> TurnRequest | None:
        self.restore_calls += 1
        self.restore_started.set()
        await self.release_restore.wait()
        return await super().restore(thread_id, turn_id)

    async def resume(
        self,
        turn: TurnRequest,
        decision: ApprovalDecision,
        emit: Emit,
    ) -> TurnOutcome:
        self.resume_calls += 1
        return await super().resume(turn, decision, emit)


class CancellationSuppressingRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def run(self, turn: TurnRequest, emit: Emit) -> TurnOutcome:
        await emit(MessageDelta(text="Before interrupt"))
        self.started.set()
        with suppress(asyncio.CancelledError):
            await asyncio.Event().wait()
        await emit(MessageDelta(text="After interrupt"))
        return TurnOutcome()

    resume = does_not_park
    restore = cannot_restore


class PausingEventLog(EventLog):
    def __init__(self, state_dir: Path) -> None:
        super().__init__(state_dir)
        self.turns_started = 0
        self.release = asyncio.Event()

    async def append(
        self,
        thread_id: UUID,
        turn_id: UUID,
        payload: Payload,
    ) -> Event:
        if isinstance(payload, TurnStarted):
            self.turns_started += 1
            await self.release.wait()
        return await super().append(thread_id, turn_id, payload)


class HistoricalEventLog(EventLog):
    def __init__(self, state_dir: Path, history: list[Event]) -> None:
        super().__init__(state_dir)
        self._history = history

    def all_events(self) -> Iterator[Event]:
        yield from self._history
        yield from super().all_events()


def _historical_turn(closed_at: datetime, model: str) -> list[Event]:
    thread_id = uuid4()
    turn_id = uuid4()
    return [
        Event(
            sequence=1,
            thread_id=thread_id,
            turn_id=turn_id,
            timestamp=closed_at - timedelta(seconds=1),
            payload=TurnStarted(message="Earlier", model=model),
        ),
        Event(
            sequence=2,
            thread_id=thread_id,
            turn_id=turn_id,
            timestamp=closed_at,
            payload=TurnCompleted(input_tokens=4, output_tokens=2),
        ),
    ]


def test_set_mode_requires_thread_admin_scope(tmp_path: Path) -> None:
    dispatcher = build_dispatcher(
        tmp_path,
        turns=TurnConfig(
            fixed_turn_preparation,
            fixed_permission_ceiling,
            ScriptedRunner(),
        ),
    )

    denied = asyncio.run(dispatcher.dispatch("thread.mode.set", {"unexpected": True}, set()))

    assert denied == ErrorEnvelope(
        code=ErrorCode.PERMISSION_DENIED,
        message='Missing required scope "thread:admin".',
        retryable=False,
    )


def test_pinned_mode_is_recorded_and_used_by_the_next_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = ModeRecordingRunner()
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, runner),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)

        pinned = await dispatcher.dispatch(
            "thread.mode.set",
            {"thread_id": created.id, "mode": "auto"},
            {Scope.THREAD_ADMIN},
        )
        assert isinstance(pinned, AcceptedResult)
        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id},
            {Scope.THREAD_READ},
        )
        events = [await asyncio.wait_for(anext(subscription), timeout=1) for _ in range(3)]
        await subscription.aclose()

        typed_events = cast(list[Event], events)
        assert typed_events[0].payload == ModePinned(mode=PermissionMode.AUTO)
        assert typed_events[-1].payload == TurnCompleted(input_tokens=0, output_tokens=0)
        assert runner.modes == [PermissionMode.AUTO]

    asyncio.run(scenario())


def test_set_mode_rejects_a_mode_above_the_instance_ceiling(tmp_path: Path) -> None:
    dispatcher = build_dispatcher(
        tmp_path,
        turns=TurnConfig(
            lambda: TurnPreparation(
                model="openai:gpt-5",
                default_mode=PermissionMode.ASK,
                ceiling=PermissionMode.AUTO,
            ),
            lambda: PermissionMode.AUTO,
            ScriptedRunner(),
        ),
    )
    created = asyncio.run(dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE}))
    assert isinstance(created, ThreadCreateResult)

    denied = asyncio.run(
        dispatcher.dispatch(
            "thread.mode.set",
            {"thread_id": created.id, "mode": "full-access"},
            {Scope.THREAD_ADMIN},
        )
    )

    assert denied == ErrorEnvelope(
        code=ErrorCode.PERMISSION_DENIED,
        message='Permission mode "full-access" exceeds the instance ceiling "auto".',
        retryable=False,
    )


def test_set_mode_rejects_a_thread_with_a_running_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = WaitingRunner()
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, runner),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        await asyncio.wait_for(runner.started.wait(), timeout=1)

        pinned = await dispatcher.dispatch(
            "thread.mode.set",
            {"thread_id": created.id, "mode": "auto"},
            {Scope.THREAD_ADMIN},
        )

        assert pinned == ErrorEnvelope(
            code=ErrorCode.THREAD_BUSY,
            message=f'Thread "{created.id}" already has a running turn.',
            retryable=True,
        )
        runner.release.set()

    asyncio.run(scenario())


def test_lowered_ceiling_constrains_an_existing_pin(tmp_path: Path) -> None:
    async def scenario() -> None:
        ceiling = PermissionMode.FULL_ACCESS

        def prepare_for_turn() -> TurnPreparation:
            return TurnPreparation(
                model="openai:gpt-5",
                default_mode=PermissionMode.ASK,
                ceiling=ceiling,
            )

        runner = ModeRecordingRunner()
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(prepare_for_turn, lambda: ceiling, runner),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        pinned = await dispatcher.dispatch(
            "thread.mode.set",
            {"thread_id": created.id, "mode": "full-access"},
            {Scope.THREAD_ADMIN},
        )
        assert isinstance(pinned, AcceptedResult)

        ceiling = PermissionMode.ASK
        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id, "after_sequence": started.sequence},
            {Scope.THREAD_READ},
        )
        completed = await asyncio.wait_for(anext(subscription), timeout=1)
        await subscription.aclose()

        assert isinstance(completed, Event)
        assert completed.type is EventType.TURN_COMPLETED
        assert runner.modes == [PermissionMode.ASK]

    asyncio.run(scenario())


def test_pinned_mode_survives_a_dispatcher_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ScriptedRunner(),
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        pinned = await dispatcher.dispatch(
            "thread.mode.set",
            {"thread_id": created.id, "mode": "read-only"},
            {Scope.THREAD_ADMIN},
        )
        assert isinstance(pinned, AcceptedResult)

        runner = ModeRecordingRunner()
        resumed = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, runner),
        )
        started = await resumed.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        subscription = resumed.subscribe(
            "thread.subscribe",
            {"thread_id": created.id, "after_sequence": started.sequence},
            {Scope.THREAD_READ},
        )
        completed = await asyncio.wait_for(anext(subscription), timeout=1)
        await subscription.aclose()

        assert isinstance(completed, Event)
        assert completed.payload == TurnCompleted(input_tokens=0, output_tokens=0)
        assert runner.modes == [PermissionMode.READ_ONLY]

    asyncio.run(scenario())


def test_unpinned_thread_uses_the_instance_default_mode(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = ModeRecordingRunner()
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                lambda: TurnPreparation(
                    model="openai:gpt-5",
                    default_mode=PermissionMode.AUTO,
                    ceiling=PermissionMode.FULL_ACCESS,
                ),
                fixed_permission_ceiling,
                runner,
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)

        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id, "after_sequence": started.sequence},
            {Scope.THREAD_READ},
        )
        completed = await asyncio.wait_for(anext(subscription), timeout=1)
        await subscription.aclose()

        assert isinstance(completed, Event)
        assert completed.type is EventType.TURN_COMPLETED
        assert runner.modes == [PermissionMode.AUTO]

    asyncio.run(scenario())


def test_turn_streams_and_replays_through_the_dispatcher(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ScriptedRunner(),
            ),
        )
        created = await dispatcher.dispatch(
            "thread.create",
            {"title": "Chat"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(created, ThreadCreateResult)

        accepted = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(accepted, AcceptedResult)
        assert accepted.thread_id == created.id
        assert accepted.sequence == 1

        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id},
            {Scope.THREAD_READ},
        )
        events = [await asyncio.wait_for(anext(subscription), timeout=1) for _ in range(4)]
        await subscription.aclose()

        typed_events = cast(list[Event], events)
        assert [event.type for event in typed_events] == [
            EventType.TURN_STARTED,
            EventType.MESSAGE_DELTA,
            EventType.MESSAGE_DELTA,
            EventType.TURN_COMPLETED,
        ]
        assert [event.sequence for event in typed_events] == [1, 2, 3, 4]
        assert typed_events[0].payload == TurnStarted(
            message="Hello",
            model="openai:gpt-5",
            permission_mode=PermissionMode.ASK,
        )
        assert typed_events[1].payload == MessageDelta(text="Hi")
        assert typed_events[2].payload == MessageDelta(text=" there")
        assert typed_events[3].payload == TurnCompleted(input_tokens=4, output_tokens=2)

        replay = build_dispatcher(tmp_path).subscribe(
            "thread.subscribe",
            {"thread_id": created.id, "after_sequence": 0},
            {Scope.THREAD_READ},
        )
        replayed = [await asyncio.wait_for(anext(replay), timeout=1) for _ in events]
        await replay.aclose()

        assert replayed == events

    asyncio.run(scenario())


def test_daily_cost_budget_refuses_a_turn_at_the_limit_without_an_event(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        event_log = EventLog(tmp_path)

        def prepare_for_turn() -> TurnPreparation:
            return _daily_budget_preparation(event_log, 0.000025)

        dispatcher = build_dispatcher(
            tmp_path,
            event_log=event_log,
            turns=TurnConfig(
                prepare_for_turn,
                fixed_permission_ceiling,
                ScriptedRunner(),
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)

        accepted = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(accepted, AcceptedResult)
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id},
            {Scope.THREAD_READ},
        )
        events = [await asyncio.wait_for(anext(subscription), timeout=1) for _ in range(4)]
        await subscription.aclose()
        assert isinstance(events[-1], Event)
        assert isinstance(events[-1].payload, TurnCompleted)

        refused = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )

        assert refused == ErrorEnvelope(
            code=ErrorCode.BUDGET_EXCEEDED,
            message="The daily cost reached the usd_per_day budget of 0.000025.",
            retryable=False,
        )
        no_new_events = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id, "after_sequence": events[-1].sequence},
            {Scope.THREAD_READ},
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(anext(no_new_events), timeout=0.01)
        await no_new_events.aclose()

    asyncio.run(scenario())


def test_daily_cost_budget_refuses_an_unpriced_model_but_unbounded_turns_run(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        bounded_path = tmp_path / "bounded"
        bounded_log = EventLog(bounded_path)
        bounded = build_dispatcher(
            bounded_path,
            event_log=bounded_log,
            turns=TurnConfig(
                lambda: _daily_budget_preparation(
                    bounded_log,
                    1,
                    model="other:model",
                ),
                fixed_permission_ceiling,
                ScriptedRunner(),
            ),
        )
        bounded_thread = await bounded.dispatch(
            "thread.create",
            {},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(bounded_thread, ThreadCreateResult)

        refused = await bounded.dispatch(
            "thread.turn.start",
            {"thread_id": bounded_thread.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )

        assert refused == ErrorEnvelope(
            code=ErrorCode.MODEL_UNPRICED,
            message=('Model "other:model" has no price. Add [prices."other:model"] to kinby.toml.'),
            retryable=False,
        )

        unbounded_path = tmp_path / "unbounded"
        unbounded = build_dispatcher(
            unbounded_path,
            turns=TurnConfig(
                lambda: TurnPreparation(
                    model="other:model",
                    default_mode=PermissionMode.ASK,
                    ceiling=PermissionMode.FULL_ACCESS,
                ),
                fixed_permission_ceiling,
                ScriptedRunner(),
            ),
        )
        unbounded_thread = await unbounded.dispatch(
            "thread.create",
            {},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(unbounded_thread, ThreadCreateResult)
        accepted = await unbounded.dispatch(
            "thread.turn.start",
            {"thread_id": unbounded_thread.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )

        assert isinstance(accepted, AcceptedResult)

    asyncio.run(scenario())


def test_daily_cost_budget_does_not_count_a_turn_closed_yesterday(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        event_log = HistoricalEventLog(
            tmp_path,
            _historical_turn(datetime.now(UTC) - timedelta(days=1), "openai:gpt-5"),
        )

        def prepare_for_turn() -> TurnPreparation:
            return _daily_budget_preparation(event_log, 0.000025)

        dispatcher = build_dispatcher(
            tmp_path,
            event_log=event_log,
            turns=TurnConfig(
                prepare_for_turn,
                fixed_permission_ceiling,
                ScriptedRunner(),
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)

        accepted = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )

        assert isinstance(accepted, AcceptedResult)

    asyncio.run(scenario())


def test_daily_cost_budget_refuses_an_unpriced_model_from_a_turn_closed_today(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        event_log = HistoricalEventLog(
            tmp_path,
            _historical_turn(datetime.now(UTC), "other:model"),
        )

        def prepare_for_turn() -> TurnPreparation:
            return _daily_budget_preparation(event_log, 1)

        dispatcher = build_dispatcher(
            tmp_path,
            event_log=event_log,
            turns=TurnConfig(
                prepare_for_turn,
                fixed_permission_ceiling,
                ScriptedRunner(),
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)

        refused = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )

        assert refused == ErrorEnvelope(
            code=ErrorCode.MODEL_UNPRICED,
            message=('Model "other:model" has no price. Add [prices."other:model"] to kinby.toml.'),
            retryable=False,
        )

    asyncio.run(scenario())


def test_turn_preparation_uses_the_event_log_and_manifest_price_override(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        instance_path = tmp_path / "priced"
        init_instance(instance_path, model="other:model")
        with (instance_path / "kinby.toml").open("a", encoding="utf-8") as manifest:
            manifest.write(
                "\n[budgets]\nusd_per_day = 0.000025\n"
                '\n[prices."other:model"]\ninput = 1.25\noutput = 10\n'
            )
        instance = load_instance(instance_path)
        event_log = HistoricalEventLog(
            instance.manifest.state_dir,
            _historical_turn(datetime.now(UTC), "other:model"),
        )
        runner = LangGraphRunner(instance, event_log=event_log)
        dispatcher = build_dispatcher(
            instance.manifest.state_dir,
            event_log=event_log,
            turns=TurnConfig(
                runner.prepare_for_turn,
                runner.permission_ceiling,
                ScriptedRunner(),
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)

        refused = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )

        assert refused == ErrorEnvelope(
            code=ErrorCode.BUDGET_EXCEEDED,
            message="The daily cost reached the usd_per_day budget of 0.000025.",
            retryable=False,
        )

    asyncio.run(scenario())


def test_completed_turn_can_be_rated_through_the_dispatcher(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ScriptedRunner(),
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id},
            {Scope.THREAD_READ},
        )
        for _ in range(4):
            await asyncio.wait_for(anext(subscription), timeout=1)

        accepted = await dispatcher.dispatch(
            "thread.turn.rate",
            {
                "thread_id": created.id,
                "turn_id": started.turn_id,
                "verdict": "good",
                "reason": "Answered the question directly.",
            },
            {Scope.THREAD_RATE},
        )
        rated = await asyncio.wait_for(anext(subscription), timeout=1)
        await subscription.aclose()

        assert accepted == AcceptedResult(
            thread_id=created.id,
            turn_id=started.turn_id,
            sequence=5,
        )
        assert isinstance(rated, Event)
        assert rated.payload == TurnRated(
            verdict=TurnVerdict.GOOD,
            reason="Answered the question directly.",
        )

    asyncio.run(scenario())


def test_running_turn_cannot_be_rated(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = WaitingRunner()
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, runner),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        await asyncio.wait_for(runner.started.wait(), timeout=1)

        rated = await dispatcher.dispatch(
            "thread.turn.rate",
            {
                "thread_id": created.id,
                "turn_id": started.turn_id,
                "verdict": "bad",
            },
            {Scope.THREAD_RATE},
        )

        assert rated == ErrorEnvelope(
            code=ErrorCode.TURN_OPEN,
            message=f'Turn "{started.turn_id}" is still open.',
            retryable=False,
        )
        runner.release.set()

    asyncio.run(scenario())


def test_unknown_turn_or_thread_cannot_be_rated(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ScriptedRunner(),
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id},
            {Scope.THREAD_READ},
        )
        for _ in range(4):
            await asyncio.wait_for(anext(subscription), timeout=1)
        await subscription.aclose()

        unknown_turn = UUID("22222222-2222-2222-2222-222222222222")
        unknown_thread = UUID("33333333-3333-3333-3333-333333333333")
        for thread_id, turn_id in (
            (created.id, unknown_turn),
            (unknown_thread, started.turn_id),
        ):
            rated = await dispatcher.dispatch(
                "thread.turn.rate",
                {
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "verdict": "bad",
                },
                {Scope.THREAD_RATE},
            )

            assert rated == ErrorEnvelope(
                code=ErrorCode.NOT_FOUND,
                message=f'Turn "{turn_id}" was not found on thread "{thread_id}".',
                retryable=False,
            )

    asyncio.run(scenario())


def test_non_turn_operation_cannot_be_rated(tmp_path: Path) -> None:
    dispatcher = build_dispatcher(
        tmp_path,
        turns=TurnConfig(
            fixed_turn_preparation,
            fixed_permission_ceiling,
            ScriptedRunner(),
        ),
    )
    created = asyncio.run(dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE}))
    assert isinstance(created, ThreadCreateResult)
    pinned = asyncio.run(
        dispatcher.dispatch(
            "thread.mode.set",
            {"thread_id": created.id, "mode": "auto"},
            {Scope.THREAD_ADMIN},
        )
    )
    assert isinstance(pinned, AcceptedResult)

    rated = asyncio.run(
        dispatcher.dispatch(
            "thread.turn.rate",
            {
                "thread_id": created.id,
                "turn_id": pinned.turn_id,
                "verdict": "good",
            },
            {Scope.THREAD_RATE},
        )
    )

    assert rated == ErrorEnvelope(
        code=ErrorCode.NOT_FOUND,
        message=f'Turn "{pinned.turn_id}" was not found on thread "{created.id}".',
        retryable=False,
    )


def test_rate_turn_requires_thread_rate_scope(tmp_path: Path) -> None:
    dispatcher = build_dispatcher(tmp_path)

    denied = asyncio.run(dispatcher.dispatch("thread.turn.rate", {}, set()))

    assert denied == ErrorEnvelope(
        code=ErrorCode.PERMISSION_DENIED,
        message='Missing required scope "thread:rate".',
        retryable=False,
    )


def test_dispatcher_without_turn_service_appends_a_second_rating(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ScriptedRunner(),
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id},
            {Scope.THREAD_READ},
        )
        for _ in range(4):
            await asyncio.wait_for(anext(subscription), timeout=1)
        await subscription.aclose()
        first = await dispatcher.dispatch(
            "thread.turn.rate",
            {
                "thread_id": created.id,
                "turn_id": started.turn_id,
                "verdict": "good",
            },
            {Scope.THREAD_RATE},
        )
        assert isinstance(first, AcceptedResult)

        restarted = build_dispatcher(tmp_path)
        second = await restarted.dispatch(
            "thread.turn.rate",
            {
                "thread_id": created.id,
                "turn_id": started.turn_id,
                "verdict": "bad",
                "reason": "I changed my mind.",
            },
            {Scope.THREAD_RATE},
        )

        assert isinstance(second, AcceptedResult)
        assert second.sequence == first.sequence + 1
        ratings = [
            event.payload
            for event in EventLog(tmp_path).stored(created.id)
            if isinstance(event.payload, TurnRated)
        ]
        assert ratings == [
            TurnRated(verdict=TurnVerdict.GOOD),
            TurnRated(verdict=TurnVerdict.BAD, reason="I changed my mind."),
        ]

    asyncio.run(scenario())


def test_start_rejects_a_second_turn_while_the_first_is_running(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = WaitingRunner()
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, runner),
        )
        created = await dispatcher.dispatch(
            "thread.create",
            {},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(created, ThreadCreateResult)

        first = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "First"},
            {Scope.THREAD_OPERATE},
        )
        await asyncio.wait_for(runner.started.wait(), timeout=1)
        second = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Second"},
            {Scope.THREAD_OPERATE},
        )

        assert isinstance(first, AcceptedResult)
        assert second == ErrorEnvelope(
            code=ErrorCode.THREAD_BUSY,
            message=f'Thread "{created.id}" already has a running turn.',
            retryable=True,
        )

        runner.release.set()
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id, "after_sequence": 1},
            {Scope.THREAD_READ},
        )
        completed = await asyncio.wait_for(anext(subscription), timeout=1)
        await subscription.aclose()
        assert isinstance(completed, Event)
        assert completed.type is EventType.TURN_COMPLETED

    asyncio.run(scenario())


def test_interrupt_ends_the_running_turn_and_allows_another(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = WaitingRunner()
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, runner),
        )
        created = await dispatcher.dispatch(
            "thread.create",
            {},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(created, ThreadCreateResult)

        first = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "First"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(first, AcceptedResult)
        await asyncio.wait_for(runner.started.wait(), timeout=1)

        interrupted = await dispatcher.dispatch(
            "thread.turn.interrupt",
            {"thread_id": created.id},
            {Scope.THREAD_OPERATE},
        )

        assert interrupted == AcceptedResult(
            thread_id=created.id,
            turn_id=first.turn_id,
            sequence=2,
        )
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id},
            {Scope.THREAD_READ},
        )
        events = [await asyncio.wait_for(anext(subscription), timeout=1) for _ in range(2)]
        await subscription.aclose()
        assert [event.type for event in cast(list[Event], events)] == [
            EventType.TURN_STARTED,
            EventType.TURN_INTERRUPTED,
        ]
        assert cast(list[Event], events)[1].payload == TurnInterrupted()

        second = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Second"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(second, AcceptedResult)
        runner.release.set()

    asyncio.run(scenario())


def test_interrupt_rejects_a_thread_with_no_active_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ScriptedRunner(),
            ),
        )
        created = await dispatcher.dispatch(
            "thread.create",
            {},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(created, ThreadCreateResult)

        result = await dispatcher.dispatch(
            "thread.turn.interrupt",
            {"thread_id": created.id},
            {Scope.THREAD_OPERATE},
        )

        assert result == ErrorEnvelope(
            code=ErrorCode.NO_ACTIVE_TURN,
            message=f'Thread "{created.id}" has no active turn.',
            retryable=False,
        )

    asyncio.run(scenario())


def test_interrupt_ends_the_turn_when_the_runner_suppresses_cancellation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runner = CancellationSuppressingRunner()
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, runner),
        )
        created = await dispatcher.dispatch(
            "thread.create",
            {},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(created, ThreadCreateResult)
        await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        await asyncio.wait_for(runner.started.wait(), timeout=1)

        interrupted = await dispatcher.dispatch(
            "thread.turn.interrupt",
            {"thread_id": created.id},
            {Scope.THREAD_OPERATE},
        )

        assert isinstance(interrupted, AcceptedResult)
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id},
            {Scope.THREAD_READ},
        )
        events = [await asyncio.wait_for(anext(subscription), timeout=1) for _ in range(3)]
        await subscription.aclose()
        assert [event.type for event in cast(list[Event], events)] == [
            EventType.TURN_STARTED,
            EventType.MESSAGE_DELTA,
            EventType.TURN_INTERRUPTED,
        ]
        assert cast(list[Event], events)[-1].payload == TurnInterrupted()

    asyncio.run(scenario())


def test_start_rejects_a_concurrent_turn_before_recording_acceptance(tmp_path: Path) -> None:
    async def scenario() -> None:
        event_log = PausingEventLog(tmp_path)
        runner = WaitingRunner()
        dispatcher = build_dispatcher(
            tmp_path,
            event_log=event_log,
            turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, runner),
        )
        created = await dispatcher.dispatch(
            "thread.create",
            {},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(created, ThreadCreateResult)

        calls = [
            asyncio.create_task(
                dispatcher.dispatch(
                    "thread.turn.start",
                    {"thread_id": created.id, "message": message},
                    {Scope.THREAD_OPERATE},
                )
            )
            for message in ("First", "Second")
        ]
        for _ in range(5):
            await asyncio.sleep(0)
        event_log.release.set()
        results = await asyncio.gather(*calls)
        runner.release.set()

        assert sum(isinstance(result, AcceptedResult) for result in results) == 1
        busy = next(result for result in results if isinstance(result, ErrorEnvelope))
        assert busy.code is ErrorCode.THREAD_BUSY

    asyncio.run(scenario())


def test_failed_model_turn_ends_with_the_error_code(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                FailingRunner(),
            ),
        )
        created = await dispatcher.dispatch(
            "thread.create",
            {},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(created, ThreadCreateResult)

        await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id},
            {Scope.THREAD_READ},
        )
        started = await asyncio.wait_for(anext(subscription), timeout=1)
        failed = await asyncio.wait_for(anext(subscription), timeout=1)
        await subscription.aclose()

        assert isinstance(started, Event)
        assert started.type is EventType.TURN_STARTED
        assert isinstance(failed, Event)
        assert failed.type is EventType.TURN_FAILED
        assert failed.payload == TurnFailed(
            code=ErrorCode.INTERNAL,
            message="The model turn failed unexpectedly.",
        )

    asyncio.run(scenario())


async def _park_turn(
    tmp_path: Path,
) -> tuple[Dispatcher, ThreadCreateResult, AcceptedResult, Event]:
    dispatcher = build_dispatcher(
        tmp_path,
        turns=TurnConfig(
            fixed_turn_preparation,
            fixed_permission_ceiling,
            ParkingRunner(),
        ),
    )
    created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
    assert isinstance(created, ThreadCreateResult)
    accepted = await dispatcher.dispatch(
        "thread.turn.start",
        {"thread_id": created.id, "message": "Hello"},
        {Scope.THREAD_OPERATE},
    )
    assert isinstance(accepted, AcceptedResult)
    subscription = dispatcher.subscribe(
        "thread.subscribe",
        {"thread_id": created.id},
        {Scope.THREAD_READ},
    )
    await asyncio.wait_for(anext(subscription), timeout=1)
    requested = await asyncio.wait_for(anext(subscription), timeout=1)
    await subscription.aclose()
    assert isinstance(requested, Event)
    assert requested.type is EventType.APPROVAL_REQUESTED
    return dispatcher, created, accepted, requested


def test_parked_approval_resumes_after_dispatcher_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, created, accepted, requested = await _park_turn(tmp_path)
        assert requested.payload == ApprovalRequested(
            approval_id=_APPROVAL_ID,
            name="continue_turn",
            arguments={},
            rule="scripted",
        )

        restarted = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ParkingRunner(
                    TurnRequest(
                        thread_id=created.id,
                        turn_id=accepted.turn_id,
                        message="Hello",
                        model=fixed_turn_preparation().model,
                        permission_mode=PermissionMode.ASK,
                    )
                ),
            ),
        )
        resumed = await restarted.dispatch(
            "thread.approval.respond",
            {
                "thread_id": created.id,
                "approval_id": _APPROVAL_ID,
                "answer": "yes",
            },
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(resumed, AcceptedResult)
        assert resumed.thread_id == created.id
        assert resumed.turn_id == accepted.turn_id

        live = restarted.subscribe(
            "thread.subscribe",
            {"thread_id": created.id, "after_sequence": requested.sequence},
            {Scope.THREAD_READ},
        )
        delta = await asyncio.wait_for(anext(live), timeout=1)
        completed = await asyncio.wait_for(anext(live), timeout=1)
        await live.aclose()

        assert isinstance(delta, Event)
        assert delta.type is EventType.MESSAGE_DELTA
        assert delta.payload == MessageDelta(text="Approved")
        assert isinstance(completed, Event)
        assert completed.type is EventType.TURN_COMPLETED
        assert completed.payload == TurnCompleted(input_tokens=3, output_tokens=1)

    asyncio.run(scenario())


def test_concurrent_approval_responses_resume_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, created, accepted, requested = await _park_turn(tmp_path)
        runner = PausingRestoreRunner(
            TurnRequest(
                thread_id=created.id,
                turn_id=accepted.turn_id,
                message="Hello",
                model=fixed_turn_preparation().model,
                permission_mode=PermissionMode.ASK,
            )
        )
        restarted = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, runner),
        )
        command = {
            "thread_id": created.id,
            "approval_id": _APPROVAL_ID,
            "answer": "yes",
        }
        first_response = asyncio.create_task(
            restarted.dispatch(
                "thread.approval.respond",
                command,
                {Scope.THREAD_OPERATE},
            )
        )
        await asyncio.wait_for(runner.restore_started.wait(), timeout=1)

        second_response = await restarted.dispatch(
            "thread.approval.respond",
            command,
            {Scope.THREAD_OPERATE},
        )
        runner.release_restore.set()
        first_result = await first_response

        assert isinstance(first_result, AcceptedResult)
        assert second_response == ErrorEnvelope(
            code=ErrorCode.THREAD_BUSY,
            message=f'Thread "{created.id}" already has a running turn.',
            retryable=True,
        )
        live = restarted.subscribe(
            "thread.subscribe",
            {"thread_id": created.id, "after_sequence": requested.sequence},
            {Scope.THREAD_READ},
        )
        await asyncio.wait_for(anext(live), timeout=1)
        await asyncio.wait_for(anext(live), timeout=1)
        await live.aclose()
        assert runner.restore_calls == 1
        assert runner.resume_calls == 1

    asyncio.run(scenario())


def test_interrupt_during_approval_restore_prevents_resume(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, created, accepted, requested = await _park_turn(tmp_path)
        runner = PausingRestoreRunner(
            TurnRequest(
                thread_id=created.id,
                turn_id=accepted.turn_id,
                message="Hello",
                model=fixed_turn_preparation().model,
                permission_mode=PermissionMode.ASK,
            )
        )
        restarted = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, runner),
        )
        response = asyncio.create_task(
            restarted.dispatch(
                "thread.approval.respond",
                {
                    "thread_id": created.id,
                    "approval_id": _APPROVAL_ID,
                    "answer": "yes",
                },
                {Scope.THREAD_OPERATE},
            )
        )
        await asyncio.wait_for(runner.restore_started.wait(), timeout=1)

        interrupted = await restarted.dispatch(
            "thread.turn.interrupt",
            {"thread_id": created.id},
            {Scope.THREAD_OPERATE},
        )
        runner.release_restore.set()
        response_result = await response

        assert interrupted == AcceptedResult(
            thread_id=created.id,
            turn_id=accepted.turn_id,
            sequence=requested.sequence + 1,
        )
        assert response_result == ErrorEnvelope(
            code=ErrorCode.NO_ACTIVE_TURN,
            message=f'Thread "{created.id}" has no active turn.',
            retryable=False,
        )
        assert runner.restore_calls == 1
        assert runner.resume_calls == 0

    asyncio.run(scenario())


def test_parked_turn_uses_checkpoint_mode_when_event_lacks_mode(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, created, accepted, requested = await _park_turn(tmp_path)
        records_path = tmp_path / "events.jsonl"
        records = [
            json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines()
        ]
        del records[0]["payload"]["permission_mode"]
        records_path.write_text(
            "".join(f"{json.dumps(record)}\n" for record in records),
            encoding="utf-8",
        )
        runner = ParkingRunner(
            TurnRequest(
                thread_id=created.id,
                turn_id=accepted.turn_id,
                message="Hello",
                model=fixed_turn_preparation().model,
                permission_mode=PermissionMode.ASK,
            )
        )
        restarted = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                lambda: TurnPreparation(
                    model="openai:gpt-5",
                    default_mode=PermissionMode.AUTO,
                    ceiling=PermissionMode.FULL_ACCESS,
                ),
                fixed_permission_ceiling,
                runner,
            ),
        )

        resumed = await restarted.dispatch(
            "thread.approval.respond",
            {
                "thread_id": created.id,
                "approval_id": _APPROVAL_ID,
                "answer": "yes",
            },
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(resumed, AcceptedResult)
        live = restarted.subscribe(
            "thread.subscribe",
            {"thread_id": created.id, "after_sequence": requested.sequence},
            {Scope.THREAD_READ},
        )
        await asyncio.wait_for(anext(live), timeout=1)
        await asyncio.wait_for(anext(live), timeout=1)
        await live.aclose()

        assert runner.resumed_modes == [PermissionMode.ASK]

    asyncio.run(scenario())


def test_set_mode_rejects_a_thread_with_a_pending_approval(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher, created, _, requested = await _park_turn(tmp_path)

        pinned = await dispatcher.dispatch(
            "thread.mode.set",
            {"thread_id": created.id, "mode": "auto"},
            {Scope.THREAD_ADMIN},
        )

        assert pinned == ErrorEnvelope(
            code=ErrorCode.THREAD_BUSY,
            message=f'Thread "{created.id}" already has a running turn.',
            retryable=True,
        )
        resumed = await dispatcher.dispatch(
            "thread.approval.respond",
            {
                "thread_id": created.id,
                "approval_id": _APPROVAL_ID,
                "answer": "yes",
            },
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(resumed, AcceptedResult)
        live = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id, "after_sequence": requested.sequence},
            {Scope.THREAD_READ},
        )
        await asyncio.wait_for(anext(live), timeout=1)
        await asyncio.wait_for(anext(live), timeout=1)
        await live.aclose()

    asyncio.run(scenario())


def test_parked_approval_resumes_with_the_turns_pinned_mode(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ParkingRunner(),
            ),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        pinned = await dispatcher.dispatch(
            "thread.mode.set",
            {"thread_id": created.id, "mode": "read-only"},
            {Scope.THREAD_ADMIN},
        )
        assert isinstance(pinned, AcceptedResult)
        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id, "after_sequence": started.sequence},
            {Scope.THREAD_READ},
        )
        requested = await asyncio.wait_for(anext(subscription), timeout=1)
        await subscription.aclose()
        assert isinstance(requested, Event)
        assert requested.type is EventType.APPROVAL_REQUESTED

        runner = ParkingRunner(
            TurnRequest(
                thread_id=created.id,
                turn_id=started.turn_id,
                message="Hello",
                model=fixed_turn_preparation().model,
                permission_mode=PermissionMode.READ_ONLY,
            )
        )
        restarted = build_dispatcher(
            tmp_path,
            turns=TurnConfig(fixed_turn_preparation, fixed_permission_ceiling, runner),
        )
        resumed = await restarted.dispatch(
            "thread.approval.respond",
            {
                "thread_id": created.id,
                "approval_id": _APPROVAL_ID,
                "answer": "yes",
            },
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(resumed, AcceptedResult)
        live = restarted.subscribe(
            "thread.subscribe",
            {"thread_id": created.id, "after_sequence": requested.sequence},
            {Scope.THREAD_READ},
        )
        await asyncio.wait_for(anext(live), timeout=1)
        await asyncio.wait_for(anext(live), timeout=1)
        await live.aclose()

        assert runner.resumed_modes == [PermissionMode.READ_ONLY]

    asyncio.run(scenario())


def test_lowered_ceiling_constrains_a_parked_turn_on_resume(tmp_path: Path) -> None:
    async def scenario() -> None:
        ceiling = PermissionMode.FULL_ACCESS

        def prepare_for_turn() -> TurnPreparation:
            return TurnPreparation(
                model="openai:gpt-5",
                default_mode=PermissionMode.AUTO,
                ceiling=ceiling,
            )

        runner = ParkingRunner()
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(prepare_for_turn, lambda: ceiling, runner),
        )
        created = await dispatcher.dispatch("thread.create", {}, {Scope.THREAD_OPERATE})
        assert isinstance(created, ThreadCreateResult)
        started = await dispatcher.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Hello"},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(started, AcceptedResult)
        subscription = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id, "after_sequence": started.sequence},
            {Scope.THREAD_READ},
        )
        requested = await asyncio.wait_for(anext(subscription), timeout=1)
        await subscription.aclose()
        assert isinstance(requested, Event)
        assert requested.type is EventType.APPROVAL_REQUESTED

        ceiling = PermissionMode.READ_ONLY
        resumed = await dispatcher.dispatch(
            "thread.approval.respond",
            {
                "thread_id": created.id,
                "approval_id": _APPROVAL_ID,
                "answer": "yes",
            },
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(resumed, AcceptedResult)
        live = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id, "after_sequence": requested.sequence},
            {Scope.THREAD_READ},
        )
        await asyncio.wait_for(anext(live), timeout=1)
        await asyncio.wait_for(anext(live), timeout=1)
        await live.aclose()

        assert runner.resumed_modes == [PermissionMode.READ_ONLY]

    asyncio.run(scenario())


def test_parked_approval_resumes_on_the_same_dispatcher(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher, created, accepted, requested = await _park_turn(tmp_path)
        resumed = await dispatcher.dispatch(
            "thread.approval.respond",
            {
                "thread_id": created.id,
                "approval_id": _APPROVAL_ID,
                "answer": "yes",
            },
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(resumed, AcceptedResult)
        assert resumed.turn_id == accepted.turn_id

        live = dispatcher.subscribe(
            "thread.subscribe",
            {"thread_id": created.id, "after_sequence": requested.sequence},
            {Scope.THREAD_READ},
        )
        delta = await asyncio.wait_for(anext(live), timeout=1)
        completed = await asyncio.wait_for(anext(live), timeout=1)
        await live.aclose()

        assert isinstance(delta, Event)
        assert delta.type is EventType.MESSAGE_DELTA
        assert isinstance(completed, Event)
        assert completed.type is EventType.TURN_COMPLETED

        stale = await dispatcher.dispatch(
            "thread.approval.respond",
            {
                "thread_id": created.id,
                "approval_id": _APPROVAL_ID,
                "answer": "yes",
            },
            {Scope.THREAD_OPERATE},
        )
        assert stale == ErrorEnvelope(
            code=ErrorCode.NO_ACTIVE_TURN,
            message=f'Thread "{created.id}" has no active turn.',
            retryable=False,
        )

    asyncio.run(scenario())


def test_unknown_approval_id_returns_not_found(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher, created, _, requested = await _park_turn(tmp_path)
        assert requested.type is EventType.APPROVAL_REQUESTED

        missing = await dispatcher.dispatch(
            "thread.approval.respond",
            {
                "thread_id": created.id,
                "approval_id": "22222222-2222-2222-2222-222222222222",
                "answer": "yes",
            },
            {Scope.THREAD_OPERATE},
        )
        assert missing == ErrorEnvelope(
            code=ErrorCode.NOT_FOUND,
            message='Approval "22222222-2222-2222-2222-222222222222" was not found.',
            retryable=False,
        )

    asyncio.run(scenario())


def test_start_rejects_a_new_turn_while_parked_after_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, created, _, requested = await _park_turn(tmp_path)
        assert requested.type is EventType.APPROVAL_REQUESTED

        restarted = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ParkingRunner(),
            ),
        )
        second = await restarted.dispatch(
            "thread.turn.start",
            {"thread_id": created.id, "message": "Second"},
            {Scope.THREAD_OPERATE},
        )
        assert second == ErrorEnvelope(
            code=ErrorCode.THREAD_BUSY,
            message=f'Thread "{created.id}" already has a running turn.',
            retryable=True,
        )

    asyncio.run(scenario())


def test_interrupt_ends_a_parked_turn_after_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, created, accepted, requested = await _park_turn(tmp_path)
        restarted = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ParkingRunner(),
            ),
        )

        interrupted = await restarted.dispatch(
            "thread.turn.interrupt",
            {"thread_id": created.id},
            {Scope.THREAD_OPERATE},
        )

        assert interrupted == AcceptedResult(
            thread_id=created.id,
            turn_id=accepted.turn_id,
            sequence=requested.sequence + 1,
        )
        subscription = restarted.subscribe(
            "thread.subscribe",
            {"thread_id": created.id, "after_sequence": requested.sequence},
            {Scope.THREAD_READ},
        )
        event = await asyncio.wait_for(anext(subscription), timeout=1)
        await subscription.aclose()
        assert isinstance(event, Event)
        assert event.payload == TurnInterrupted()

    asyncio.run(scenario())


def test_unknown_approval_id_with_no_active_turn_returns_not_found(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        dispatcher = build_dispatcher(
            tmp_path,
            turns=TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ParkingRunner(),
            ),
        )
        created = await dispatcher.dispatch(
            "thread.create",
            {},
            {Scope.THREAD_OPERATE},
        )
        assert isinstance(created, ThreadCreateResult)

        missing = await dispatcher.dispatch(
            "thread.approval.respond",
            {
                "thread_id": created.id,
                "approval_id": "11111111-1111-1111-1111-111111111111",
                "answer": "yes",
            },
            {Scope.THREAD_OPERATE},
        )
        assert missing == ErrorEnvelope(
            code=ErrorCode.NOT_FOUND,
            message='Approval "11111111-1111-1111-1111-111111111111" was not found.',
            retryable=False,
        )

    asyncio.run(scenario())
