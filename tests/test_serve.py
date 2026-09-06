import asyncio
import os
import signal
from datetime import UTC, datetime, timedelta
from importlib import import_module
from threading import Event as ThreadEvent
from threading import Thread

from kinby.cli import main
from kinby.contracts import (
    MemoryRecapped,
    MessageDelta,
    RoutineOrigin,
    Scope,
    ThreadListResult,
    ToolCall,
    ToolResult,
    TurnCompleted,
    TurnInterrupted,
    TurnStarted,
    Warning,
)
from kinby.core import boot_instance
from kinby.core.dispatcher import TurnConfig
from kinby.core.events import EventLog
from kinby.core.turns import TurnOutcome
from kinby.memory import GraphStore, RecapWriter
from tests.helpers import fixed_permission_ceiling, fixed_turn_preparation
from tests.test_routines import instance_at, routine_file
from tests.test_run import BlockingInput
from tests.test_scheduler import FakeClock, ScriptedRunner


class EventRunner(ScriptedRunner):
    async def run(self, turn, context):
        await context(MessageDelta(text="Checking\n"))
        await context(ToolCall(call_id="fetch-1", name="fetch", arguments={"topic": "news"}))
        await context(
            ToolResult(call_id="fetch-1", name="fetch", output="Two stories", error=False)
        )
        await context(Warning(sources=("tools/fetch.py",), message="Using cached results."))
        await context(MessageDelta(text="Done"))
        return TurnOutcome()


class BlockingRoutineRunner(ScriptedRunner):
    def __init__(self) -> None:
        self.started = ThreadEvent()
        self.cancelled = ThreadEvent()
        self.runs = 0
        self._loop = None
        self._release = None

    async def run(self, turn, context):
        self.runs += 1
        self._loop = asyncio.get_running_loop()
        self._release = asyncio.Event()
        self.started.set()
        try:
            await self._release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return TurnOutcome()

    def release(self) -> None:
        assert self._loop is not None and self._release is not None
        self._loop.call_soon_threadsafe(self._release.set)


def test_boot_instance_starts_the_scheduler(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        instance = instance_at(tmp_path)
        routine_file(instance, "description: News\nschedule: * * * * *")
        clock = FakeClock(datetime(2026, 9, 6, 9, tzinfo=UTC))
        monkeypatch.setattr(
            "kinby.core.runtime.turn_config",
            lambda *args, **kwargs: TurnConfig(
                fixed_turn_preparation,
                fixed_permission_ceiling,
                ScriptedRunner(),
            ),
        )

        runtime = await boot_instance(instance, clock=clock)
        try:
            clock.now = datetime(2026, 9, 6, 9, 1, tzinfo=UTC)
            runtime.scheduler.schedule()
            async with asyncio.timeout(3):
                while True:
                    threads = await runtime.dispatcher.dispatch("thread.list", {}, set(Scope))
                    assert isinstance(threads, ThreadListResult)
                    if threads.threads:
                        break
                    await asyncio.sleep(0)
        finally:
            await runtime.stop_after_running_routine()

    asyncio.run(scenario())


def test_routine_list_prints_schedule_history_and_loader_warnings(tmp_path, capsys) -> None:
    instance = instance_at(tmp_path)
    news = routine_file(instance, "description: News\nschedule: 0 9 * * *")
    (news.parent / "run.py").write_text(
        "from kinby.plugins import tool\n"
        "@tool(write=False)\n"
        "def fetch() -> None:\n"
        '    """Fetch news."""\n'
        "    return None\n"
    )
    digest = tmp_path / "routines" / "digest" / "ROUTINE.md"
    digest.parent.mkdir(parents=True)
    digest.write_text(
        "---\ndescription: Digest\nschedule: 0 18 * * *\nenabled: false\n---\nDigest.\n"
    )
    broken = tmp_path / "routines" / "broken" / "ROUTINE.md"
    broken.parent.mkdir(parents=True)
    broken.write_text("---\nenabled: true\n---\nBroken.\n")

    assert main(["routine", "run", "news", "--instance", str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["routine", "list", "--instance", str(tmp_path)]) == 0

    output = capsys.readouterr().out
    lines = output.splitlines()
    assert lines[0] == "name\tschedule\tenabled\tlast run\tnext run"
    assert any(line.startswith("digest\t0 18 * * *\tdisabled\tnone\tnone") for line in lines)
    assert any(
        line.startswith("news\t0 9 * * *\tenabled\t")
        and " no-work " in line
        and not line.endswith("\tnone")
        for line in lines
    )
    assert "warning:" in output and "broken" in output and "description" in output


def test_routine_run_renders_the_turn_event_stream(tmp_path, capsys, monkeypatch) -> None:
    instance = instance_at(tmp_path)
    routine_file(instance, "description: News")
    monkeypatch.setattr(
        "kinby.core.runtime.turn_config",
        lambda *args, **kwargs: TurnConfig(
            fixed_turn_preparation,
            fixed_permission_ceiling,
            EventRunner(),
        ),
    )

    assert main(["routine", "run", "news", "--instance", str(tmp_path)]) == 0

    output = capsys.readouterr()
    assert "Checking\n" in output.out
    assert '[tool.call] fetch {"topic": "news"}\n' in output.out
    assert "[tool.result] fetch (ok): Two stories\n" in output.out
    assert output.out.endswith("Done\n")
    assert output.err == "[warning] tools/fetch.py: Using cached results.\n"


def test_routine_run_rejects_an_unknown_name(tmp_path, capsys) -> None:
    instance_at(tmp_path)

    assert main(["routine", "run", "missing", "--instance", str(tmp_path)]) == 1

    output = capsys.readouterr()
    assert output.out == ""
    assert 'NOT_FOUND: Routine "missing" was not found.' in output.err


def test_run_fires_a_due_routine_while_the_repl_waits(tmp_path, monkeypatch) -> None:
    instance = instance_at(tmp_path)
    routine = routine_file(instance, "description: News\nschedule: * * * * *")
    (routine.parent / "run.py").write_text(
        "from kinby.plugins import tool\n"
        "@tool(write=False)\n"
        "def fetch() -> None:\n"
        '    """Fetch news."""\n'
        "    return None\n"
    )
    clock = FakeClock(datetime(2026, 9, 6, 9, tzinfo=UTC))

    async def boot_with_clock(instance, *, model_override=None):
        runtime = await boot_instance(
            instance,
            model_override=model_override,
            clock=clock,
        )
        clock.now = datetime(2026, 9, 6, 9, 1, tzinfo=UTC)
        runtime.scheduler.schedule()
        return runtime

    monkeypatch.setattr(import_module("kinby.cli.main"), "boot_instance", boot_with_clock)
    stdin = BlockingInput()
    monkeypatch.setattr("sys.stdin", stdin)
    fired = ThreadEvent()

    def close_repl_after_fire() -> None:
        waiter = ThreadEvent()
        for _ in range(300):
            if any(
                isinstance(event.payload, TurnCompleted)
                for event in EventLog(tmp_path / ".state").all_events()
            ):
                fired.set()
                break
            waiter.wait(0.01)
        stdin.send("")

    closer = Thread(target=close_repl_after_fire, daemon=True)
    closer.start()
    exit_code = main(["run", "--instance", str(tmp_path)])
    closer.join(timeout=5)

    events = list(EventLog(tmp_path / ".state").all_events())
    started = [event.payload for event in events if isinstance(event.payload, TurnStarted)]
    assert exit_code == 0
    assert fired.is_set()
    assert not closer.is_alive()
    assert any(isinstance(event.origin, RoutineOrigin) for event in started)


def test_serve_interrupts_a_running_routine_and_drains_its_recap(
    tmp_path, capsys, monkeypatch
) -> None:
    instance = instance_at(tmp_path)
    with (tmp_path / "kinby.toml").open("a") as manifest:
        manifest.write('[memory]\nrecap = "off"\n')
    routine_file(instance, "description: News\nschedule: * * * * *")
    runner = BlockingRoutineRunner()
    clock = FakeClock(datetime.now(UTC) - timedelta(minutes=1))
    booted = ThreadEvent()

    def scripted_turns(instance, *, event_log, model_override=None):
        recap = RecapWriter(
            event_log,
            GraphStore(instance.path),
            instance,
            model_override=model_override,
        )
        return TurnConfig(
            fixed_turn_preparation,
            fixed_permission_ceiling,
            runner,
            recap,
        )

    async def boot_with_clock(instance, *, model_override=None):
        runtime = await boot_instance(
            instance,
            model_override=model_override,
            clock=clock,
        )
        clock.now = datetime.now(UTC)
        runtime.scheduler.schedule()
        booted.set()
        return runtime

    monkeypatch.setattr("kinby.core.runtime.turn_config", scripted_turns)
    monkeypatch.setattr(import_module("kinby.cli.main"), "boot_instance", boot_with_clock)

    def stop_process() -> None:
        if not runner.started.wait(3):
            return
        os.kill(os.getpid(), signal.SIGTERM)
        if not runner.cancelled.wait(1):
            runner.release()

    stopper = Thread(target=stop_process, daemon=True)
    stopper.start()
    exit_code = main(["serve", "--instance", str(tmp_path)])
    stopper.join(timeout=5)

    booted.clear()

    def stop_restarted_process() -> None:
        if booted.wait(3):
            os.kill(os.getpid(), signal.SIGTERM)

    restarted_stopper = Thread(target=stop_restarted_process, daemon=True)
    restarted_stopper.start()
    restarted_exit_code = main(["serve", "--instance", str(tmp_path)])
    restarted_stopper.join(timeout=5)

    output = capsys.readouterr()
    events = list(EventLog(tmp_path / ".state").all_events())
    assert exit_code == 0, (output.out, output.err)
    assert restarted_exit_code == 0
    assert not stopper.is_alive()
    assert not restarted_stopper.is_alive()
    assert "id: test" in output.out
    assert "name\tschedule\tenabled\tlast run\tnext run" in output.out
    assert runner.cancelled.is_set()
    assert runner.runs == 1
    assert any(isinstance(event.payload, TurnInterrupted) for event in events)
    assert any(isinstance(event.payload, MemoryRecapped) for event in events)
