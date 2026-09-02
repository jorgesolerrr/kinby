import asyncio
from io import StringIO
from pathlib import Path
from queue import Queue
from threading import Event as ThreadEvent
from threading import Thread
from uuid import uuid4

import pytest

from kinby.cli import main
from kinby.contracts import (
    MemoryRecapped,
    PermissionMode,
    ToolCall,
    TurnCompleted,
    TurnStarted,
)
from kinby.core.events import EventLog
from kinby.instance import RecapPolicy, init_instance, load_instance, reload_manifest


class BlockingInput(StringIO):
    def __init__(self) -> None:
        super().__init__()
        self._lines: Queue[str] = Queue()

    def readline(self, size: int = -1, /) -> str:
        return self._lines.get()

    def send(self, line: str) -> None:
        self._lines.put(line)


def test_manifest_defaults_to_an_every_turn_recap_policy(tmp_path: Path) -> None:
    instance_path = tmp_path / "alice"
    init_instance(instance_path)

    instance = load_instance(instance_path)

    assert instance.manifest.memory.recap is RecapPolicy.EVERY_TURN


def test_run_opens_a_repl_with_a_session_model_override_without_changing_the_manifest(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = tmp_path / "alice"
    instance.mkdir()
    manifest_path = instance / "kinby.toml"
    original_manifest = 'id = "alice"\n\n[models]\nmain = "openai:gpt-5"\n'
    manifest_path.write_text(original_manifest, encoding="utf-8")

    monkeypatch.setattr("sys.stdin", StringIO(""))

    exit_code = main(["run", str(instance), "--model", "anthropic:claude-sonnet-4-6"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "id: alice" in captured.out
    assert "main: anthropic:claude-sonnet-4-6" in captured.out
    assert captured.out.endswith("> ")
    assert captured.err == ""
    assert manifest_path.read_text(encoding="utf-8") == original_manifest


def test_run_catches_up_uncovered_turns_before_waiting_for_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = tmp_path / "alice"
    instance.mkdir()
    (instance / "kinby.toml").write_text(
        ('id = "alice"\n\n[models]\nmain = "openai:gpt-5"\n\n[memory]\nrecap = "off"\n'),
        encoding="utf-8",
    )
    event_log = EventLog(instance / ".state")
    thread_id = uuid4()
    turn_id = uuid4()

    async def seed_closed_turn() -> None:
        await event_log.append(
            thread_id,
            turn_id,
            TurnStarted(
                message="Run the tests",
                model="openai:gpt-5",
                permission_mode=PermissionMode.ASK,
            ),
        )
        await event_log.append(
            thread_id,
            turn_id,
            ToolCall(
                call_id="test-1",
                name="bash",
                arguments={"command": "uv run pytest"},
            ),
        )
        await event_log.append(
            thread_id,
            turn_id,
            TurnCompleted(input_tokens=4, output_tokens=2),
        )

    asyncio.run(seed_closed_turn())
    stdin = BlockingInput()
    monkeypatch.setattr("sys.stdin", stdin)
    exit_codes: Queue[int] = Queue()
    run = Thread(target=lambda: exit_codes.put(main(["run", str(instance)])))
    run.start()

    marker_seen = False
    try:
        waiter = ThreadEvent()
        for _ in range(100):
            marker_seen = any(
                event.turn_id == turn_id and isinstance(event.payload, MemoryRecapped)
                for event in event_log.all_events()
            )
            if marker_seen:
                break
            waiter.wait(0.01)
    finally:
        stdin.send("")
        run.join(timeout=1)

    assert marker_seen
    assert not run.is_alive()
    assert exit_codes.get_nowait() == 0


def test_reload_manifest_reads_changes_and_reapplies_the_session_model_override(
    tmp_path: Path,
) -> None:
    instance_path = tmp_path / "alice"
    instance_path.mkdir()
    manifest_path = instance_path / "kinby.toml"
    manifest_path.write_text(
        'id = "alice"\n\n[models]\nmain = "openai:gpt-5"\n',
        encoding="utf-8",
    )
    instance = load_instance(instance_path)
    manifest_path.write_text(
        ('id = "alice"\npersona_name = "Ada"\n\n[models]\nmain = "google:gemini-2.5-pro"\n'),
        encoding="utf-8",
    )

    manifest = reload_manifest(
        instance,
        model_override="anthropic:claude-sonnet-4-6",
    )

    assert manifest.persona_name == "Ada"
    assert manifest.models.main == "anthropic:claude-sonnet-4-6"
    assert manifest.models.recap == "anthropic:claude-sonnet-4-6"


def test_run_resumes_an_existing_thread_instead_of_creating_one(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = tmp_path / "alice"
    init_instance(instance)
    create_exit = main(["thread", "create", str(instance), "--title", "Launch notes"])
    created = capsys.readouterr()
    thread_id = created.out.splitlines()[0].removeprefix("id: ")

    monkeypatch.setattr("sys.stdin", StringIO(""))
    exit_code = main(["run", str(instance), "--thread", thread_id])
    run_output = capsys.readouterr()

    list_exit = main(["thread", "list", str(instance)])
    listed = capsys.readouterr()
    threads = listed.out.splitlines()

    assert create_exit == 0
    assert exit_code == 0
    assert list_exit == 0
    assert run_output.err == ""
    assert run_output.out.endswith("> ")
    assert len(threads) == 1
    assert thread_id in threads[0]


def test_run_rejects_an_unknown_thread(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = tmp_path / "alice"
    init_instance(instance)
    thread_id = uuid4()

    monkeypatch.setattr("sys.stdin", StringIO(""))
    exit_code = main(["run", str(instance), "--thread", str(thread_id)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert not captured.out.endswith("> ")
    assert f'NOT_FOUND: Thread "{thread_id}" was not found.' in captured.err


def test_run_rejects_a_malformed_thread_id(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = tmp_path / "alice"
    init_instance(instance)

    monkeypatch.setattr("sys.stdin", StringIO(""))
    exit_code = main(["run", str(instance), "--thread", "not-a-thread"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert not captured.out.endswith("> ")
    assert "--thread must be a thread id from kinby thread list" in captured.err


def test_run_creates_a_new_thread_when_one_already_exists(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = tmp_path / "alice"
    init_instance(instance)
    main(["thread", "create", str(instance), "--title", "Launch notes"])
    capsys.readouterr()

    monkeypatch.setattr("sys.stdin", StringIO(""))
    exit_code = main(["run", str(instance)])
    capsys.readouterr()
    list_exit = main(["thread", "list", str(instance)])
    listed = capsys.readouterr()

    assert exit_code == 0
    assert list_exit == 0
    assert len(listed.out.splitlines()) == 2
