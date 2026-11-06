import asyncio
import json
import subprocess
import sys
from collections.abc import Mapping
from contextlib import closing
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessageChunk

from kinby.contracts import (
    ApprovalRequested,
    CompletionOutcome,
    ConfigActor,
    ConfigChange,
    ConfigFile,
    DeliveryId,
    Event,
    MemoryRecalled,
    MessageDelta,
    NodeId,
    Payload,
    RoutineName,
    RoutineOrigin,
    RoutineTrigger,
    SignalReceived,
    ToolCall,
    ToolResult,
    TurnCompleted,
    TurnStarted,
)
from kinby.core.events import EventLog
from kinby.core.session_recalls import take_recalls
from kinby.core.threads import ThreadRecord, ThreadStore
from kinby.core.turn_runner import LangGraphRunner
from kinby.instance import Instance, init_instance, load_instance
from kinby.instance.config_changes import ConfigChangeLog
from kinby.instance.database import connect
from tests.helpers import every_event
from tests.test_checkpoint_pruning import (
    _DONE,
    _calling,
    _checkpoints,
    _instance,
    _runtime,
    _stop,
)
from tests.test_checkpoint_pruning import _turn as _run_turn
from tests.test_plugins import ScriptedModel
from tests.test_scheduler import call

_SCRIPT = Path(__file__).parents[1] / "scripts" / "migrate_instance_database.py"
_AT = datetime(2026, 9, 1, 9, tzinfo=UTC)


def _migrate(*instances: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, _SCRIPT, *instances], capture_output=True, text=True, check=False
    )


def _turn(
    thread_id: UUID, first: int, *payloads: Payload, turn_id: UUID | None = None
) -> list[Event]:
    turn_id = turn_id or uuid4()
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
    # The append cut short stopped inside a character of two bytes.
    (state_dir / "events.jsonl").write_bytes(
        _lines(*(event.model_dump_json() for event in interleaved)).encode()
        + '{"payload": {"text": "café'.encode()[:-1]
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
        after = event_log.thread_events(thread_id)
        assert len(after) == len(before)
        assert _turns(after) == _turns(before)
    assert every_event(event_log) == interleaved
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


def test_a_migration_stopped_after_deleting_the_events_finishes_on_the_next_run(
    tmp_path: Path,
) -> None:
    instance = load_instance(init_instance(tmp_path / "alice"))
    state_dir = instance.manifest.state_dir
    state_dir.mkdir(exist_ok=True)
    thread_id = uuid4()
    events = _turn(thread_id, 1, TurnStarted(message="Hi", model="openai:gpt-5"))
    (state_dir / "events.jsonl").write_text(
        _lines(*(event.model_dump_json() for event in events)), encoding="utf-8"
    )
    thread = ThreadRecord(id=thread_id, title="Launch", created_at=_AT)
    threads = _lines(thread.model_dump_json())
    (state_dir / "threads.jsonl").write_text(threads, encoding="utf-8")
    assert _migrate(instance.path).returncode == 0
    # The first run deleted events.jsonl and stopped before threads.jsonl.
    (state_dir / "threads.jsonl").write_text(threads, encoding="utf-8")

    migrated = _migrate(instance.path)

    assert migrated.returncode == 0, migrated.stdout
    assert not (state_dir / "threads.jsonl").exists()
    assert EventLog(state_dir).thread_events(thread_id) == events
    assert ThreadStore(state_dir).threads() == [thread]


async def _parked_after_two_turns(
    instance: Instance, parking: AIMessageChunk
) -> tuple[UUID, Event]:
    """A thread with two finished turns and a third parked on the call *parking*."""
    dispatcher = _runtime(instance, ScriptedModel([_DONE, _DONE, parking]))
    created = await call(dispatcher, "thread.create")
    first = await _run_turn(dispatcher, created.id, "First", after=0)
    second = await _run_turn(dispatcher, created.id, "Second", after=first.sequence)
    return created.id, await _run_turn(dispatcher, created.id, "Third", after=second.sequence)


async def _parked_on_its_first_turn(
    instance: Instance, message: str, parking: AIMessageChunk
) -> tuple[UUID, Event]:
    """A thread whose first turn, started with *message*, parked on the call *parking*."""
    dispatcher = _runtime(instance, ScriptedModel([parking]))
    created = await call(dispatcher, "thread.create")
    return created.id, await _run_turn(dispatcher, created.id, message, after=0)


async def _keep_checkpoints(runner: LangGraphRunner, thread_id: UUID) -> None:
    """Stands in for pruning, so a thread holds every checkpoint as before #1193."""


def _old_arrival(thread_id: UUID, turn_id: UUID, delivery: Mapping[str, object]) -> str:
    """A delivery's arrival as it was recorded before ADR 0117, its body included."""
    return json.dumps(
        {
            "sequence": 1,
            "thread_id": str(thread_id),
            "turn_id": str(turn_id),
            "timestamp": _AT.isoformat(),
            "payload": {
                "type": "signal.received",
                "origin": {
                    "kind": "routine",
                    "name": "scan",
                    "trigger": "signal",
                    "delivery_id": delivery.get("delivery_id"),
                },
                "delivery": delivery,
            },
        }
    )


def test_the_migration_keeps_a_delivery_body_only_while_it_waits_and_tidies_checkpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = _instance(tmp_path)
    state_dir = instance.manifest.state_dir
    remember = _calling(
        "remember", {"description": "A fact", "subjects": ["facts"], "body": "Kept."}
    )
    monkeypatch.setattr(LangGraphRunner, "prune_checkpoints", _keep_checkpoints)
    parked_thread, parked = asyncio.run(_parked_after_two_turns(instance, remember))
    monkeypatch.undo()
    unpruned = _checkpoints(instance, parked_thread)
    assert isinstance(parked.payload, ApprovalRequested)
    approval_id = parked.payload.approval_id
    # A delivery turn's checkpoint kept its body as the turn's message before ADR 0117.
    delivered_body = '{"action":"opened","issue":{"number":8}}'
    delivered, delivered_park = asyncio.run(
        _parked_on_its_first_turn(instance, delivered_body, remember)
    )
    assert isinstance(delivered_park.payload, ApprovalRequested)
    delivered_approval = delivered_park.payload.approval_id
    delivered_arrival = _old_arrival(
        delivered,
        delivered_park.turn_id,
        {
            "headers": {},
            "content_type": "application/json",
            "body": delivered_body,
            "delivery_id": "delivery-8",
            "received_at": _AT.isoformat(),
        },
    )
    history: list[str] = []
    for event in every_event(EventLog(state_dir)):
        if event.thread_id == delivered:
            if event.sequence == 1:
                history.append(delivered_arrival)
            event = event.model_copy(update={"sequence": event.sequence + 1})
        history.append(event.model_dump_json())
    threads = [thread.model_dump_json() for thread in ThreadStore(state_dir).threads()]
    for leftover in ("", "-wal", "-shm"):
        Path(f"{state_dir / 'instance.sqlite'}{leftover}").unlink(missing_ok=True)

    scan, scan_turn, waiting, waiting_turn = uuid4(), uuid4(), uuid4(), uuid4()
    body = '{"action":"labeled","issue":{"number":7}}'
    delivery = {
        "headers": {"content-type": "application/json", "x-github-event": "issues"},
        "content_type": "application/json",
        "body": body,
        "delivery_id": "delivery-7",
        "received_at": _AT.isoformat(),
    }
    origin = RoutineOrigin(
        name=RoutineName("scan"),
        trigger=RoutineTrigger.SIGNAL,
        delivery_id=DeliveryId("delivery-7"),
    )
    scan_turn_events = _turn(
        scan,
        2,
        TurnStarted(message=body, model="openai:gpt-5", origin=origin),
        ToolCall(
            call_id="call-1",
            name="scan",
            arguments={"owner": "jorge", "signal": {**delivery, "body": json.loads(body)}},
        ),
        ToolResult(call_id="call-1", name="scan", output="None", error=False),
        TurnCompleted(input_tokens=0, output_tokens=0, outcome=CompletionOutcome.NO_WORK),
        turn_id=scan_turn,
    )
    waiting_delivery = {
        "headers": {},
        "content_type": "text/plain",
        "body": "issue 9 opened",
        "received_at": _AT.isoformat(),
    }
    (state_dir / "events.jsonl").write_text(
        _lines(
            *history,
            _old_arrival(scan, scan_turn, delivery),
            *(event.model_dump_json() for event in scan_turn_events),
            _old_arrival(waiting, waiting_turn, waiting_delivery),
        ),
        encoding="utf-8",
    )
    (state_dir / "threads.jsonl").write_text(
        _lines(
            *threads,
            *(
                ThreadRecord(id=thread_id, title="scan", created_at=_AT).model_dump_json()
                for thread_id in (scan, waiting)
            ),
        ),
        encoding="utf-8",
    )

    migrated = _migrate(instance.path)

    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    event_log = EventLog(state_dir)
    received, started, code_step, *_ = event_log.thread_events(scan)
    assert isinstance(received.payload, SignalReceived)
    arrival = received.payload.delivery
    assert (arrival.size, arrival.sha256) == (len(body), sha256(body.encode()).hexdigest())
    assert (arrival.headers, arrival.action) == ({"x-github-event": "issues"}, "labeled")
    assert isinstance(started.payload, TurnStarted)
    assert started.payload.message == "Delivery delivery-7"
    assert isinstance(code_step.payload, ToolCall)
    assert code_step.payload.arguments == {"owner": "jorge", "signal": "Delivery delivery-7"}
    assert event_log.waiting_body(scan_turn) is None
    [waited] = event_log.thread_events(waiting)
    assert isinstance(waited.payload, SignalReceived)
    assert event_log.waiting_body(waiting_turn) == "issue 9 opened"
    assert unpruned == 3
    assert _checkpoints(instance, parked_thread) == 2
    assert not [path for path in state_dir.rglob("*") if path.suffix == ".jsonl"]
    _, delivered_start, *_ = event_log.thread_events(delivered)
    assert isinstance(delivered_start.payload, TurnStarted)
    assert delivered_start.payload.message == "Delivery delivery-8"
    restored = asyncio.run(LangGraphRunner(instance).restore(delivered, delivered_park.turn_id))
    assert restored is not None
    assert restored.message == "Delivery delivery-8"

    async def resume(thread_id: UUID, approval: UUID, after: int) -> list[Event]:
        restarted = _runtime(instance, ScriptedModel([_DONE]))
        await call(
            restarted,
            "thread.approval.respond",
            thread_id=thread_id,
            approval_id=approval,
            decision="approve",
        )
        return await _stop(restarted, thread_id, after)

    resumed = asyncio.run(resume(parked_thread, approval_id, parked.sequence))
    assert isinstance(resumed[-1].payload, TurnCompleted)
    resumed = asyncio.run(resume(delivered, delivered_approval, delivered_park.sequence + 1))
    assert isinstance(resumed[-1].payload, TurnCompleted)


def test_a_database_from_before_adr_0117_drops_its_delivery_bodies_in_place(
    tmp_path: Path,
) -> None:
    instance = load_instance(init_instance(tmp_path / "alice"))
    state_dir = instance.manifest.state_dir
    state_dir.mkdir(exist_ok=True)
    scan, scan_turn, waiting, waiting_turn = uuid4(), uuid4(), uuid4(), uuid4()
    body = '{"action":"labeled","issue":{"number":7}}'
    delivery = {
        "headers": {"x-github-event": "issues"},
        "content_type": "application/json",
        "body": body,
        "delivery_id": "delivery-7",
        "received_at": _AT.isoformat(),
    }
    started = TurnStarted(message=body, model="openai:gpt-5")
    waiting_delivery = {
        "headers": {},
        "content_type": "text/plain",
        "body": "issue 9 opened",
        "received_at": _AT.isoformat(),
    }
    records = [
        json.loads(_old_arrival(scan, scan_turn, delivery)),
        *(event.model_dump(mode="json") for event in _turn(scan, 2, started, turn_id=scan_turn)),
        json.loads(_old_arrival(waiting, waiting_turn, waiting_delivery)),
    ]
    with closing(connect(state_dir / "instance.sqlite")) as db:
        db.executemany(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
            (
                (
                    record["thread_id"],
                    record["sequence"],
                    record["turn_id"],
                    record["payload"]["type"],
                    datetime.fromisoformat(record["timestamp"]).isoformat(timespec="microseconds"),
                    json.dumps(record["payload"]),
                )
                for record in records
            ),
        )

    migrated = _migrate(instance.path)

    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    event_log = EventLog(state_dir)
    received, turn_started = event_log.thread_events(scan)
    assert isinstance(received.payload, SignalReceived)
    assert received.payload.delivery.sha256 == sha256(body.encode()).hexdigest()
    assert isinstance(turn_started.payload, TurnStarted)
    assert turn_started.payload.message == "Delivery delivery-7"
    assert event_log.waiting_body(scan_turn) is None
    assert event_log.waiting_body(waiting_turn) == "issue 9 opened"
    assert _migrate(instance.path).stdout.strip().endswith("nothing to migrate")
