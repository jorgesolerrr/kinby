import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from kinby.contracts import (
    ConfigActor,
    ConfigChange,
    ConfigFile,
    Event,
    MemoryRecalled,
    MessageDelta,
    NodeId,
    Payload,
    TurnCompleted,
    TurnStarted,
)
from kinby.core.events import EventLog
from kinby.core.session_recalls import take_recalls
from kinby.core.threads import ThreadRecord, ThreadStore
from kinby.instance import init_instance, load_instance
from kinby.instance.config_changes import ConfigChangeLog

_SCRIPT = Path(__file__).parents[1] / "scripts" / "migrate_instance_database.py"
_AT = datetime(2026, 9, 1, 9, tzinfo=UTC)


def _migrate(*instances: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, _SCRIPT, *instances], capture_output=True, text=True, check=False
    )


def _turn(thread_id: UUID, first: int, *payloads: Payload) -> list[Event]:
    turn_id = uuid4()
    return [
        Event(
            sequence=sequence,
            thread_id=thread_id,
            turn_id=turn_id,
            payload=payload,
            timestamp=_AT + timedelta(seconds=sequence),
        )
        for sequence, payload in enumerate(payloads, start=first)
    ]


def _lines(*records: str) -> str:
    return "".join(f"{record}\n" for record in records)


def _turns(events: list[Event]) -> list[UUID]:
    return list(dict.fromkeys(event.turn_id for event in events))


def test_the_migration_moves_every_store_into_the_database_and_deletes_the_jsonl(
    tmp_path: Path,
) -> None:
    instance = load_instance(init_instance(tmp_path / "alice"))
    state_dir = instance.manifest.state_dir
    state_dir.mkdir(exist_ok=True)
    chat, scan = uuid4(), uuid4()
    started = TurnStarted(message="Hello", model="openai:gpt-5")
    closed = TurnCompleted(input_tokens=3, output_tokens=2)
    chat_events = [
        *_turn(chat, 1, started, MessageDelta(text="Hi"), closed),
        *_turn(chat, 4, started, closed),
    ]
    scan_events = _turn(scan, 1, started, closed)
    interleaved = [*chat_events[:2], *scan_events, *chat_events[2:]]
    (state_dir / "events.jsonl").write_text(
        _lines(*(event.model_dump_json() for event in interleaved)) + '{"sequence": 6, "thr',
        encoding="utf-8",
    )
    created = ThreadRecord(id=chat, title="Launch", created_at=_AT)
    renamed = created.model_copy(update={"title": "Launch notes"})
    archived = ThreadRecord(id=scan, title=None, created_at=_AT, archived=True)
    (state_dir / "threads.jsonl").write_text(
        _lines(
            created.model_dump_json(exclude={"archived"}),
            archived.model_dump_json(),
            renamed.model_dump_json(),
        ),
        encoding="utf-8",
    )
    changes = [
        ConfigChange(at=_AT, file=ConfigFile(name), actor=ConfigActor.APP, diff="")
        for name in ("SYSTEM.md", "RECAP.md")
    ]
    (state_dir / "config-changes.jsonl").write_text(
        _lines(*(change.model_dump_json() for change in changes)), encoding="utf-8"
    )
    run_id = uuid4()
    recall = MemoryRecalled(nodes=(NodeId("2026-09-01-deploys"),), step="code", attempt=1)
    (state_dir / "recalls").mkdir()
    (state_dir / "recalls" / f"{run_id}.jsonl").write_text(
        _lines(recall.model_dump_json()), encoding="utf-8"
    )

    migrated = _migrate(instance.path)

    assert migrated.returncode == 0, migrated.stderr
    event_log = EventLog(state_dir)
    for thread_id, before in ((chat, chat_events), (scan, scan_events)):
        after = event_log.stored(thread_id)
        assert len(after) == len(before)
        assert _turns(after) == _turns(before)
    assert list(event_log.all_events()) == interleaved
    assert ThreadStore(state_dir).threads() == [renamed, archived]
    assert ConfigChangeLog(state_dir).history(None, 10) == changes[::-1]
    assert take_recalls(state_dir, run_id) == [recall]
    assert not [path for path in state_dir.rglob("*") if path.suffix == ".jsonl"]
    assert not (state_dir / "recalls").exists()


def test_a_failed_check_keeps_the_jsonl_files_and_makes_no_database(tmp_path: Path) -> None:
    instance = load_instance(init_instance(tmp_path / "alice"))
    state_dir = instance.manifest.state_dir
    state_dir.mkdir(exist_ok=True)
    thread_id = uuid4()
    [first] = _turn(thread_id, 1, TurnStarted(message="Hi", model="openai:gpt-5"))
    # Two appends that took one sequence, as two processes appending at once could.
    second = first.model_copy(update={"turn_id": uuid4()})
    jsonl = _lines(first.model_dump_json(), second.model_dump_json())
    (state_dir / "events.jsonl").write_text(jsonl, encoding="utf-8")

    migrated = _migrate(instance.path)

    assert migrated.returncode == 1
    assert str(thread_id) in migrated.stdout
    assert (state_dir / "events.jsonl").read_text(encoding="utf-8") == jsonl
    assert not (state_dir / "instance.sqlite").exists()
