import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest

from kinby.cli import main
from kinby.cli.client import ContractClient
from kinby.cli.repl import run_repl
from kinby.contracts import (
    AcceptedResult,
    CompletionOutcome,
    ContractModel,
    Delivery,
    DeliveryId,
    ErrorEnvelope,
    Event,
    MessageDelta,
    RoutineName,
    RoutineOrigin,
    RoutineTrigger,
    Scope,
    SignalReceived,
    ThreadListResult,
    TurnCompleted,
    TurnInterrupted,
    TurnStarted,
    is_turn_closing,
)
from kinby.core.dispatcher import (
    Dispatcher,
    ScheduledTurnConfig,
    TurnConfig,
    build_dispatcher,
)
from kinby.core.errors import BudgetExceeded, CodeStepFailed
from kinby.core.events import EventLog
from kinby.core.scheduler import SchedulerConfig
from kinby.core.turns import TurnOutcome
from kinby.instance import FeedbackPolicy, ManifestError, load_instance
from kinby.instance.schema import manifest_schema
from tests.helpers import (
    cannot_restore,
    does_not_park,
    fixed_permission_ceiling,
    fixed_turn_preparation,
)
from tests.test_routines import instance_at, routine_file


def test_routine_timezone_defaults_and_schema(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    assert instance.manifest.routines.timezone.key == "UTC"
    with (tmp_path / "kinby.toml").open("a") as manifest:
        manifest.write('[routines]\ntimezone = "Europe/Madrid"\n')
    assert load_instance(tmp_path).manifest.routines.timezone.key == "Europe/Madrid"
    assert '"routines"' in json.dumps(manifest_schema())


def test_serve_listen_parses_host_and_port(tmp_path: Path) -> None:
    instance = instance_at(tmp_path)
    assert instance.manifest.serve is None
    with (tmp_path / "kinby.toml").open("a") as manifest:
        manifest.write('[serve]\nlisten = "127.0.0.1:8484"\n')
    loaded = load_instance(tmp_path)
    assert loaded.manifest.serve is not None
    assert loaded.manifest.serve.host == "127.0.0.1"
    assert loaded.manifest.serve.port == 8484


@pytest.mark.parametrize(
    "setting,named",
    [
        ('listen = "not-an-address"', "listen"),
        ('listen = "bad host:8484"', "listen"),
        ('listen = "http://localhost:8484"', "listen"),
        ('listen = "foo/bar:8484"', "listen"),
        ('listen = "localhost:+8484"', "listen"),
        ('listen = "foo..bar:8484"', "listen"),
        ('listen = ".:8484"', "listen"),
        ('listen = "-:8484"', "listen"),
        ('listen = "127.0.0.1:0"', "listen"),
        ('listen = "127.0.0.1:65536"', "listen"),
        ('listen = "127.0.0.1:8484"\nsurprise = true', "surprise"),
    ],
)
def test_invalid_serve_settings_fail_load(tmp_path: Path, setting: str, named: str) -> None:
    instance_at(tmp_path)
    with (tmp_path / "kinby.toml").open("a") as manifest:
        manifest.write(f"[serve]\n{setting}\n")
    with pytest.raises(ManifestError, match=named):
        load_instance(tmp_path)


@pytest.mark.parametrize("setting", ['timezone = "Unknown/Zone"', "surprise = true"])
def test_invalid_routine_settings_fail_load(tmp_path: Path, setting: str) -> None:
    instance_at(tmp_path)
    with (tmp_path / "kinby.toml").open("a") as manifest:
        manifest.write(f"[routines]\n{setting}\n")
    with pytest.raises(ManifestError, match="routines"):
        load_instance(tmp_path)


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class ScriptedRunner:
    restore = cannot_restore
    resume = does_not_park

    async def run(self, turn, context):
        await context(MessageDelta(text="News today\nMore details"))
        return TurnOutcome()


def runtime(instance, clock, runner=None):
    return build_dispatcher(
        instance.manifest.state_dir,
        turns=ScheduledTurnConfig(
            TurnConfig(
                fixed_turn_preparation, fixed_permission_ceiling, runner or ScriptedRunner()
            ),
            SchedulerConfig(instance, clock),
        ),
    )


async def call(dispatcher, method, **payload):
    return await dispatcher.dispatch(method, payload, set(Scope))


async def start_payload_call(
    dispatcher: Dispatcher, name: str, payload: str
) -> asyncio.Task[ContractModel]:
    pending_call = asyncio.create_task(
        call(
            dispatcher,
            "routine.run",
            name=name,
            payload={"body": payload, "content_type": "text/plain"},
        )
    )
    await asyncio.sleep(0)
    return pending_call


async def cancel_call(pending_call: asyncio.Task[ContractModel]) -> None:
    pending_call.cancel()
    with suppress(asyncio.CancelledError):
        await pending_call


async def events_for(dispatcher, thread_id):
    events = []
    stream = dispatcher.subscribe("thread.subscribe", {"thread_id": thread_id}, set(Scope))
    async with asyncio.timeout(3):
        async for event in stream:
            assert isinstance(event, Event)
            events.append(event)
            if is_turn_closing(event.payload):
                break
    await stream.aclose()
    return events


def test_scheduled_fire_through_dispatcher(tmp_path: Path) -> None:
    async def scenario():
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News\nschedule: 0 9 * * *")
        clock = FakeClock(datetime(2026, 9, 6, 8, 59, tzinfo=UTC))
        dispatcher = runtime(instance, clock)
        assert dispatcher.scheduler is not None
        await dispatcher.scheduler.tick()
        listed = await call(dispatcher, "routine.list")
        assert not isinstance(listed, ErrorEnvelope)
        assert listed.routines[0].next_run == datetime(2026, 9, 6, 9, tzinfo=UTC)
        clock.now = datetime(2026, 9, 6, 9, 1, tzinfo=UTC)
        await dispatcher.scheduler.tick()
        await dispatcher.scheduler.drain()
        threads = await call(dispatcher, "thread.list")
        assert isinstance(threads, ThreadListResult)
        assert len(threads.threads) == 1
        assert threads.threads[0].title == "news · 2026-09-06T09:01:00+00:00"
        events = await events_for(dispatcher, threads.threads[0].id)
        assert isinstance(events[0].payload, TurnStarted)
        assert events[0].payload.message == "Read the news."
        assert isinstance(events[0].payload.origin, RoutineOrigin)
        assert events[0].payload.origin.trigger == "scheduled"
        assert isinstance(events[-1].payload, TurnCompleted)
        listed = await call(dispatcher, "routine.list")
        assert listed.routines[0].last_run.first_line == "News today"
        assert listed.routines[0].next_run == datetime(2026, 9, 7, 9, tzinfo=UTC)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "catch_up,history,expected", [(True, True, 1), (False, True, 0), (True, False, 0)]
)
def test_start_catches_up_once(tmp_path, catch_up, history, expected):
    async def scenario():
        instance = instance_at(tmp_path)
        routine_file(
            instance, f"description: News\nschedule: 0 9 * * *\ncatch_up: {str(catch_up).lower()}"
        )
        clock = FakeClock(datetime(2026, 9, 3, 9, tzinfo=UTC))
        dispatcher = runtime(instance, clock)
        if history:
            accepted = await call(dispatcher, "routine.run", name="news")
            await events_for(dispatcher, accepted.thread_id)
            path = instance.manifest.state_dir / "events.jsonl"
            events = [Event.model_validate_json(line) for line in path.read_text().splitlines()]
            path.write_text(
                "".join(
                    e.model_copy(update={"timestamp": clock.now}).model_dump_json() + "\n"
                    for e in events
                )
            )
        clock.now = datetime(2026, 9, 6, 10, tzinfo=UTC)
        dispatcher = runtime(instance, clock)
        await dispatcher.scheduler.tick()
        await dispatcher.scheduler.tick()
        threads = await call(dispatcher, "thread.list")
        assert len(threads.threads) == int(history) + expected
        if expected:
            listed = await call(dispatcher, "routine.list")
            events = await events_for(dispatcher, listed.routines[0].last_run.thread_id)
            assert events[0].payload.origin.trigger == "catch-up"

    asyncio.run(scenario())


def test_interrupted_scheduled_run_does_not_catch_up_after_restart(tmp_path):
    async def scenario():
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News\nschedule: * * * * *")
        clock = FakeClock(datetime(2026, 9, 6, 9, tzinfo=UTC))
        runner = BlockingRunner()
        dispatcher = runtime(instance, clock, runner)

        clock.now = datetime(2026, 9, 6, 9, 1, tzinfo=UTC)
        ticking = asyncio.create_task(dispatcher.scheduler.tick())
        while not (await call(dispatcher, "thread.list")).threads:
            await asyncio.sleep(0)
        await dispatcher.scheduler.interrupt()
        await ticking

        path = instance.manifest.state_dir / "events.jsonl"
        events = [Event.model_validate_json(line) for line in path.read_text().splitlines()]
        path.write_text(
            "".join(
                event.model_copy(update={"timestamp": clock.now}).model_dump_json() + "\n"
                for event in events
            )
        )
        clock.now = datetime(2026, 9, 6, 9, 3, tzinfo=UTC)
        restarted = runtime(instance, clock)
        await restarted.scheduler.tick()

        threads = await call(restarted, "thread.list")
        assert len(threads.threads) == 1
        listed = await call(restarted, "routine.list")
        assert listed.routines[0].last_run.outcome == "interrupted"
        assert listed.routines[0].next_run == datetime(2026, 9, 6, 9, 4, tzinfo=UTC)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "start,expected",
    [
        ("2026-03-28T03:01:00+00:00", "2026-03-29T01:00:00+00:00"),
        ("2026-10-24T03:01:00+00:00", "2026-10-25T02:00:00+00:00"),
    ],
)
def test_madrid_schedule_across_dst(tmp_path, start, expected):
    async def scenario():
        instance = instance_at(tmp_path)
        with (tmp_path / "kinby.toml").open("a") as f:
            f.write('[routines]\ntimezone = "Europe/Madrid"\n')
        instance = load_instance(tmp_path)
        routine_file(instance, "description: News\nschedule: 0 3 * * *")
        clock = FakeClock(datetime.fromisoformat(start))
        dispatcher = runtime(instance, clock)
        listed = await call(dispatcher, "routine.list")
        assert listed.routines[0].next_run == datetime.fromisoformat(expected)
        clock.now = datetime.fromisoformat(expected)
        await dispatcher.scheduler.tick()
        assert len((await call(dispatcher, "thread.list")).threads) == 1

    asyncio.run(scenario())


def test_reload_and_invalid_schedules(tmp_path):
    async def scenario():
        instance = instance_at(tmp_path)
        path = routine_file(instance, "description: News\nschedule: 0 9 * * *")
        clock = FakeClock(datetime(2026, 9, 6, 8, tzinfo=UTC))
        dispatcher = runtime(instance, clock)
        await dispatcher.scheduler.tick()
        path.write_text(path.read_text().replace("0 9", "0 10"))
        clock.now = datetime(2026, 9, 6, 9, tzinfo=UTC)
        await dispatcher.scheduler.tick()
        assert len((await call(dispatcher, "thread.list")).threads) == 0
        assert (await call(dispatcher, "routine.list")).routines[0].next_run.hour == 10
        path.write_text(
            path.read_text().replace("description: News", "description: News\nenabled: false")
        )
        clock.now = datetime(2026, 9, 6, 10, tzinfo=UTC)
        await dispatcher.scheduler.tick()
        assert len((await call(dispatcher, "thread.list")).threads) == 0
        path.write_text(path.read_text().replace("0 10 * * *", "invalid"))
        listed = await call(dispatcher, "routine.list")
        assert listed.routines == []
        assert len(listed.warnings) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("frontmatter", ["enabled: false\nschedule: 0 9 * * *", "enabled: true"])
def test_manual_and_scopes(tmp_path, frontmatter):
    async def scenario():
        instance = instance_at(tmp_path)
        routine_file(instance, f"description: News\n{frontmatter}")
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 6, tzinfo=UTC)))
        denied = await dispatcher.dispatch("routine.run", {"name": "news"}, {Scope.INSTANCE_READ})
        assert denied.code == "PERMISSION_DENIED"
        missing = await call(dispatcher, "routine.run", name="missing")
        assert missing.code == "NOT_FOUND"
        accepted = await call(dispatcher, "routine.run", name="news")
        assert isinstance(accepted, AcceptedResult)
        assert (await events_for(dispatcher, accepted.thread_id))[
            0
        ].payload.origin.trigger == "manual"
        bare = build_dispatcher(instance.manifest.state_dir)
        for method in ["routine.list", "routine.run"]:
            assert (await call(bare, method)).code == "NOT_FOUND"

    asyncio.run(scenario())


class BlockingRunner(ScriptedRunner):
    def __init__(self):
        self.release = asyncio.Event()

    async def run(self, turn, context):
        await self.release.wait()
        return await super().run(turn, context)


def test_delivery_received_while_routine_runs_waits_then_fires(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        runner = BlockingRunner()
        dispatcher = runtime(
            instance,
            FakeClock(datetime(2026, 9, 6, 9, tzinfo=UTC)),
            runner,
        )
        assert dispatcher.scheduler is not None
        running = await call(dispatcher, "routine.run", name="news")
        assert isinstance(running, AcceptedResult)
        receiving = await start_payload_call(dispatcher, "news", "later")
        received_event = next(
            event
            for event in EventLog(instance.manifest.state_dir).all_events()
            if isinstance(event.payload, SignalReceived)
        )
        assert not receiving.done()
        assert not any(
            isinstance(event.payload, TurnStarted)
            for event in EventLog(instance.manifest.state_dir).stored(received_event.thread_id)
        )

        runner.release.set()
        received = await receiving
        assert isinstance(received, AcceptedResult)

        assert any(
            isinstance(event.payload, TurnStarted)
            for event in EventLog(instance.manifest.state_dir).stored(received.thread_id)
        )

    asyncio.run(scenario())


def test_instance_busy_serializes_routines_but_not_users(tmp_path):
    async def scenario():
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News\nschedule: * * * * *")
        runner = BlockingRunner()
        clock = FakeClock(datetime(2026, 9, 6, 9, tzinfo=UTC))
        dispatcher = runtime(instance, clock, runner)
        await dispatcher.scheduler.tick()
        one = await call(dispatcher, "thread.create")
        two = await call(dispatcher, "thread.create")
        assert isinstance(
            await call(dispatcher, "thread.turn.start", thread_id=one.id, message="Hi"),
            AcceptedResult,
        )
        assert isinstance(
            await call(dispatcher, "thread.turn.start", thread_id=two.id, message="Hi"),
            AcceptedResult,
        )
        refused = await call(dispatcher, "routine.run", name="news")
        assert refused.code == "INSTANCE_BUSY"
        assert refused.retryable and "news" in refused.message
        clock.now = datetime(2026, 9, 6, 9, 1, tzinfo=UTC)
        ticking = asyncio.create_task(dispatcher.scheduler.tick())
        await asyncio.sleep(0)
        assert not ticking.done()
        runner.release.set()
        await ticking
        await dispatcher.scheduler.drain()
        runner.release.clear()
        accepted = await call(dispatcher, "routine.run", name="news")
        assert isinstance(accepted, AcceptedResult)
        busy = await call(dispatcher, "thread.turn.start", thread_id=one.id, message="Hi")
        assert busy.code == "INSTANCE_BUSY" and busy.retryable
        assert "news" in busy.message
        runner.release.set()
        await dispatcher.scheduler.drain()

    asyncio.run(scenario())


class FailingRunner(ScriptedRunner):
    def __init__(self):
        self.result: Exception | TurnOutcome = CodeStepFailed("feed unavailable")

    async def run(self, turn, context):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_failure_notices_disable_and_restart(tmp_path):
    async def scenario():
        instance = instance_at(tmp_path)
        path = routine_file(
            instance,
            "description: News\nschedule: * * * * *\nenabled: true",
            "Keep this body.\nenabled: true",
        )
        original = path.read_text()
        runner = FailingRunner()
        clock = FakeClock(datetime(2026, 9, 6, 9, tzinfo=UTC))
        dispatcher = runtime(instance, clock, runner)
        for count in range(1, 11):
            accepted = await call(dispatcher, "routine.run", name="news")
            await events_for(dispatcher, accepted.thread_id)
            await dispatcher.scheduler.drain()
            listed = (await call(dispatcher, "routine.list")).routines[0]
            assert listed.failure_count == count
            assert len(listed.notices) == (1 if count < 10 else 2)
            assert "feed unavailable" in listed.notices[-1].message
        assert not listed.enabled
        assert listed.notices[-1].kind == "disabled"
        assert path.read_text() == original.replace("enabled: true", "enabled: false", 1)
        clock.now = datetime(2026, 9, 6, 10, tzinfo=UTC)
        await dispatcher.scheduler.tick()
        assert len((await call(dispatcher, "thread.list")).threads) == 10
        dispatcher = runtime(instance, clock, runner)
        await dispatcher.scheduler.tick()
        assert (await call(dispatcher, "routine.list")).routines[0].failure_count == 10
        assert len((await call(dispatcher, "routine.list")).routines[0].notices) == 2
        runner.result = TurnOutcome()
        await call(dispatcher, "routine.run", name="news")
        await dispatcher.scheduler.drain()
        listed = (await call(dispatcher, "routine.list")).routines[0]
        assert listed.failure_count == 0 and not listed.enabled
        path.write_text(original)
        dispatcher = runtime(instance, clock, runner)
        await dispatcher.scheduler.tick()
        assert path.read_text() == original
        assert (await call(dispatcher, "routine.list")).routines[0].enabled

    asyncio.run(scenario())


def test_failed_signal_firings_disable_routine_at_ten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("SIGNAL_SECRET", "secret")
        instance = instance_at(tmp_path)
        routine_file(
            instance,
            "description: Issues\nenabled: true\nsignal:\n  secret: SIGNAL_SECRET",
        )
        clock = FakeClock(datetime(2026, 9, 6, 9, tzinfo=UTC))
        dispatcher = runtime(instance, clock, FailingRunner())
        assert dispatcher.scheduler is not None
        for count in range(1, 11):
            await call(
                dispatcher,
                "routine.run",
                name="news",
                payload={"body": str(count), "content_type": "text/plain"},
            )
            listed = await call(dispatcher, "routine.list")
            assert not isinstance(listed, ErrorEnvelope)
            assert listed.routines[0].failure_count == count

        assert not listed.routines[0].enabled
        assert listed.routines[0].notices[-1].kind == "disabled"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure",
    [CodeStepFailed("code broke"), RuntimeError("model broke"), BudgetExceeded("tokens", 5)],
)
def test_failure_count_ignores_no_work_and_resets_on_work(tmp_path, failure):
    async def scenario():
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        runner = FailingRunner()
        runner.result = failure
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 6, tzinfo=UTC)), runner)

        async def fire():
            accepted = await call(dispatcher, "routine.run", name="news")
            await events_for(dispatcher, accepted.thread_id)
            await dispatcher.scheduler.drain()
            return (await call(dispatcher, "routine.list")).routines[0]

        assert (await fire()).failure_count == 1
        runner.result = TurnOutcome(outcome=CompletionOutcome.NO_WORK)
        assert (await fire()).failure_count == 1
        runner.result = failure
        assert (await fire()).failure_count == 2
        runner.result = TurnOutcome()
        assert (await fire()).failure_count == 0
        runner.result = failure
        listed = await fire()
        assert listed.failure_count == 1
        assert len(listed.notices) == 2

    asyncio.run(scenario())


def test_worker_fires_and_drains(tmp_path):
    async def scenario():
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News\nschedule: * * * * *")
        clock = FakeClock(datetime(2026, 9, 6, 9, tzinfo=UTC))
        dispatcher = runtime(instance, clock)
        dispatcher.scheduler.start()
        await asyncio.sleep(0)
        clock.now = datetime(2026, 9, 6, 9, 1, tzinfo=UTC)
        dispatcher.scheduler.schedule()
        async with asyncio.timeout(3):
            while not (await call(dispatcher, "thread.list")).threads:
                await asyncio.sleep(0)
        await dispatcher.scheduler.stop()
        listed = await call(dispatcher, "routine.list")
        assert listed.routines[0].last_run.outcome == "work"

    asyncio.run(scenario())


def test_cli_routine_list_shows_signal_path_auth_and_pending(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    instance = instance_at(tmp_path)
    routine_file(
        instance,
        """description: GitHub issues
signal:
  secret: GITHUB_WEBHOOK_SECRET""",
    )

    async def record_deliveries() -> None:
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 6, 9, tzinfo=UTC)))
        assert dispatcher.scheduler is not None
        for body in ("opened", "labeled"):
            await dispatcher.scheduler.receive(
                RoutineName("news"),
                Delivery(
                    headers={},
                    content_type="text/plain",
                    body=body,
                    received_at=datetime(2026, 9, 6, 9, tzinfo=UTC),
                ),
                RoutineTrigger.SIGNAL,
            )

    asyncio.run(record_deliveries())
    assert main(["routine", "list", "--instance", str(tmp_path)]) == 0
    assert capsys.readouterr().out == (
        "name\tschedule\tenabled\tlast run\tnext run\tsignal\tauth\tpending\n"
        "news\tnone\tenabled\tnone\tnone\t/signals/news\ttoken\t2\n"
    )


def test_receive_records_delivery_and_drops_repeated_id(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        clock = FakeClock(datetime(2026, 9, 6, 9, tzinfo=UTC))
        dispatcher = runtime(instance, clock)
        assert dispatcher.scheduler is not None
        delivery = Delivery(
            headers={"content-type": "application/json"},
            content_type="application/json",
            body='{"action":"opened"}',
            delivery_id=DeliveryId("delivery-1"),
            received_at=clock.now,
        )

        accepted = await dispatcher.scheduler.receive(
            RoutineName("issues"), delivery, RoutineTrigger.SIGNAL
        )
        repeated = await dispatcher.scheduler.receive(
            RoutineName("issues"), delivery, RoutineTrigger.SIGNAL
        )

        assert repeated == accepted
        threads = await call(dispatcher, "thread.list")
        assert isinstance(threads, ThreadListResult)
        assert [(thread.id, thread.title) for thread in threads.threads] == [
            (accepted.thread_id, "issues · delivery-1")
        ]
        events = EventLog(instance.manifest.state_dir).stored(accepted.thread_id)
        assert len(events) == 1
        assert events[0].turn_id == accepted.turn_id
        assert events[0].sequence == accepted.sequence
        assert events[0].payload == SignalReceived(
            origin=RoutineOrigin(
                name=RoutineName("issues"),
                trigger=RoutineTrigger.SIGNAL,
                delivery_id=DeliveryId("delivery-1"),
            ),
            delivery=delivery,
        )

    asyncio.run(scenario())


def test_routine_list_counts_pending_deliveries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("SIGNAL_SECRET", "secret")
        instance = instance_at(tmp_path)
        routine_file(
            instance,
            "description: Issues\nsignal:\n  secret: SIGNAL_SECRET",
            name="issues",
        )
        routine_file(instance, "description: Digest", name="digest")
        clock = FakeClock(datetime(2026, 9, 6, 9, tzinfo=UTC))
        dispatcher = runtime(instance, clock)
        assert dispatcher.scheduler is not None
        for body in ("opened", "labeled"):
            await dispatcher.scheduler.receive(
                RoutineName("issues"),
                Delivery(
                    headers={},
                    content_type="text/plain",
                    body=body,
                    received_at=clock.now,
                ),
                RoutineTrigger.SIGNAL,
            )

        listed = await call(dispatcher, "routine.list")
        assert not isinstance(listed, ErrorEnvelope)
        assert {routine.name: routine.pending for routine in listed.routines} == {
            "digest": 0,
            "issues": 2,
        }

    asyncio.run(scenario())


def test_first_scheduler_pass_fires_pending_delivery_with_fixed_turn_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("SIGNAL_SECRET", "secret")
        instance = instance_at(tmp_path)
        routine_file(
            instance,
            "description: Issues\nsignal:\n  secret: SIGNAL_SECRET",
            name="issues",
        )
        clock = FakeClock(datetime(2026, 9, 6, 9, tzinfo=UTC))
        runner = BlockingRunner()
        dispatcher = runtime(instance, clock, runner)
        assert dispatcher.scheduler is not None
        await call(dispatcher, "routine.run", name="issues")
        receiving = await start_payload_call(dispatcher, "issues", "opened")
        received = next(
            event
            for event in EventLog(instance.manifest.state_dir).all_events()
            if isinstance(event.payload, SignalReceived)
        )
        await cancel_call(receiving)
        runner.release.set()
        await dispatcher.scheduler.drain()

        restarted = runtime(instance, clock)
        assert restarted.scheduler is not None
        await restarted.scheduler.tick()
        await restarted.scheduler.drain()

        events = EventLog(instance.manifest.state_dir).stored(received.thread_id)
        assert [type(event.payload) for event in events] == [
            SignalReceived,
            TurnStarted,
            MessageDelta,
            TurnCompleted,
        ]
        assert {event.turn_id for event in events} == {received.turn_id}
        started = events[1].payload
        assert isinstance(started, TurnStarted)
        assert started.origin == RoutineOrigin(
            name=RoutineName("issues"), trigger=RoutineTrigger.SIGNAL
        )
        listed = await call(restarted, "routine.list")
        assert not isinstance(listed, ErrorEnvelope)
        assert listed.routines[0].pending == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "delivery_time,expected",
    [
        (
            datetime(2026, 9, 6, 9, 1, tzinfo=UTC),
            [RoutineTrigger.SCHEDULED, RoutineTrigger.SIGNAL],
        ),
        (
            datetime(2026, 9, 6, 8, 58, tzinfo=UTC),
            [RoutineTrigger.SIGNAL, RoutineTrigger.SCHEDULED],
        ),
    ],
)
def test_scheduler_fires_oldest_ready_work_one_per_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    delivery_time: datetime,
    expected: list[RoutineTrigger],
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("SIGNAL_SECRET", "secret")
        instance = instance_at(tmp_path)
        routine_file(
            instance,
            "description: Schedule\nschedule: 0 9 * * *",
            name="scheduled",
        )
        routine_file(
            instance,
            "description: Delivery\nsignal:\n  secret: SIGNAL_SECRET",
            name="issues",
        )
        clock = FakeClock(datetime(2026, 9, 6, 8, 59, tzinfo=UTC))
        runner = BlockingRunner()
        dispatcher = runtime(instance, clock, runner)
        assert dispatcher.scheduler is not None
        await call(dispatcher, "routine.run", name="issues")
        clock.now = delivery_time
        receiving = await start_payload_call(dispatcher, "issues", "opened")
        await cancel_call(receiving)
        runner.release.set()
        await dispatcher.scheduler.drain()
        clock.now = datetime(2026, 9, 6, 9, 2, tzinfo=UTC)

        observed = []
        for _ in range(2):
            await dispatcher.scheduler.tick()
            started = [
                event.payload
                for event in EventLog(instance.manifest.state_dir).all_events()
                if isinstance(event.payload, TurnStarted)
                and isinstance(event.payload.origin, RoutineOrigin)
                and event.payload.origin.trigger is not RoutineTrigger.MANUAL
            ]
            assert len(started) == len(observed) + 1
            origin = started[-1].origin
            assert isinstance(origin, RoutineOrigin)
            observed.append(origin.trigger)

        assert observed == expected

    asyncio.run(scenario())


def test_scheduler_fires_every_pending_delivery_in_receipt_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        routine_file(instance, "description: Issues", name="issues")
        runner = BlockingRunner()
        clock = FakeClock(datetime(2026, 9, 6, 9, tzinfo=UTC))
        dispatcher = runtime(
            instance,
            clock,
            runner,
        )
        assert dispatcher.scheduler is not None
        running = await call(dispatcher, "routine.run", name="news")
        assert isinstance(running, AcceptedResult)
        receiving = []
        for name, body, minute in (
            ("news", "first", 2),
            ("issues", "second", 1),
            ("news", "third", 0),
        ):
            clock.now = datetime(2026, 9, 6, 9, minute, tzinfo=UTC)
            receiving.append(await start_payload_call(dispatcher, name, body))

        received = [
            event
            for event in EventLog(instance.manifest.state_dir).all_events()
            if isinstance(event.payload, SignalReceived)
        ]
        assert len(received) == 3
        for task in receiving:
            await cancel_call(task)

        runner.release.set()
        await dispatcher.scheduler.drain()

        for _ in received:
            await dispatcher.scheduler.tick()

        assert [
            event.thread_id
            for event in EventLog(instance.manifest.state_dir).all_events()
            if isinstance(event.payload, TurnStarted)
            and isinstance(event.payload.origin, RoutineOrigin)
            and event.payload.origin.trigger is RoutineTrigger.SIGNAL
        ] == [item.thread_id for item in received]

    asyncio.run(scenario())


def test_cli_routine_list_and_no_work_run(tmp_path, capsys):
    instance = instance_at(tmp_path)
    path = routine_file(instance, "description: News\nenabled: false")
    (path.parent / "run.py").write_text(
        "from kinby.plugins import tool\n@tool(write=False)\n"
        'def fetch() -> None:\n    """Fetch news."""\n    return None\n'
    )
    assert main(["routine", "list", "--instance", str(tmp_path)]) == 0
    assert "news" in capsys.readouterr().out
    assert main(["routine", "run", "news", "--instance", str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["routine", "list", "--instance", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "disabled" in output and "no-work" in output


def test_cli_parked_routine_reports_non_success(tmp_path, capsys, monkeypatch):
    from importlib import import_module

    from tests.test_repl import ApprovalReplRunner

    instance_path = tmp_path / "instance with spaces"
    instance_path.mkdir()
    instance = instance_at(instance_path)
    routine_file(instance, "description: News\nmode: ask")
    monkeypatch.setattr(
        import_module("kinby.core.runtime"),
        "turn_config",
        lambda *args, **kwargs: TurnConfig(
            fixed_turn_preparation, fixed_permission_ceiling, ApprovalReplRunner()
        ),
    )
    assert main(["routine", "run", "news", "--instance", str(instance_path)]) == 1
    output = capsys.readouterr()
    assert "thread:" in output.out
    thread_id = output.out.splitlines()[0].removeprefix("thread: ")
    assert (
        f"Resume with: kinby run --thread {thread_id} --instance '{instance_path}'\n"
    ) in output.out
    assert "parked" in output.err and "approval" in output.err
    events = EventLog(instance.manifest.state_dir).all_events()
    assert not any(isinstance(event.payload, TurnInterrupted) for event in events)


@pytest.mark.parametrize("thread_count", [1, 8])
def test_availability_reads_history_once(tmp_path, monkeypatch, thread_count):
    from kinby.core.events import EventLog
    from tests.test_repl import ApprovalReplRunner

    async def scenario():
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        clock = FakeClock(datetime(2026, 9, 6, tzinfo=UTC))
        dispatcher = runtime(instance, clock, ApprovalReplRunner())
        await call(dispatcher, "routine.run", name="news")
        await dispatcher.scheduler.drain()
        user = await call(dispatcher, "thread.create")
        for _ in range(thread_count - 1):
            await call(dispatcher, "thread.create")
        restarted = runtime(instance, clock)
        reads = 0
        all_events = EventLog.all_events

        def counted_events(log):
            nonlocal reads
            reads += 1
            return all_events(log)

        monkeypatch.setattr(EventLog, "all_events", counted_events)
        busy = await call(restarted, "thread.turn.start", thread_id=user.id, message="Hello")
        assert busy.code == "INSTANCE_BUSY"
        assert reads == 1

    asyncio.run(scenario())


def test_repl_waits_visibly_and_shows_routines(tmp_path):
    async def scenario():
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        runner = BlockingRunner()
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 6, tzinfo=UTC)), runner)
        await call(dispatcher, "routine.run", name="news")
        thread = await call(dispatcher, "thread.create")
        stdout, stderr = StringIO(), StringIO()
        repl = asyncio.create_task(
            run_repl(
                ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope)),
                thread.id,
                feedback=FeedbackPolicy.OFF,
                stdin=StringIO("Hello\n/routines\n"),
                stdout=stdout,
                stderr=stderr,
            )
        )
        async with asyncio.timeout(3):
            while "INSTANCE_BUSY" not in stderr.getvalue():
                await asyncio.sleep(0)
        runner.release.set()
        assert await repl == 0
        assert stderr.getvalue().count("INSTANCE_BUSY") == 1
        starts = [
            event.payload
            for event in EventLog(instance.manifest.state_dir).stored(thread.id)
            if isinstance(event.payload, TurnStarted)
        ]
        assert [started.message for started in starts] == ["Hello"]
        assert "News today" in stdout.getvalue()
        assert "news" in stdout.getvalue()

    asyncio.run(scenario())


def test_repl_shows_pending_deliveries_only_for_signal_routines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("SIGNAL_SECRET", "secret")
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News\nsignal:\n  secret: SIGNAL_SECRET")
        untouched = instance.path / "routines" / "untouched" / "ROUTINE.md"
        untouched.parent.mkdir(parents=True)
        untouched.write_text("---\ndescription: Untouched\n---\nWait.\n")
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 6, tzinfo=UTC)))
        accepted = await call(dispatcher, "routine.run", name="news")
        await events_for(dispatcher, accepted.thread_id)
        event_log = EventLog(instance.manifest.state_dir)
        events = list(event_log.all_events())
        (instance.manifest.state_dir / "events.jsonl").write_text(
            "".join(
                event.model_copy(
                    update={"timestamp": datetime(2026, 9, 6, 9, tzinfo=UTC)}
                ).model_dump_json()
                + "\n"
                for event in events
            ),
            encoding="utf-8",
        )
        assert dispatcher.scheduler is not None
        for body in ("opened", "labeled"):
            await dispatcher.scheduler.receive(
                RoutineName("news"),
                Delivery(
                    headers={},
                    content_type="text/plain",
                    body=body,
                    received_at=datetime(2026, 9, 6, 9, tzinfo=UTC),
                ),
                RoutineTrigger.SIGNAL,
            )
        thread = await call(dispatcher, "thread.create")
        stdout = StringIO()

        exit_code = await run_repl(
            ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope)),
            thread.id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO(),
            stdout=stdout,
            stderr=StringIO(),
        )

        assert exit_code == 0
        assert stdout.getvalue() == (
            'Routine "news": 2026-09-06T09:00:00+00:00, work, News today, 2 pending\n'
            'Routine "untouched": never ran.\n'
            "> "
        )

    asyncio.run(scenario())


def test_repl_startup_names_the_thread_for_a_parked_routine(tmp_path: Path) -> None:
    from tests.test_repl import ApprovalReplRunner

    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        runner = ApprovalReplRunner()
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 6, tzinfo=UTC)), runner)
        accepted = await call(dispatcher, "routine.run", name="news")
        await asyncio.wait_for(runner.parked.wait(), timeout=1)
        await dispatcher.scheduler.drain()
        thread = await call(dispatcher, "thread.create")
        stdout = StringIO()

        exit_code = await run_repl(
            ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope)),
            thread.id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO(),
            stdout=stdout,
            stderr=StringIO(),
        )

        assert exit_code == 0
        assert f", parked on thread {accepted.thread_id}\n" in stdout.getvalue()

    asyncio.run(scenario())


def test_repl_answers_a_parked_routine_approval_before_input(tmp_path: Path) -> None:
    from kinby.core.turns import ApprovalDecision
    from tests.test_repl import ApprovalReplRunner

    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        runner = ApprovalReplRunner()
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 6, tzinfo=UTC)), runner)
        accepted = await call(dispatcher, "routine.run", name="news")
        await asyncio.wait_for(runner.parked.wait(), timeout=1)
        await dispatcher.scheduler.drain()
        listed = await call(dispatcher, "routine.list")
        started_at = listed.routines[0].last_run.started_at.isoformat()
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await asyncio.wait_for(
            run_repl(
                ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope)),
                accepted.thread_id,
                feedback=FeedbackPolicy.OFF,
                stdin=StringIO("yes\n"),
                stdout=stdout,
                stderr=stderr,
            ),
            timeout=1,
        )

        assert exit_code == 0
        assert runner.decisions == [ApprovalDecision.APPROVE]
        assert stdout.getvalue() == (
            f'Routine "news": {started_at}, parked on thread {accepted.thread_id}\n'
            'Approve write_note {"note": "remember me"} under rule "mode.ask.write"? '
            "[yes/no] "
            '[tool.call] write_note {"note": "remember me"}\n'
            "[tool.result] write_note (ok): remember me\nDone\n> "
        )
        assert stderr.getvalue() == ""

    asyncio.run(scenario())


def test_repl_startup_shows_a_routines_first_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        dispatcher = runtime(
            instance,
            FakeClock(datetime(2026, 9, 6, tzinfo=UTC)),
            FailingRunner(),
        )
        await call(dispatcher, "routine.run", name="news")
        await dispatcher.scheduler.drain()
        thread = await call(dispatcher, "thread.create")
        stdout = StringIO()

        await run_repl(
            ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope)),
            thread.id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO(),
            stdout=stdout,
            stderr=StringIO(),
        )

        assert ", failed\n  Last failure: feed unavailable\n" in stdout.getvalue()
        assert '  Routine "news" failed: feed unavailable\n' in stdout.getvalue()

    asyncio.run(scenario())


def test_repl_startup_explains_an_automatically_disabled_routine(tmp_path: Path) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News\nenabled: true")
        runner = FailingRunner()
        dispatcher = runtime(
            instance,
            FakeClock(datetime(2026, 9, 6, tzinfo=UTC)),
            runner,
        )
        for _ in range(10):
            await call(dispatcher, "routine.run", name="news")
            await dispatcher.scheduler.drain()
        runner.result = TurnOutcome()
        await call(dispatcher, "routine.run", name="news")
        await dispatcher.scheduler.drain()
        listed = await call(dispatcher, "routine.list")
        assert not listed.routines[0].enabled
        assert listed.routines[0].last_run.outcome == "work"
        thread = await call(dispatcher, "thread.create")
        stdout = StringIO()

        await run_repl(
            ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope)),
            thread.id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO(),
            stdout=stdout,
            stderr=StringIO(),
        )

        assert ", work\n" in stdout.getvalue()
        assert 'Routine "news" disabled after 10 failed firings: feed unavailable' in (
            stdout.getvalue()
        )
        assert (
            "Re-enable it by setting enabled = true in routines/news/ROUTINE.md."
            in stdout.getvalue()
        )

    asyncio.run(scenario())


def test_parked_routine_reserves_instance_and_interruption_is_neutral(tmp_path):
    from tests.test_repl import ApprovalReplRunner

    async def scenario():
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        clock = FakeClock(datetime(2026, 9, 6, tzinfo=UTC))
        dispatcher = runtime(instance, clock, FailingRunner())
        await call(dispatcher, "routine.run", name="news")
        await dispatcher.scheduler.drain()
        runner = ApprovalReplRunner()
        dispatcher = runtime(instance, clock, runner)
        accepted = await call(dispatcher, "routine.run", name="news")
        await runner.parked.wait()
        await dispatcher.scheduler.drain()
        routine = (await call(dispatcher, "routine.list")).routines[0]
        assert routine.last_run.outcome == "parked" and routine.failure_count == 1
        restarted = runtime(instance, clock, runner)
        await restarted.scheduler.interrupt()
        routine = (await call(restarted, "routine.list")).routines[0]
        assert routine.last_run.outcome == "parked"
        user = await call(restarted, "thread.create")
        busy = await call(restarted, "thread.turn.start", thread_id=user.id, message="Hello")
        assert busy.code == "INSTANCE_BUSY"
        await call(restarted, "thread.turn.interrupt", thread_id=accepted.thread_id)
        await restarted.scheduler.drain()
        routine = (await call(restarted, "routine.list")).routines[0]
        assert routine.last_run.outcome == "interrupted" and routine.failure_count == 1
        assert len(routine.notices) == 1

    asyncio.run(scenario())


def test_daily_refusal_leaves_failure_count_unchanged(tmp_path):
    from kinby.core.budgets import DailyCost
    from kinby.instance import Budgets

    async def scenario():
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News\nschedule: * * * * *")
        clock = FakeClock(datetime(2026, 9, 6, 9, tzinfo=UTC))
        dispatcher = runtime(instance, clock, FailingRunner())
        await call(dispatcher, "routine.run", name="news")
        await dispatcher.scheduler.drain()
        dispatcher = build_dispatcher(
            instance.manifest.state_dir,
            turns=ScheduledTurnConfig(
                TurnConfig(
                    lambda: fixed_turn_preparation(
                        budgets=Budgets(usd_per_day=1), daily_cost=DailyCost(usd=1)
                    ),
                    fixed_permission_ceiling,
                    ScriptedRunner(),
                ),
                SchedulerConfig(instance, clock),
            ),
        )
        assert dispatcher.scheduler is not None
        refused = await call(dispatcher, "routine.run", name="news")
        assert refused.code == "BUDGET_EXCEEDED"
        assert len((await call(dispatcher, "thread.list")).threads) == 1
        accepted = await call(
            dispatcher,
            "routine.run",
            name="news",
            payload={"body": "pending", "content_type": "text/plain"},
        )
        assert isinstance(accepted, AcceptedResult)
        listed = await call(dispatcher, "routine.list")
        assert listed.routines[0].pending == 1
        await dispatcher.scheduler.tick()
        clock.now = datetime(2026, 9, 6, 9, 1, tzinfo=UTC)
        await dispatcher.scheduler.tick()
        assert len((await call(dispatcher, "thread.list")).threads) == 2
        await dispatcher.scheduler.drain()
        routine = (await call(dispatcher, "routine.list")).routines[0]
        assert routine.failure_count == 1 and len(routine.notices) == 1

    asyncio.run(scenario())


def test_many_frequent_routines_have_no_artificial_limit(tmp_path):
    async def scenario():
        instance = instance_at(tmp_path)
        for i in range(21):
            path = tmp_path / "routines" / f"routine-{i}" / "ROUTINE.md"
            path.parent.mkdir(parents=True)
            path.write_text("---\ndescription: Check\nschedule: * * * * *\n---\nCheck.")
        clock = FakeClock(datetime(2026, 9, 6, 9, tzinfo=UTC))
        dispatcher = runtime(instance, clock)
        listed = await call(dispatcher, "routine.list")
        assert len(listed.routines) == 21 and not listed.warnings
        clock.now = datetime(2026, 9, 6, 9, 1, tzinfo=UTC)
        for _ in range(21):
            await dispatcher.scheduler.tick()
        assert len((await call(dispatcher, "thread.list")).threads) == 21

    asyncio.run(scenario())


def test_reenable_with_failed_history_is_not_undone(tmp_path):
    async def scenario():
        instance = instance_at(tmp_path)
        path = routine_file(
            instance, "description: News\nschedule: * * * * *\nenabled: true\ncatch_up: false"
        )
        original = path.read_text()
        clock = FakeClock(datetime(2026, 9, 6, 9, tzinfo=UTC))
        dispatcher = runtime(instance, clock, FailingRunner())
        for _ in range(10):
            await call(dispatcher, "routine.run", name="news")
            await dispatcher.scheduler.drain()
        path.write_text(original)
        dispatcher = runtime(instance, clock)
        await dispatcher.scheduler.tick()
        await dispatcher.scheduler.tick()
        assert path.read_text() == original
        routine = (await call(dispatcher, "routine.list")).routines[0]
        assert routine.failure_count == 10 and len(routine.notices) == 2
        clock.now = datetime(2026, 9, 6, 9, 1, tzinfo=UTC)
        await dispatcher.scheduler.tick()
        routine = (await call(dispatcher, "routine.list")).routines[0]
        assert routine.enabled and routine.failure_count == 0

    asyncio.run(scenario())


def test_final_text_head_excludes_text_before_a_tool_call(tmp_path):
    from kinby.contracts import ToolCall, ToolResult

    class ToolRunner(ScriptedRunner):
        async def run(self, turn, context):
            await context(MessageDelta(text="I'll fetch the news."))
            await context(ToolCall(call_id="fetch-1", name="fetch", arguments={}))
            await context(ToolResult(call_id="fetch-1", name="fetch", output="News", error=False))
            return await super().run(turn, context)

    async def scenario():
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 6, tzinfo=UTC)), ToolRunner())
        await call(dispatcher, "routine.run", name="news")
        await dispatcher.scheduler.drain()
        routine = (await call(dispatcher, "routine.list")).routines[0]
        assert routine.last_run.first_line == "News today"

    asyncio.run(scenario())


def test_worker_drain_returns_when_a_turn_parks(tmp_path):
    from tests.test_repl import ApprovalReplRunner

    async def scenario():
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News\nschedule: * * * * *")
        runner = ApprovalReplRunner()
        clock = FakeClock(datetime(2026, 9, 6, 9, tzinfo=UTC))
        dispatcher = runtime(instance, clock, runner)
        dispatcher.scheduler.start()
        await asyncio.sleep(0)
        clock.now = datetime(2026, 9, 6, 9, 1, tzinfo=UTC)
        dispatcher.scheduler.schedule()
        await runner.parked.wait()
        # Let the worker begin its next pass while the approval stays parked.
        dispatcher.scheduler.schedule()
        for _ in range(5):
            await asyncio.sleep(0)
        try:
            async with asyncio.timeout(0.2):
                await dispatcher.scheduler.drain()
        finally:
            await dispatcher.scheduler.stop()

    asyncio.run(scenario())


def test_duplicate_failure_is_counted_once_and_notices_reach_clients(tmp_path, capsys):
    from kinby.contracts import ErrorCode, TurnFailed
    from kinby.core.events import EventLog

    async def scenario():
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 6, tzinfo=UTC)), FailingRunner())
        accepted = await call(dispatcher, "routine.run", name="news")
        await dispatcher.scheduler.drain()
        await EventLog(instance.manifest.state_dir).append(
            accepted.thread_id,
            accepted.turn_id,
            TurnFailed(code=ErrorCode.INTERNAL, message="feed unavailable"),
        )
        await dispatcher.scheduler.drain()
        routine = (await call(dispatcher, "routine.list")).routines[0]
        assert routine.failure_count == 1 and len(routine.notices) == 1
        thread = await call(dispatcher, "thread.create")
        stdout = StringIO()
        await run_repl(
            ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope)),
            thread.id,
            feedback=FeedbackPolicy.OFF,
            stdin=StringIO("/routines\n"),
            stdout=stdout,
            stderr=StringIO(),
        )
        assert 'Routine "news" failed: feed unavailable' in stdout.getvalue()

    asyncio.run(scenario())
    assert main(["routine", "list", "--instance", str(tmp_path)]) == 0
    assert 'Routine "news" failed: feed unavailable' in capsys.readouterr().out


def test_notice_watcher_announces_only_new_failures(tmp_path):
    from contextlib import suppress

    from kinby.cli.routines import watch_routine_notices

    async def scenario():
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        runner = FailingRunner()
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 6, tzinfo=UTC)), runner)
        await call(dispatcher, "routine.run", name="news")
        await dispatcher.scheduler.drain()
        stdout = StringIO()
        watcher = asyncio.create_task(
            watch_routine_notices(
                ContractClient(dispatcher.dispatch, dispatcher.subscribe, set(Scope)), stdout
            )
        )
        try:
            await asyncio.sleep(0)
            assert stdout.getvalue() == ""
            runner.result = TurnOutcome()
            await call(dispatcher, "routine.run", name="news")
            await dispatcher.scheduler.drain()
            runner.result = CodeStepFailed("new failure")
            await call(dispatcher, "routine.run", name="news")
            await dispatcher.scheduler.drain()
            async with asyncio.timeout(3):
                while "new failure" not in stdout.getvalue():
                    await asyncio.sleep(0.01)
            assert "feed unavailable" not in stdout.getvalue()
            assert stdout.getvalue().count("new failure") == 1
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher

    asyncio.run(scenario())


@pytest.mark.parametrize("failures", [1, 10])
def test_success_after_restart_clears_unhandled_failure_actions(tmp_path, failures):
    async def scenario():
        instance = instance_at(tmp_path)
        path = routine_file(instance, "description: News\nenabled: true")
        original = path.read_text()
        clock = FakeClock(datetime(2026, 9, 6, tzinfo=UTC))
        dispatcher = runtime(instance, clock, FailingRunner())
        for _ in range(failures):
            accepted = await call(dispatcher, "routine.run", name="news")
            await events_for(dispatcher, accepted.thread_id)
        dispatcher = runtime(instance, clock)
        accepted = await call(dispatcher, "routine.run", name="news")
        await events_for(dispatcher, accepted.thread_id)
        await dispatcher.scheduler.drain()
        routine = (await call(dispatcher, "routine.list")).routines[0]
        assert routine.failure_count == 0
        assert routine.notices == []
        assert routine.enabled and path.read_text() == original
        dispatcher = runtime(instance, clock)
        await dispatcher.scheduler.tick()
        assert (await call(dispatcher, "routine.list")).routines[0].notices == []
        assert path.read_text() == original

    asyncio.run(scenario())


def test_scheduler_uses_timezone_validated_at_instance_load(tmp_path):
    async def scenario():
        instance = instance_at(tmp_path)
        clock = FakeClock(datetime(2026, 9, 6, 8, tzinfo=UTC))
        dispatcher = runtime(instance, clock)
        (tmp_path / "kinby.toml").write_text("[unfinished")
        routine_file(instance, "description: News\nschedule: 0 9 * * *")
        listed = await call(dispatcher, "routine.list")
        assert not isinstance(listed, ErrorEnvelope)
        assert listed.routines[0].next_run == datetime(2026, 9, 6, 9, tzinfo=UTC)
        await dispatcher.scheduler.tick()
        clock.now = datetime(2026, 9, 6, 9, tzinfo=UTC)
        await dispatcher.scheduler.tick()
        assert len((await call(dispatcher, "thread.list")).threads) == 1

    asyncio.run(scenario())


def test_cli_lists_latest_failure_between_notices(tmp_path, capsys):
    async def scenario():
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News")
        runner = FailingRunner()
        dispatcher = runtime(instance, FakeClock(datetime(2026, 9, 6, tzinfo=UTC)), runner)
        await call(dispatcher, "routine.run", name="news")
        await dispatcher.scheduler.drain()
        runner.result = CodeStepFailed("most recent failure")
        await call(dispatcher, "routine.run", name="news")
        await dispatcher.scheduler.drain()
        routine = (await call(dispatcher, "routine.list")).routines[0]
        assert routine.last_failure == "most recent failure"
        assert len(routine.notices) == 1

    asyncio.run(scenario())
    assert main(["routine", "list", "--instance", str(tmp_path)]) == 0
    assert "Last failure: most recent failure" in capsys.readouterr().out
